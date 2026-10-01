"""Discord bot 本体。

  /zaiko code:<ASIN or JAN> [area:<地域名>] [stores:<id,id>]
  /stores                       … 登録チェーン一覧
  /addstore name url            … 書店を追加（url に {isbn13} などか、実際のISBNを含む検索URLをそのまま）
  /togglestore store_id         … 有効/無効
  メッセージに ASIN / JAN / Amazon URL（＋任意で地域）だけを貼っても自動で検索する
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv

from . import codes
from .lookup import fetch_meta, resolve_asin
from .render import build_messages
from .stores import check_all, known_ids, load_configs, save_configs
from .stores.base import StoreConfig

load_dotenv()
log = logging.getLogger("zaikobot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = os.environ.get("GUILD_ID")                          # 任意：即時反映させたいサーバーID
AUTO_CHANNELS = {c.strip() for c in os.environ.get("AUTO_REPLY_CHANNELS", "").split(",") if c.strip()}
PREFIX = os.environ.get("COMMAND_PREFIX", "!zaiko")
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


async def run_search(text: str, area: Optional[str] = None, only: Optional[str] = None):
    """コード判定 → ASIN解決 → 書誌 → 全店チェック。(messages, error_message) を返す。"""
    code = codes.parse(text)
    if not code.valid:
        return None, ("コードを認識できませんでした。ASIN（例 `4101010018` / `B0XXXXXXXX`）か "
                      "JAN/ISBN 13桁（例 `9784101010014`）、Amazon の URL を入れてください。")
    only_set = {s.strip() for s in only.split(",") if s.strip()} if only else None
    if only_set:
        unknown = only_set - known_ids()
        if unknown:
            return None, f"存在しないチェーンID: {', '.join(sorted(unknown))}（`/stores` で確認できます）"
    area = (area or "").strip() or None
    key = (code.isbn13 or code.jan or code.asin, area, tuple(sorted(only_set)) if only_set else None)
    cached = _cache_get(key)
    if cached:
        return cached, None

    async with _search_gate:
        started = time.perf_counter()
        async with aiohttp.ClientSession() as session:
            if code.kind == "ASIN" and not code.isbn13:
                await resolve_asin(session, code)
            meta = await fetch_meta(session, code)
        results = await check_all(code, area=area, only=only_set)
        messages = build_messages(code, meta, results, area, time.perf_counter() - started)
    _cache_put(key, messages)
    return messages, None


async def _send_all(first_send, rest_send, messages: list[list[discord.Embed]]) -> None:
    await first_send(embeds=messages[0])
    for embeds in messages[1:]:
        await rest_send(embeds=embeds)


@bot.tree.command(name="zaiko", description="ASIN / JAN / ISBN から本の店舗在庫を全書店まとめて検索")
@app_commands.describe(code="ASIN・JAN・ISBN・Amazon URL のどれか",
                       area="店名に含まれる地域で絞る（例: 新宿, 大阪, 札幌）",
                       stores="チェーンIDをカンマ区切りで指定（/stores で確認）")
async def zaiko(interaction: discord.Interaction, code: str, area: Optional[str] = None,
                stores: Optional[str] = None) -> None:
    await interaction.response.defer(thinking=True)
    try:
        messages, err = await run_search(code, area, stores)
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        await interaction.followup.send(f"エラーが発生しました: `{type(e).__name__}: {e}`")
        return
    if err:
        await interaction.followup.send(err)
    else:
        await _send_all(interaction.followup.send, interaction.followup.send, messages)


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
    sid = _make_id(name, url, {c.id for c in cfgs})
    branch = [s.strip() for s in re.split(r"[、,/／]", stores or "") if s.strip()]
    cfgs.append(StoreConfig(id=sid, name=name, search=url, checker="generic" if auto else "link",
                            enabled=True, verified=False, stores=branch, note="Discordから追加"))
    save_configs(cfgs)
    await interaction.response.send_message(f"追加しました: `{sid}` {name}\n{url}", ephemeral=True)


def _make_id(name: str, url: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "", name.lower().encode("ascii", "ignore").decode())
    if not base:
        host = (re.match(r"https?://([^/]+)", url) or [None, ""])[1]
        base = re.sub(r"^www\.|\.(co\.jp|com|jp|net)$", "", host).replace(".", "")
    base = base or "store"
    sid, i = base, 2
    while sid in taken:
        sid, i = f"{base}{i}", i + 1
    return sid


@bot.tree.command(name="togglestore", description="チェーンの有効/無効を切り替え")
@app_commands.describe(store_id="/stores に出る ID")
async def togglestore(interaction: discord.Interaction, store_id: str) -> None:
    cfgs = load_configs()
    for c in cfgs:
        if c.id == store_id:
            c.enabled = not c.enabled
            save_configs(cfgs)
            await interaction.response.send_message(
                f"`{c.id}` {c.name} を {'有効' if c.enabled else '無効'} にしました。", ephemeral=True)
            return
    await interaction.response.send_message(f"`{store_id}` は見つかりません。", ephemeral=True)


@bot.event
async def on_ready() -> None:
    log.info("logged in as %s (%s)", bot.user, bot.user and bot.user.id)


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
    area = " ".join(tokens[1:]) or None
    if area and len(area) > 10:
        return None
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
    if content.lower().startswith(PREFIX.lower()):
        parts = content[len(PREFIX):].strip().split()
        if not parts:
            await message.reply(f"使い方: `{PREFIX} <ASIN/JAN> [地域]`", mention_author=False)
            return
        code_text, area = parts[0], (" ".join(parts[1:]) or None)
    else:
        if AUTO_CHANNELS and str(message.channel.id) not in AUTO_CHANNELS:
            return
        parsed = parse_plain_message(content)
        if not parsed:
            return
        code_text, area = parsed

    try:
        async with message.channel.typing():
            messages, err = await run_search(code_text, area)
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        await message.reply(f"エラーが発生しました: `{type(e).__name__}: {e}`", mention_author=False)
        return
    if err:
        await message.reply(err, mention_author=False)
        return
    first = await message.reply(embeds=messages[0], mention_author=False)
    for embeds in messages[1:]:
        await message.channel.send(embeds=embeds, reference=first)


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
