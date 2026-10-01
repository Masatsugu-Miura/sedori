"""検索結果を Discord の Embed にまとめる。

Discord の制限: 1メッセージ 10 Embed まで、Embed 内の文字数合計は 1メッセージあたり 6000 まで、
field.value は 1024 まで。ここでは複数メッセージに分割して必ず制限内に収める。
"""
from __future__ import annotations

from typing import Optional

import discord

from .codes import Code
from .lookup import BookMeta
from .stores.base import CheckResult, Status

COLOR = 0xF2B134
MAX_ROWS = 8            # 1チェーンあたり表示する店舗行
FIELD_LIMIT = 1000
FIELDS_PER_EMBED = 10
MESSAGE_CHAR_LIMIT = 5800   # 6000 に対して余裕
LEGEND = "🟢在庫あり 🟡わずか 🔴なし ⚪要確認 🔗リンク ⚠️失敗"


def field_value(r: CheckResult, max_rows: int = MAX_ROWS) -> str:
    lines: list[str] = []
    if r.stocks:
        for s in r.stocks[:max_rows]:
            note = f"（{s.note}）" if s.note and s.note != s.status.text else ""
            lines.append(f"{s.status.emoji} {s.store}{note}")
        if len(r.stocks) > max_rows:
            lines.append(f"…他 {len(r.stocks) - max_rows} 店（リンク先で全件）")
    elif r.message:
        lines.append(r.message)
    if not r.verified:
        lines.append("※検索URL未検証（開けない場合は stores.json を修正）")
    if r.url:
        lines.append(f"[サイトで確認]({r.url})")
    v = "\n".join(lines)
    return v if len(v) <= FIELD_LIMIT else v[:FIELD_LIMIT - 1] + "…"


def _embed_len(e: discord.Embed) -> int:
    n = len(e.title or "") + len(e.description or "")
    if e.footer and e.footer.text:
        n += len(e.footer.text)
    for f in e.fields:
        n += len(f.name or "") + len(f.value or "")
    return n


def header_embed(code: Code, meta: BookMeta, results: list[CheckResult], area: Optional[str],
                 elapsed: float) -> discord.Embed:
    counts = {s: 0 for s in Status}
    for r in results:
        counts[r.status] += 1
    summary = "  ".join(f"{s.emoji} {counts[s]}" for s in
                        (Status.IN_STOCK, Status.LOW, Status.OUT, Status.UNKNOWN, Status.LINK, Status.ERROR))
    lines = [code.label()]
    if meta.author or meta.publisher:
        lines.append(" / ".join(x for x in (meta.author, meta.publisher) if x))
    if meta.price:
        lines.append(meta.price)
    if area:
        lines.append(f"エリア絞り込み: **{area}**")
    lines.append(summary)
    lines += [f"ℹ️ {n}" for n in code.notes]
    e = discord.Embed(title=(meta.title or "書誌情報なし")[:250], description="\n".join(lines)[:2000], color=COLOR)
    if meta.cover:
        e.set_thumbnail(url=meta.cover)
    e.set_footer(text=f"せどりDESK 在庫チェック • {len(results)}店 / {elapsed:.1f}s • {LEGEND}")
    return e


def build_messages(code: Code, meta: BookMeta, results: list[CheckResult], area: Optional[str],
                   elapsed: float) -> list[list[discord.Embed]]:
    """Embed を複数メッセージに分けて返す。各メッセージは 10 Embed / 約6000 文字以内。"""
    messages: list[list[discord.Embed]] = []
    cur_msg: list[discord.Embed] = []
    cur_len = 0
    cur = header_embed(code, meta, results, area, elapsed)

    def flush_embed() -> None:
        nonlocal cur, cur_len, cur_msg
        if cur is not None and (cur.fields or cur.title):
            cur_msg.append(cur)
            cur_len += _embed_len(cur)
        cur = discord.Embed(color=COLOR)

    def flush_message() -> None:
        nonlocal cur_msg, cur_len
        if cur_msg:
            messages.append(cur_msg)
        cur_msg, cur_len = [], 0

    for r in results:
        name = f"{r.status.emoji} {r.chain}"[:256]
        value = field_value(r) or "-"
        add = len(name) + len(value)
        if len(cur.fields) >= FIELDS_PER_EMBED:
            flush_embed()
        if cur_len + _embed_len(cur) + add > MESSAGE_CHAR_LIMIT or len(cur_msg) >= 10 - 1 and cur.fields:
            flush_embed()
            flush_message()
        cur.add_field(name=name, value=value, inline=False)
    flush_embed()
    flush_message()
    return messages
