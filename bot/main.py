"""Discord bot 本体。

  /zaiko code:<ASIN or JAN> [area:<全国|地域名>] [stores:<id,id>] … 既定は地元（愛知・京都）、area:全国 で全国
  /local code:<ASIN or JAN>                                      … 地元（stores.json の home_regions＝愛知・京都）
  /stores                       … 登録チェーン一覧
  /addstore name url            … 書店を追加（url に {isbn13} などか、実際のISBNを含む検索URLをそのまま）
  /togglestore store_id         … 有効/無効
  メッセージに ASIN / JAN / Amazon URL（＋任意で「全国」や地域名）だけを貼っても自動で検索する
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

from . import codes, watch
from .lookup import GRAPH_FILENAME, fetch_amazon, fetch_keepa_graph, fetch_meta, resolve_asin
from .render import build_messages
from .stores import (check_all, home_regions, known_ids, load_configs, load_settings, make_id,
                     manual_checks, region_groups, region_keywords, save_configs)
from .stores.base import PREFECTURES, StoreConfig

load_dotenv()
log = logging.getLogger("zaikobot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = os.environ.get("GUILD_ID")                          # 任意：即時反映させたいサーバーID
AUTO_CHANNELS = {c.strip() for c in os.environ.get("AUTO_REPLY_CHANNELS", "").split(",") if c.strip()}
PREFIX = os.environ.get("COMMAND_PREFIX", "!zaiko")
LOCAL_PREFIX = os.environ.get("LOCAL_PREFIX", "!local")
# 地域を指定しなかったときの既定範囲: "local"=地元（愛知・京都）, "all"=全国。
# 旧名 AUTO_REPLY_SCOPE も読む。
DEFAULT_SCOPE = os.environ.get("DEFAULT_SCOPE", os.environ.get("AUTO_REPLY_SCOPE", "local")).lower()
LOCAL_WORDS = {"地元", "local", "home", "ローカル"}
ALL_WORDS = {"全国", "ぜんこく", "all", "zenkoku", "全部"}
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "300"))   # 同じコードの再検索はこの秒数だけキャッシュ
MAX_PARALLEL_SEARCHES = int(os.environ.get("MAX_PARALLEL_SEARCHES", "2"))

intents = discord.Intents.default()
intents.message_content = True


class ZaikoBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self._warned_intent = False

    async def setup_hook(self) -> None:
        if GUILD_ID:
            g = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()
        log.info("slash commands synced")


bot = ZaikoBot()
_search_gate = asyncio.Semaphore(MAX_PARALLEL_SEARCHES)
_cache: dict[tuple, tuple[float, list[list[discord.Embed]]]] = {}


def _cache_get(key: tuple):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    _cache.pop(key, None)
    return None


def _cache_put(key: tuple, value) -> None:
    if len(_cache) > 200:
        oldest = min(_cache, key=lambda k: _cache[k][0])
        _cache.pop(oldest, None)
    _cache[key] = (time.monotonic(), value)


def _save_configs(cfgs: list[StoreConfig]) -> None:
    """stores.json を保存し、古い登録内容で作った検索結果キャッシュを捨てる。"""
    save_configs(cfgs)
    _cache.clear()


def known_area(word: Optional[str]) -> bool:
    """『地元』『全国』、stores.json の regions にある地域名、都道府県名のどれかか。
    コードの後ろに付いた「在庫ある？」のような言葉を地域と誤認しないために使う。"""
    w = (word or "").strip()
    if not w:
        return False
    if w in LOCAL_WORDS or w in ALL_WORDS:
        return True
    regions = load_settings()["regions"]
    names = [n for n in re.split(r"[、,/／\s+]+", w) if n]
    for n in names:
        if n in regions or (n.endswith(("県", "府", "都", "道")) and n[:-1] in regions):
            continue
        if any(n in (p, p[:-1]) for p in PREFECTURES):
            continue
        return False
    return bool(names)


def resolve_area(area: Optional[str]) -> tuple[Optional[str], str]:
    """入力の地域指定を (check_all に渡す area, 表示ラベル) にする。
    未指定なら DEFAULT_SCOPE（既定は地元＝愛知・京都）、『全国』なら全国。"""
    a = (area or "").strip()
    if not a:
        a = "地元" if DEFAULT_SCOPE == "local" else "全国"
    if a in ALL_WORDS:
        return None, "全国"
    if a in LOCAL_WORDS:
        regs = home_regions()
        if not regs:
            return None, "全国（home_regions 未設定）"
        return "地元", "地元（" + "・".join(regs) + "）"
    return a, a


async def run_search(text: str, area: Optional[str] = None, only: Optional[str] = None):
    """コード判定 → ASIN解決 → 書誌 → 全店チェック。(messages, error_message) を返す。"""
    code = codes.parse(text)
    if not code.valid:
        return None, ("コードを認識できませんでした。ASIN（例 `4101010013` / `B0XXXXXXXX`）か "
                      "JAN/ISBN 13桁（例 `9784101010014`）、Amazon の URL を入れてください。")
    only_set = {s.strip() for s in only.split(",") if s.strip()} if only else None
    if only_set:
        unknown = only_set - known_ids()
        if unknown:
            return None, f"存在しないチェーンID: {', '.join(sorted(unknown))}（`/stores` で確認できます）"
    area, scope_label = resolve_area(area)
    if area and not region_keywords(area):
        return None, f"地域「{area}」を解釈できませんでした。"
    key = (code.isbn13 or code.jan or code.asin, area, tuple(sorted(only_set)) if only_set else None)
    cached = _cache_get(key)
    if cached:
        return cached, None

    async with _search_gate:
        started = time.perf_counter()
        async with aiohttp.ClientSession(trust_env=True) as session:
            if code.kind == "ASIN" and not code.isbn13:
                await resolve_asin(session, code)
            # 書誌・波形・在庫チェックは独立なので並行に（書誌 API のタイムアウト待ちを在庫チェックに上乗せしない）
            meta, graph, amazon, results = await asyncio.gather(fetch_meta(session, code),
                                                                fetch_keepa_graph(session, code.asin),
                                                                fetch_amazon(session, code.asin),
                                                                check_all(code, area=area, only=only_set))
            meta.amazon = amazon
        serves = None
        if area:
            kws = region_keywords(area)
            serves = {c.id: c.serves(kws) for c in load_configs()}
        messages = build_messages(code, meta, results, area, time.perf_counter() - started, scope_label, serves,
                                  graph, region_groups(area), manual_checks(area))
    _cache_put(key, (messages, graph))
    return (messages, graph), None


def _graph_file(graph: Optional[bytes]) -> Optional[discord.File]:
    """Keepa の PNG を添付ファイルに（discord.File は 1 回しか送れないので送信のたびに作る）。"""
    return discord.File(io.BytesIO(graph), filename=GRAPH_FILENAME) if graph else None


async def _send_all(first_send, rest_send, messages: list[list[discord.Embed]],
                    graph: Optional[bytes] = None) -> None:
    f = _graph_file(graph)
    await (first_send(embeds=messages[0], file=f) if f else first_send(embeds=messages[0]))
    for embeds in messages[1:]:
        await rest_send(embeds=embeds)


async def _respond(interaction: discord.Interaction, code: str, area: Optional[str], stores: Optional[str]) -> None:
    await interaction.response.defer(thinking=True)
    try:
        found, err = await run_search(code, area, stores)
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        await interaction.followup.send(f"エラーが発生しました: `{type(e).__name__}: {e}`")
        return
    if err:
        await interaction.followup.send(err)
        return
    messages, graph = found
    try:
        await _send_all(interaction.followup.send, interaction.followup.send, messages, graph)
    except discord.HTTPException as e:
        log.exception("sending results failed")
        await interaction.followup.send(f"結果の送信に失敗しました: `{e}`")


@bot.tree.command(name="zaiko", description="書店の店舗在庫を検索（既定は地元＝愛知・京都。area:全国 で全国）")
@app_commands.describe(code="ASIN・JAN・ISBN・Amazon URL のどれか",
                       area="検索範囲: 全国 / 愛知 / 京都 / 地元（愛知＋京都、既定）/ 新宿 などの地名",
                       stores="チェーンIDをカンマ区切りで指定（/stores で確認）")
async def zaiko(interaction: discord.Interaction, code: str, area: Optional[str] = None,
                stores: Optional[str] = None) -> None:
    await _respond(interaction, code, area, stores)


@bot.tree.command(name="local", description="地元（愛知・京都）の店舗だけで本の在庫を検索")
@app_commands.describe(code="ASIN・JAN・ISBN・Amazon URL のどれか",
                       stores="チェーンIDをカンマ区切りで指定（/stores で確認）")
async def local(interaction: discord.Interaction, code: str, stores: Optional[str] = None) -> None:
    await _respond(interaction, code, "地元", stores)


@bot.tree.command(name="stores", description="登録されている書店チェーンの一覧")
async def stores_cmd(interaction: discord.Interaction) -> None:
    cfgs = load_configs()
    lines = []
    for c in cfgs:
        flag = "✅" if c.enabled else "⏸"
        ver = "" if c.verified else " ※URL未検証"
        filt = f"（店舗: {', '.join(c.stores)}）" if c.stores else ""
        lines.append(f"{flag} `{c.id}` {c.name}{ver}{filt}")
    text = f"**登録チェーン {len(cfgs)}件**（✅有効 ⏸無効）\n" + "\n".join(lines)
    await interaction.response.send_message(text[:1990], ephemeral=True)


@bot.tree.command(name="addstore", description="書店を追加（リンク表示、または検索ページの自動解析）")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(name="表示名（例: 〇〇書店）",
                       url="検索URL。{isbn13} などのプレースホルダ、または実際にISBNで検索したURLをそのまま貼る",
                       auto="検索ページを開いて在庫表記を自動で読む（デフォルト: リンクのみ）",
                       stores="対象店舗名をカンマ区切りで（自動解析時の絞り込み）")
async def addstore(interaction: discord.Interaction, name: str, url: str, auto: bool = False,
                   stores: Optional[str] = None) -> None:
    url = codes.templatize_url(url)
    if not re.match(r"https?://", url):
        await interaction.response.send_message("URL は https:// から書いてください。", ephemeral=True)
        return
    if not codes.has_placeholder(url):
        await interaction.response.send_message(
            "URL にコードの位置が見つかりません。`{isbn13}` を書くか、実際に ISBN で検索した結果の URL を貼ってください。",
            ephemeral=True)
        return
    cfgs = load_configs()
    sid = make_id(name, url, {c.id for c in cfgs})
    branch = [s.strip() for s in re.split(r"[、,/／]", stores or "") if s.strip()]
    cfgs.append(StoreConfig(id=sid, name=name, search=url, checker="generic" if auto else "link",
                            enabled=True, verified=False, stores=branch, note="Discordから追加"))
    _save_configs(cfgs)
    await interaction.response.send_message(f"追加しました: `{sid}` {name}\n{url}", ephemeral=True)


@bot.tree.command(name="togglestore", description="チェーンの有効/無効を切り替え")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(store_id="/stores に出る ID")
async def togglestore(interaction: discord.Interaction, store_id: str) -> None:
    cfgs = load_configs()
    for c in cfgs:
        if c.id == store_id:
            c.enabled = not c.enabled
            _save_configs(cfgs)
            await interaction.response.send_message(
                f"`{c.id}` {c.name} を {'有効' if c.enabled else '無効'} にしました。", ephemeral=True)
            return
    await interaction.response.send_message(f"`{store_id}` は見つかりません。", ephemeral=True)


WATCH_HOUR = int(os.environ.get("WATCH_HOUR", "12"))                 # 日販系の連携チェックを走らせる時刻（日本時間）
WATCH_ENABLED = os.environ.get("WATCH_ENABLED", "1") != "0"
NOTIFY_CHANNEL_ID = os.environ.get("NOTIFY_CHANNEL_ID", "").strip()  # 通知先チャンネル。無ければ DISCORD_WEBHOOK_URL
_watch_task: Optional[asyncio.Task] = None


async def _notify(text: str) -> None:
    """監視の通知を送る: NOTIFY_CHANNEL_ID のチャンネル → 無ければ Webhook。"""
    if NOTIFY_CHANNEL_ID:
        ch = bot.get_channel(int(NOTIFY_CHANNEL_ID)) or await bot.fetch_channel(int(NOTIFY_CHANNEL_ID))
        await ch.send(text)
        return
    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if url.startswith("https://discord.com/api/webhooks/"):
        async with aiohttp.ClientSession(trust_env=True) as session:
            await watch.post_webhook(session, url, text)
    else:
        log.warning("通知先が無いので送れません（NOTIFY_CHANNEL_ID か DISCORD_WEBHOOK_URL を設定）: %s", text[:80])


async def _daily_watch() -> None:
    """毎日 WATCH_HOUR 時に日販系の在庫連携をチェックし、初回と状態が変わったときに通知する。"""
    while True:
        try:
            st, msg = await watch.run_once(post=False)
            log.info("日販系連携チェック: %s", watch.summary(st))
            if msg:
                await _notify(msg)
        except Exception:  # noqa: BLE001
            log.exception("日販系連携チェックに失敗")
        await asyncio.sleep(watch.seconds_until(WATCH_HOUR))


@bot.event
async def on_ready() -> None:
    global _watch_task
    log.info("logged in as %s (%s)", bot.user, bot.user and bot.user.id)
    if WATCH_ENABLED and (_watch_task is None or _watch_task.done()):
        _watch_task = asyncio.create_task(_daily_watch())


def parse_plain_message(content: str) -> Optional[tuple[str, Optional[str]]]:
    """自動反応の対象なら (コード, 地域) を返す。雑談中の13桁数字などには反応しない。"""
    tokens = content.split()
    if not tokens or len(tokens) > 3:
        return None
    cand = codes.extract_candidate(tokens[0])
    if not cand:
        return None
    is_url = bool(re.search(r"amazon\.co\.jp", tokens[0], re.I))
    if not is_url and len(tokens[0]) > 20:
        return None
    # 電話番号（10桁）や日付（8桁）に反応しないよう、数字だけのコードはチェックディジットが合うものに限る
    if not is_url and not codes.has_valid_check_digit(cand):
        return None
    area = " ".join(tokens[1:]) or None
    if area and len(area) > 10:
        return None
    # 「9784101010014 在庫ある？」のように地域でない言葉が付いていたら、地域指定なし（既定の範囲）として扱う
    if area and not known_area(area):
        area = None
    return cand, area


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    content = message.content.strip()
    if not content:
        if not bot._warned_intent and message.guild is not None:
            bot._warned_intent = True
            log.warning("メッセージ本文が空です。Developer Portal で MESSAGE CONTENT INTENT が ON か確認してください。")
        return
    low = content.lower()
    if low.startswith(LOCAL_PREFIX.lower()):
        parts = content[len(LOCAL_PREFIX):].strip().split()
        if not parts:
            await message.reply(f"使い方: `{LOCAL_PREFIX} <ASIN/JAN>`（地元＝{'・'.join(home_regions()) or '未設定'}）",
                                mention_author=False)
            return
        code_text, area = parts[0], "地元"
    elif low.startswith(PREFIX.lower()):
        parts = content[len(PREFIX):].strip().split()
        if not parts:
            await message.reply(f"使い方: `{PREFIX} <ASIN/JAN> [全国|地域]`　例: `{PREFIX} 9784101010014 全国`"
                                f"（地域なしは地元＝{'・'.join(home_regions()) or '未設定'}）",
                                mention_author=False)
            return
        code_text, area = parts[0], (" ".join(parts[1:]) or None)
        if area and not known_area(area):
            area = None
    else:
        if AUTO_CHANNELS and str(message.channel.id) not in AUTO_CHANNELS:
            return
        parsed = parse_plain_message(content)
        if not parsed:
            return
        code_text, area = parsed

    try:
        async with message.channel.typing():
            found, err = await run_search(code_text, area)
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        await message.reply(f"エラーが発生しました: `{type(e).__name__}: {e}`", mention_author=False)
        return
    if err:
        await message.reply(err, mention_author=False)
        return
    messages, graph = found
    try:
        f = _graph_file(graph)
        first = await (message.reply(embeds=messages[0], file=f, mention_author=False) if f
                       else message.reply(embeds=messages[0], mention_author=False))
        for embeds in messages[1:]:
            await message.channel.send(embeds=embeds, reference=first)
    except discord.HTTPException as e:
        log.exception("sending results failed")
        await message.reply(f"結果の送信に失敗しました: `{e}`", mention_author=False)


def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN が設定されていません（.env を作成してください）")
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("Developer Portal → Bot → Privileged Gateway Intents で MESSAGE CONTENT INTENT を ON にしてください") from None
    except discord.LoginFailure:
        raise SystemExit("DISCORD_TOKEN が正しくありません") from None


if __name__ == "__main__":
    main()
