#!/usr/bin/env python3
"""日販系（ほんらぶ対応店）の在庫連携が復旧したか調べる。
  python scripts/nippan_watch.py            # 調べて画面に出すだけ
  python scripts/nippan_watch.py --post     # 初回と状態が変わったときに .env の DISCORD_WEBHOOK_URL へ投稿
  python scripts/nippan_watch.py --post --force   # 毎回投稿
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.watch import run_once  # noqa: E402

load_dotenv()


async def main() -> None:
    post, force = "--post" in sys.argv, "--force" in sys.argv
    st, msg = await run_once(post=post, force=force)
    print(f"{st.checked}  連携店 {st.total} / 応答 {st.ok}  → {'復旧' if st.recovered else 'エラー中'}")
    for n in st.names_ok[:5]:
        print("  OK ", n)
    for n in st.names_err[:5]:
        print("  ERR", n)
    print(("投稿: " + msg.replace("\n", " / ")) if (post and msg) else ("通知文: " + msg.replace("\n", " / ") if msg else "変化なし（通知しない）"))


if __name__ == "__main__":
    asyncio.run(main())
