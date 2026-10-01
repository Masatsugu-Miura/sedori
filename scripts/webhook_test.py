#!/usr/bin/env python3
"""Discord の Webhook に、実際の検索結果を 1 回だけ投稿するテスト用スクリプト。
bot のトークンやサーバー招待なしで、見た目と各書店の読み取り状況を確認できる。

使い方:
  .env に DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/... を書いてから
  python scripts/webhook_test.py 9784101010014            # 全国
  python scripts/webhook_test.py 9784101010014 地元       # 地元（愛知・京都）
  python scripts/webhook_test.py 9784101010014 京都
  python scripts/webhook_test.py 9784101010014 --dry      # 投稿せず結果を画面に出すだけ
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot import codes  # noqa: E402
from bot.lookup import fetch_meta, resolve_asin  # noqa: E402
from bot.render import build_messages  # noqa: E402
from bot.stores import check_all, load_configs, region_keywords  # noqa: E402

load_dotenv()


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    if not args:
        raise SystemExit(__doc__)
    code = codes.parse(args[0])
    if not code.valid:
        raise SystemExit("コードを認識できませんでした")
    area = args[1] if len(args) > 1 else None
    label = "全国"
    if area in ("地元", "local", "home"):
        area, label = "地元", "地元"
    elif area:
        label = area

    started = time.perf_counter()
    async with aiohttp.ClientSession(trust_env=True) as session:
        if code.kind == "ASIN" and not code.isbn13:
            await resolve_asin(session, code)
        meta = await fetch_meta(session, code)
    results = await check_all(code, area=area)
    serves = {c.id: c.serves(region_keywords(area)) for c in load_configs()} if area else None
    messages = build_messages(code, meta, results, area, time.perf_counter() - started, label, serves)

    print(f"== {meta.title or '(書誌なし)'}  {code.label()}")
    for r in results:
        print(f"{r.status.emoji} {r.chain:28} {r.status.text:6} {r.message}  {r.url}")
        for s in r.stocks[:8]:
            print(f"      {s.status.emoji} {s.store} ({s.note})")
    if dry:
        return

    url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url.startswith("https://discord.com/api/webhooks/"):
        raise SystemExit("DISCORD_WEBHOOK_URL が .env にありません（Discord のチャンネル設定 → 連携サービス → ウェブフック）")
    async with aiohttp.ClientSession(trust_env=True) as session:
        for embeds in messages:
            payload = {"username": "せどりDESK 在庫チェック", "embeds": [e.to_dict() for e in embeds]}
            async with session.post(url + "?wait=true", json=payload) as r:
                body = await r.text()
                if r.status >= 300:
                    raise SystemExit(f"Webhook 投稿失敗 HTTP {r.status}: {body[:300]}")
    print(f"Discord に {len(messages)} 件投稿しました")


if __name__ == "__main__":
    asyncio.run(main())
