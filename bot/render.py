"""検索結果を Discord の Embed にまとめる。"""
from __future__ import annotations

import discord

from .codes import Code
from .lookup import BookMeta
from .stores.base import CheckResult, Status

MAX_ROWS = 8          # 1チェーンあたり表示する店舗行
FIELD_LIMIT = 1000
FIELDS_PER_EMBED = 12


def _field_value(r: CheckResult) -> str:
    lines: list[str] = []
    if r.stocks:
        for s in r.stocks[:MAX_ROWS]:
            note = f"（{s.note}）" if s.note and s.note != s.status.text else ""
            lines.append(f"{s.status.emoji} {s.store}{note}")
        if len(r.stocks) > MAX_ROWS:
            lines.append(f"…他 {len(r.stocks) - MAX_ROWS} 店")
    elif r.message:
        lines.append(r.message)
    if not r.verified:
        lines.append("※検索URL未検証（開けない場合は stores.json を修正）")
    lines.append(f"[サイトで確認]({r.url})" if r.url else "")
    v = "\n".join(l for l in lines if l)
    return v[:FIELD_LIMIT - 1] + "…" if len(v) > FIELD_LIMIT else v


def build_embeds(code: Code, meta: BookMeta, results: list[CheckResult], area: str | None,
                 elapsed: float) -> list[discord.Embed]:
    counts = {s: 0 for s in Status}
    for r in results:
        counts[r.status] += 1
    summary = (f"🟢 {counts[Status.IN_STOCK]}  🟡 {counts[Status.LOW]}  🔴 {counts[Status.OUT]}  "
               f"⚪ {counts[Status.UNKNOWN]}  🔗 {counts[Status.LINK]}  ⚠️ {counts[Status.ERROR]}")

    title = meta.title or "書誌情報なし"
    desc_lines = [code.label()]
    if meta.author or meta.publisher:
        desc_lines.append(" / ".join(x for x in (meta.author, meta.publisher) if x))
    if meta.price:
        desc_lines.append(meta.price)
    if area:
        desc_lines.append(f"エリア絞り込み: **{area}**")
    desc_lines.append(summary)
    for n in code.notes:
        desc_lines.append(f"ℹ️ {n}")

    first = discord.Embed(title=title[:250], description="\n".join(desc_lines)[:4000], color=0xF2B134)
    if meta.cover:
        first.set_thumbnail(url=meta.cover)
    first.set_footer(text=f"せどりDESK 在庫チェック  •  {len(results)}店 / {elapsed:.1f}s  •  "
                          "🟢在庫あり 🟡わずか 🔴なし ⚪要確認 🔗リンク ⚠️失敗")

    embeds = [first]
    cur = first
    for r in results:
        if len(cur.fields) >= FIELDS_PER_EMBED:
            cur = discord.Embed(color=0xF2B134)
            embeds.append(cur)
        cur.add_field(name=f"{r.status.emoji} {r.chain}"[:256], value=_field_value(r) or "-", inline=False)
    return embeds[:10]
