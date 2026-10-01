"""日販系（ほんらぶ対応店：BOOKSえみたす など）の在庫連携が復旧したかを毎日チェックして Discord に知らせる。

書店在庫情報プロジェクト（openBS）では日販系の店は Shop_Nippan_* という系統で登録されているが、
現状はカーリル API が全店 Error を返す。復旧すれば bot の結果に自動で出るので、状態が変わったときだけ通知する。
  python scripts/nippan_watch.py            # いま調べて画面に出す
  python scripts/nippan_watch.py --post     # 状態が変わっていれば Webhook に投稿（--force で毎回投稿）
bot 本体は毎日 WATCH_HOUR 時（既定 12 時、日本時間）に同じチェックを走らせる。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import aiohttp

from .lookup import HEADERS
from .stores import openbs

JST = timezone(timedelta(hours=9))
PROBE_ISBN = "9784101010014"            # 吾輩は猫である（新潮文庫）: どの店でも扱う定番
ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = Path(os.environ.get("WATCH_STATE_FILE", ROOT / "data" / "nippan_state.json"))
TIMEOUT = aiohttp.ClientTimeout(total=20)
WATCH_REGIONS = ["愛知", "京都"]        # 代表点を取る地域（えみたすは愛知）


@dataclass
class LinkStatus:
    checked: str                        # ISO8601（JST）
    total: int                          # 日販系の連携店の数
    ok: int                             # 在庫情報が返ってきた店の数
    names_ok: list[str] = field(default_factory=list)
    names_err: list[str] = field(default_factory=list)

    @property
    def recovered(self) -> bool:
        return self.ok > 0


async def _get(session: aiohttp.ClientSession, url: str) -> tuple[int, str]:
    async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
        return r.status, await r.text()


async def check_nippan(session: aiohttp.ClientSession) -> LinkStatus:
    """愛知・京都の代表点から日販系の連携店を集め、カーリル API で在庫情報が返るか調べる。"""
    stores: dict[str, dict] = {}
    for pt in openbs.points_for(WATCH_REGIONS):
        try:
            status, text = await _get(session, openbs.recommend_url(*pt))
        except Exception:  # noqa: BLE001
            continue
        if status >= 400:
            continue
        for s in openbs.linked_stores(text):
            if s["systemid"].startswith("Shop_Nippan"):
                stores.setdefault(f"{s['systemid']}:{s['libkey']}", s)
    now = datetime.now(JST).isoformat(timespec="minutes")
    if not stores:
        return LinkStatus(now, 0, 0)
    systemids = sorted({s["systemid"] for s in stores.values()})
    status, text = await _get(session, openbs.check_url(PROBE_ISBN, systemids))
    try:
        data = json.loads(text) if status < 400 else {}
    except ValueError:
        data = {}
    polls = 0
    while isinstance(data, dict) and data.get("continue") == 1 and data.get("session") and polls < openbs.MAX_POLLS:
        polls += 1
        await asyncio.sleep(openbs.POLL_INTERVAL)
        status, text = await _get(session, openbs.poll_url(data["session"]))
        try:
            data = json.loads(text) if status < 400 else {}
        except ValueError:
            break
    stocks, failed = openbs.merge(list(stores.values()), text, PROBE_ISBN)
    ok_names = [s.store for s in stocks]
    err_names = [s["name"] for s in stores.values() if not any(s["name"] in n for n in ok_names)]
    return LinkStatus(now, len(stores), len(stocks), ok_names, err_names)


def load_state(path: Path = STATE_FILE) -> Optional[LinkStatus]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return LinkStatus(**{k: d[k] for k in ("checked", "total", "ok", "names_ok", "names_err")})
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_state(st: LinkStatus, path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summary(st: LinkStatus) -> str:
    if st.total == 0:
        return "日販系の連携店が一覧に見つかりませんでした（プロジェクト側の一覧が取れていません）"
    ex = "、".join(st.names_ok[:3]) if st.names_ok else "、".join(st.names_err[:3])
    return f"日販系（ほんらぶ対応店）{st.total}店中 {st.ok}店が応答（例: {ex}）"


def message_for(st: LinkStatus, prev: Optional[LinkStatus], force: bool = False) -> Optional[str]:
    """通知文。初回と状態が変わったときだけ返す（force なら毎回）。"""
    if prev is None:
        head = "🔎 日販系の在庫連携の監視を始めました。" + ("すでに復旧しています。" if st.recovered else "現在はまだエラー中です。")
        return f"{head}\n{summary(st)}\n毎日 12 時に確認して、変わったときに知らせます。"
    if st.recovered and not prev.recovered:
        return (f"✅ 日販系（ほんらぶ対応店：BOOKSえみたす など）の在庫連携が復旧しました！\n{summary(st)}\n"
                "これからは在庫検索の結果に自動で出ます。")
    if prev.recovered and not st.recovered:
        return f"⚠️ 日販系の在庫連携がまたエラーになりました。\n{summary(st)}"
    if force:
        return f"ℹ️ 日販系の在庫連携: {'復旧しています' if st.recovered else 'まだエラー中です'}。\n{summary(st)}"
    return None


async def post_webhook(session: aiohttp.ClientSession, url: str, content: str) -> None:
    async with session.post(url, json={"username": "せどりDESK 在庫チェック", "content": content[:1900]}) as r:
        if r.status >= 300:
            raise RuntimeError(f"Webhook HTTP {r.status}: {(await r.text())[:200]}")


async def run_once(post: bool = False, force: bool = False, webhook: Optional[str] = None) -> tuple[LinkStatus, Optional[str]]:
    """チェック → 状態保存 → (通知するなら) Webhook 投稿。戻りは (状態, 通知文 or None)。"""
    prev = load_state()
    async with aiohttp.ClientSession(trust_env=True) as session:
        st = await check_nippan(session)
        msg = message_for(st, prev, force)
        save_state(st)
        if post and msg:
            url = webhook or os.environ.get("DISCORD_WEBHOOK_URL", "")
            if url.startswith("https://discord.com/api/webhooks/"):
                await post_webhook(session, url, msg)
    return st, msg


def seconds_until(hour: int, now: Optional[datetime] = None) -> float:
    """次の hour 時（日本時間）までの秒数。"""
    now = now or datetime.now(JST)
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()
