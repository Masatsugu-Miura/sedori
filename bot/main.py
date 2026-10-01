"""Discord bot 本体。

  /zaiko code:<ASIN or JAN> [area:<地域名>] [stores:<id,id>]
  /stores                       … 登録チェーン一覧
  /addstore name url            … リンク店を追加（url に {isbn13} {isbn10} {jan} {asin} または %s）
  メッセージに ASIN / JAN / Amazon URL だけを貼っても自動で検索する
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
from .render import build_embeds
from .stores import check_all, load_configs, save_configs
from .stores.base import StoreConfig

load_dotenv()
log = logging.getLogger("zaikobot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TOKEN = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = os.environ.get("GUILD_ID")                          # 任意：即時反映させたいサーバーID
AUTO_CHANNELS = {c.strip() for c in os.environ.get("AUTO_REPLY_CHANNELS", "").split(",") if c.strip()}
PREFIX = os.environ.get("COMMAND_PREFIX", "!zaiko")

intents = discord.Intents.default()
intents.message_content = True


class ZaikoBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        if GUILD_ID:
            g = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()
        log.info("slash commands synced")


bot = ZaikoBot()


async def run_search(text: str, area: Optional[str] = None, only: Optional[str] = None):
    """コード判定 → ASIN解決 → 書誌 → 全店チェック。(embeds, error_message) を返す。"""
    code = codes.parse(text)
    if not code.valid:
        return None, ("コードを認識できませんでした。ASIN（例 `4088820001` / `B0XXXXXXXX`）か "
                      "JAN/ISBN13桁（例 `9784088820002`）を入れてください。")
    started = time.perf_counter()
    async with aiohttp.ClientSession() as session:
        if code.kind == "ASIN" and not code.isbn13:
            await resolve_asin(session, code)
        meta = await fetch_meta(session, code)
    only_set = {s.strip() for s in only.split(",") if s.strip()} if only else None
    results = await check_all(code, area=area or None, only=only_set)
    embeds = build_embeds(code, meta, results, area, time.perf_counter() - started)
    return embeds, None


@bot.tree.command(name="zaiko", description="ASIN / JAN / ISBN から本の店舗在庫を全書店まとめて検索")
@app_commands.describe(code="ASIN・JAN・ISBN・Amazon URL のどれか",
                       area="店名に含まれる地域で絞る（例: 新宿, 大阪, 札幌）",
                       stores="チェーンIDをカンマ区切りで指定（/stores で確認）")
async def zaiko(interaction: discord.Interaction, code: str, area: Optional[str] = None,
                stores: Optional[str] = None) -> None:
    await interaction.response.defer(thinking=True)
    try:
        embeds, err = await run_search(code, area, stores)
    except Exception as e:  # noqa: BLE001
        log.exception("search failed")
        await interaction.followup.send(f"エラーが発生しました: `{type(e).__name__}: {e}`")
        return
    if err:
        await interaction.followup.send(err)
    else:
        await interaction.followup.send(embeds=embeds)


@bot.tree.command(name="stores", description="登録されている書店チェーンの一覧")
async def stores_cmd(interaction: discord.Interaction) -> None:
    cfgs = load_configs()
    lines = []
    for c in cfgs:
        flag = "✅" if c.enabled else "⏸"
        ver = "" if c.verified else " ※URL未検証"
        filt = f"（店舗: {', '.join(c.stores)}）" if c.stores else ""
        lines.append(f"{flag} `{c.id}` {c.name}{ver}{filt}")
    text = "\n".join(lines)
    await interaction.response.send_message(f"**登録チェーン {len(cfgs)}件**\n{text}"[:1990], ephemeral=True)


@bot.tree.command(name="addstore", description="書店を追加（リンク表示、または検索ページの自動解析）")
@app_commands.describe(name="表示名（例: 〇〇書店）",
                       url="検索URL。コード位置に {isbn13} {isbn10} {jan} {asin} か %s を書く",
                       auto="検索ページを開いて在庫表記を自動で読む（デフォルト: リンクのみ）")
async def addstore(interaction: discord.Interaction, name: str, url: str, auto: bool = False) -> None:
    if not re.search(r"\{(isbn13|isbn10|jan|asin|code)\}|%s", url):
        await interaction.response.send_message("URL に `{isbn13}` などのプレースホルダか `%s` が必要です。", ephemeral=True)
        return
    cfgs = load_configs()
    sid = (re.sub(r"[^a-z0-9]+", "", name.lower().encode("ascii", "ignore").decode())
           or re.sub(r"^www\.|\.(co\.jp|com|jp|net)$", "", (re.match(r"https?://([^/]+)", url) or [None, ""])[1]).replace(".", "")
           or f"store{len(cfgs) + 1}")
    base, i = sid, 2
    while any(c.id == sid for c in cfgs):
        sid, i = f"{base}{i}", i + 1
    cfgs.append(StoreConfig(id=sid, name=name, search=url, checker="generic" if auto else "link",
                            enabled=True, verified=False, note="Discordから追加"))
    save_configs(cfgs)
    await interaction.response.send_message(f"追加しました: `{sid}` {name}\n{url}", ephemeral=True)


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


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    content = message.content.strip()
    if not content:
        return
    area = None
    if content.lower().startswith(PREFIX.lower()):
        rest = content[len(PREFIX):].strip()
        parts = rest.split()
        if not parts:
            await message.reply(f"使い方: `{PREFIX} <ASIN/JAN> [地域]`")
            return
        content, area = parts[0], (parts[1] if len(parts) > 1 else None)
    else:
        if AUTO_CHANNELS and str(message.channel.id) not in AUTO_CHANNELS:
            return
        cand = codes.extract_candidate(content)
        # 本文がコードそのもの / Amazon URL のときだけ自動反応（雑談の中の13桁数字には反応しない）
        if not cand or (not re.search(r"amazon\.co\.jp", content, re.I) and len(content) > 40):
            return
        content = cand

    async with message.channel.typing():
        try:
            embeds, err = await run_search(content, area)
        except Exception as e:  # noqa: BLE001
            log.exception("search failed")
            await message.reply(f"エラーが発生しました: `{type(e).__name__}: {e}`")
            return
    await message.reply(err if err else None, embeds=embeds or [], mention_author=False)


def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN が設定されていません（.env を作成してください）")
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
