"""検索結果を Discord の Embed にまとめる。

Discord の制限: 1メッセージ 10 Embed まで、Embed 内の文字数合計は 1メッセージあたり 6000 まで、
field.value は 1024 まで。ここでは複数メッセージに分割して必ず制限内に収める。
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Optional

import discord

from .codes import Code
from .lookup import GRAPH_FILENAME, BookMeta
from .stores.base import CheckResult, Status, StoreStock, assign_region

COLOR = 0xF2B134
MAX_ROWS = 10           # 1チェーンあたり表示する店舗行（在庫あり・わずか の店だけ）
FIELD_LIMIT = 1000
FIELDS_PER_EMBED = 10
MESSAGE_CHAR_LIMIT = 5800   # 6000 に対して余裕
REST_FIELD_NAME = "この地域に該当店舗なし／地域外のチェーン"
OTHER_REGION = "その他"           # 地域分けのとき、どの地域にも振り分けられなかった店
LINKS_SECTION = "🔗 リンク・要確認"  # 地域分けのとき、店舗行の無いチェーン（リンクのみ／要確認／失敗）をまとめる区画
LEGEND = "🟢在庫あり 🟡わずか 🔴なし ⚪要確認 🔗リンク ⚠️失敗"
SHOWN = (Status.IN_STOCK, Status.LOW)   # 店舗行として出す状態（在庫なしの店は件数だけ）

_COUNT = re.compile(r"(\d+)\s*[点冊個]|(?:残り|在庫数)[:：]?\s*(\d+)")
_LABEL = re.compile(r"（([^（）]+)）\s*$")


def store_label(s: StoreStock) -> str:
    """店名の表記をチェーン間で揃える: 『店名（県・市）』＋在庫数が分かれば『 ×N』。
    『（愛知県）』は『（愛知）』に、注記の生テキスト（○ / あり / 在庫あり 6点 …）は出さない。"""
    name = s.store
    m = _LABEL.search(name)
    if m:
        parts = m.group(1).split("・")
        if len(parts[0]) > 2 and parts[0].endswith(("県", "府", "都")):
            parts[0] = parts[0][:-1]
        name = name[:m.start()] + "（" + "・".join(parts) + "）"
    c = _COUNT.search(s.note or "")
    n = next((g for g in c.groups() if g), None) if c else None
    return f"{name} ×{n}" if n else name


def stock_summary(stocks: list[StoreStock]) -> str:
    counts = {st: sum(1 for s in stocks if s.status == st) for st in Status}
    return (f"🟢{counts[Status.IN_STOCK]} 🟡{counts[Status.LOW]} 🔴{counts[Status.OUT]}"
            f"（確認 {len(stocks)}店）")


def field_value(r: CheckResult, max_rows: int = MAX_ROWS) -> str:
    """どのチェーンも同じ並び: 1行目に件数、在庫あり→わずか の店だけ行で、残りは件数、最後にリンク。"""
    lines: list[str] = []
    if r.stocks:
        lines.append(stock_summary(r.stocks))
        shown = [s for s in r.stocks if s.status in SHOWN]
        shown.sort(key=lambda s: s.status.rank)      # 🟢 → 🟡（同じ状態の中は元の順）
        for s in shown[:max_rows]:
            lines.append(f"{s.status.emoji} {store_label(s)}")
        if len(shown) > max_rows:
            lines.append(f"…他 {len(shown) - max_rows}店はリンク先で")
        if not shown:
            lines.append("在庫あり店舗なし")
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


def split_by_region(results: list[CheckResult], groups: dict[str, list[str]]) -> dict[str, list[CheckResult]]:
    """店舗行のあるチェーンを地域ごとに分ける。各地域にはその地域の店だけを持つ CheckResult のコピーを入れる。"""
    out: dict[str, list[CheckResult]] = {name: [] for name in groups}
    out[OTHER_REGION] = []
    for r in results:
        if not r.stocks:
            continue
        buckets: dict[str, list[StoreStock]] = {}
        for s in r.stocks:
            buckets.setdefault(assign_region(s.store, groups) or OTHER_REGION, []).append(s)
        for name, stocks in buckets.items():
            rr = replace(r, stocks=stocks, message="")
            rr.summarize()
            out[name].append(rr)
    for name in out:
        out[name].sort(key=lambda r: (r.status.rank, r.chain))
    return {name: rs for name, rs in out.items() if rs}


def header_embed(code: Code, meta: BookMeta, results: list[CheckResult], area: Optional[str],
                 elapsed: float, scope_label: str = "全国", graph: Optional[bytes] = None,
                 groups: Optional[dict[str, list[str]]] = None) -> discord.Embed:
    counts = {s: 0 for s in Status}
    for r in results:
        counts[r.status] += 1
    chains = "  ".join(f"{s.emoji} {counts[s]}" for s in
                       (Status.IN_STOCK, Status.LOW, Status.OUT, Status.UNKNOWN, Status.LINK, Status.ERROR))
    stores = [s for r in results for s in r.stocks]
    n_in = sum(1 for s in stores if s.status == Status.IN_STOCK)
    n_low = sum(1 for s in stores if s.status == Status.LOW)
    lines = [code.label()]
    if meta.author or meta.publisher:
        lines.append(" / ".join(x for x in (meta.author, meta.publisher) if x))
    if meta.price:
        lines.append(meta.price)
    lines.append(f"検索範囲: **{scope_label}**" + (f"（店名に {area} 系の地名を含む店舗）" if area else ""))
    lines.append(f"**在庫あり店舗 {n_in + n_low}店**（🟢{n_in} 🟡{n_low}）／ 確認 {len(stores)}店")
    if groups:
        # 地域ごとの内訳（愛知 🟢3 🟡12 ／ 京都 🟢1 🟡2）
        parts = []
        for name, rs in split_by_region(results, groups).items():
            ss = [s for r in rs for s in r.stocks]
            parts.append(f"{name} 🟢{sum(1 for s in ss if s.status == Status.IN_STOCK)}"
                         f" 🟡{sum(1 for s in ss if s.status == Status.LOW)}")
        if parts:
            lines.append(" ／ ".join(parts))
    lines.append(f"チェーン: {chains}")
    lines += [f"ℹ️ {n}" for n in code.notes]
    e = discord.Embed(title=(meta.title or "書誌情報なし")[:250], description="\n".join(lines)[:2000], color=COLOR)
    if meta.cover:
        e.set_thumbnail(url=meta.cover)
    if graph:
        # 波形（Keepa の価格・ランキング推移）。PNG は送信時に同名の添付ファイルとして付ける
        e.set_image(url=f"attachment://{GRAPH_FILENAME}")
    e.set_footer(text=f"せどりDESK 在庫チェック • {len(results)}チェーン / {elapsed:.1f}s • {LEGEND}")
    return e


def compact_line(r: CheckResult) -> str:
    msg = f" {r.message}" if r.message and not r.stocks else ""
    link = f"[{r.chain}]({r.url})" if r.url else r.chain
    return f"{r.status.emoji} {link}{msg}"[:200]


def split_for_region(results: list[CheckResult], serves: dict[str, bool]) -> tuple[list[CheckResult], list[CheckResult]]:
    """地域モード: 該当店舗の行があるチェーン＋その地域に出店しているリンク店を『主』、残りを『その他』に。"""
    main: list[CheckResult] = []
    rest: list[CheckResult] = []
    for r in results:
        in_region = serves.get(r.chain_id, True)
        if r.stocks or (in_region and r.status in (Status.LINK, Status.UNKNOWN, Status.ERROR)):
            main.append(r)
        else:
            rest.append(r)
    return main, rest


def build_messages(code: Code, meta: BookMeta, results: list[CheckResult], area: Optional[str],
                   elapsed: float, scope_label: str = "全国",
                   serves: Optional[dict[str, bool]] = None,
                   graph: Optional[bytes] = None,
                   groups: Optional[dict[str, list[str]]] = None) -> list[list[discord.Embed]]:
    """Embed を複数メッセージに分けて返す。各メッセージは 10 Embed / 約6000 文字以内。
    area 指定時（地域モード）は該当のあるチェーンだけを個別表示し、残りは1つのフィールドにまとめる。
    groups（地元＝愛知・京都のように複数地域）があれば『📍 愛知』『📍 京都』の区画に分けて、
    各チェーンの店をそれぞれの地域側に振り分ける（店が多くても埋もれないように）。
    graph（Keepa の PNG）があれば先頭 Embed の画像にし、送る側は最初のメッセージにその PNG を添付する。"""
    messages: list[list[discord.Embed]] = []
    cur_msg: list[discord.Embed] = []
    cur_len = 0
    cur = header_embed(code, meta, results, area, elapsed, scope_label, graph, groups if area else None)
    rest: list[CheckResult] = []
    if area:
        results, rest = split_for_region(results, serves or {})

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

    def add_field(name: str, value: str) -> None:
        """1 フィールド追加。Embed あたりのフィールド数・メッセージあたりの Embed 数／文字数を超えるなら先に区切る。"""
        if len(cur.fields) >= FIELDS_PER_EMBED:
            flush_embed()
        if cur_len + _embed_len(cur) + len(name) + len(value) > MESSAGE_CHAR_LIMIT or len(cur_msg) >= 10 - 1 and cur.fields:
            flush_embed()
            flush_message()
        cur.add_field(name=name, value=value, inline=False)

    def start_section(title: str) -> None:
        """『📍 愛知』のような見出し付きの Embed を新しく始める。"""
        flush_embed()
        cur.title = title

    if area and groups and len(groups) >= 2:
        for name, rs in split_by_region(results, groups).items():
            start_section(f"📍 {name}")
            for r in rs:
                add_field(f"{r.status.emoji} {r.chain}"[:256], field_value(r) or "-")
        others = [r for r in results if not r.stocks]
        if others:
            start_section(LINKS_SECTION)
            for r in others:
                add_field(f"{r.status.emoji} {r.chain}"[:256], field_value(r) or "-")
    else:
        for r in results:
            add_field(f"{r.status.emoji} {r.chain}"[:256], field_value(r) or "-")
    if rest:
        chunk: list[str] = []
        for ln in (compact_line(r) for r in rest):
            if chunk and sum(len(x) + 1 for x in chunk) + len(ln) > FIELD_LIMIT:
                add_field(REST_FIELD_NAME, "\n".join(chunk))
                chunk = []
            chunk.append(ln)
        if chunk:
            add_field(REST_FIELD_NAME, "\n".join(chunk))
    flush_embed()
    flush_message()
    return messages
