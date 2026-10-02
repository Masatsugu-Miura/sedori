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
from .stores.base import CheckResult, Status, StoreConfig, StoreStock, assign_region

COLOR = 0xF2B134
FIELD_LIMIT = 1000
DESC_LIMIT = 4000       # Embed の description 上限 4096 に対して余裕
FIELDS_PER_EMBED = 10
MESSAGE_CHAR_LIMIT = 5800   # 6000 に対して余裕
# Discord は文字数とは別に、Embed 本文の UTF-8 バイト数が約 10KB を超えると 413 (Request entity too large) を返す
# （実測: 『あ』3381 文字＝10143 バイトまで）。日本語は 1 文字 3 バイト、絵文字は 4 バイトなので、こちらの方が先に当たる
MESSAGE_BYTE_LIMIT = 9200
REST_FIELD_NAME = "この地域に該当店舗なし／地域外のチェーン"
OTHER_REGION = "その他"           # 地域分けのとき、どの地域にも振り分けられなかった店
LINKS_SECTION = "🔗 リンク・要確認"  # 地域分けのとき、店舗行の無いチェーン（リンクのみ／要確認／失敗）をまとめる区画
MANUAL_SECTION = "📱 自動検索できない店（アプリ・電話で確認）"
# check_by の値 → 見出し。この順に並べる
MANUAL_METHODS = [("ほんらぶ", "📱 ほんらぶ（日販のアプリ）"),
                  ("本コレ", "📱 本コレ（TSUTAYA / CCC のアプリ）"),
                  ("Honya Club", "📱 Honya Club アプリ（受取店舗に設定すると在庫が見える）"),
                  ("電話", "📞 電話で確認")]


def manual_fields(manual: list[StoreConfig]) -> list[tuple[str, list[str]]]:
    """アプリ・電話で調べる店を方法ごとに (見出し, 行リスト) にする。"""
    out: list[tuple[str, list[str]]] = []
    for key, label in MANUAL_METHODS:
        lines = []
        for c in manual:
            if key not in c.check_by:
                continue
            line = c.name
            if key == "電話" and c.phone:
                line += f" `{c.phone}`"
            if c.hint:
                line += f"（{c.hint}）"
            lines.append(line)
        if lines:
            out.append((label, lines))
    return out
LEGEND = "多い=在庫あり 少ない=在庫わずか ⚪要確認 🔗リンク ⚠️失敗"
CONT_FIELD_NAME = "└ 続き"
SHOWN = (Status.IN_STOCK, Status.LOW)   # 店舗行として出す状態（在庫なしの店は件数だけ）

_COUNT = re.compile(r"(\d+)\s*[点冊個]|(?:残り|在庫数)[:：]?\s*(\d+)")
_LABEL = re.compile(r"（([^（）]+)）\s*$")
WORD = {Status.IN_STOCK: "多い", Status.LOW: "少ない", Status.OUT: "なし", Status.UNKNOWN: "要確認"}
SEP = " ─ "


def store_name(s: StoreStock, strip_label: bool = False) -> str:
    """店名の表記を揃える。strip_label なら『（愛知・名古屋市）』の地域ラベルを落とす（地域ごとの区画では自明なので）。
    残すときは『（愛知県）』を『（愛知）』に。"""
    name = s.store
    m = _LABEL.search(name)
    if not m:
        return name
    if strip_label:
        return name[:m.start()].rstrip()
    parts = m.group(1).split("・")
    if len(parts[0]) > 2 and parts[0].endswith(("県", "府", "都")):
        parts[0] = parts[0][:-1]
    return name[:m.start()] + "（" + "・".join(parts) + "）"


def stock_word(s: StoreStock) -> str:
    """在庫の言い方: 数が分かれば『2個』、分からなければ 多い / 少ない / なし。"""
    c = _COUNT.search(s.note or "")
    n = next((g for g in c.groups() if g), None) if c else None
    if n and s.status in SHOWN:
        return f"{int(n)}個"
    return WORD.get(s.status, "要確認")


def row_text(s: StoreStock, strip_label: bool = False) -> str:
    return f"{store_name(s, strip_label)}{SEP}{stock_word(s)}"


def stock_summary(stocks: list[StoreStock]) -> str:
    counts = {st: sum(1 for s in stocks if s.status == st) for st in Status}
    return (f"多い {counts[Status.IN_STOCK]}店 / 少ない {counts[Status.LOW]}店 / なし {counts[Status.OUT]}店"
            f"（確認 {len(stocks)}店）")


def _chunk(lines: list[str], limit: int = FIELD_LIMIT) -> list[str]:
    """行を 1 フィールドの上限内で区切る。1 行が長すぎれば切り詰める。"""
    chunks: list[list[str]] = [[]]
    for ln in lines:
        while ulen(ln) > limit:
            ln = ln[:-(ulen(ln) - limit + 1)] + "…"
        if chunks[-1] and sum(ulen(x) + 1 for x in chunks[-1]) + ulen(ln) > limit:
            chunks.append([])
        chunks[-1].append(ln)
    return ["\n".join(c) for c in chunks if c]


def field_values(r: CheckResult, strip_label: bool = False) -> list[str]:
    """どのチェーンも同じ並び: 1行目に件数、在庫あり→少ない の店を全部（省略しない）、最後にリンク。
    1 フィールドに収まらなければ複数の値に分ける（送る側は『└ 続き』の名前で続ける）。"""
    lines: list[str] = []
    if r.stocks:
        lines.append(stock_summary(r.stocks))
        shown = [s for s in r.stocks if s.status in SHOWN]
        shown.sort(key=lambda s: s.status.rank)      # 多い → 少ない（同じ状態の中は元の順）
        lines += [row_text(s, strip_label) for s in shown]
        if not shown:
            lines.append("在庫あり店舗なし")
    elif r.message:
        lines.append(r.message)
    if not r.verified:
        lines.append("※検索URL未検証（開けない場合は stores.json を修正）")
    if r.url:
        lines.append(f"[サイトで確認]({r.url})")
    return _chunk(lines) or ["-"]


def field_value(r: CheckResult, strip_label: bool = False) -> str:
    """旧 API 互換: 最初のフィールド分だけ。"""
    return field_values(r, strip_label)[0]


def chain_lines(r: CheckResult, region: str = "", strip_label: bool = False) -> list[str]:
    """チェーン 1 つ分を本文の行に。1 行目が『**[チェーン名](リンク)** 愛知 5/25』（在庫あり店数/確認店数）、
    続けて在庫あり→少ない の店を全部。店舗行の無いチェーンは『🔗 [名前](リンク) メッセージ』の 1 行だけ。"""
    link = f"[{r.chain}]({r.url})" if r.url else r.chain
    if r.stocks:
        shown = sorted((s for s in r.stocks if s.status in SHOWN), key=lambda s: s.status.rank)
        head = f"**{link}** {region + ' ' if region else ''}{len(shown)}/{len(r.stocks)}"
        return [head] + [row_text(s, strip_label) for s in shown]
    extra = f" {r.message}" if r.message else ""
    if not r.verified:
        extra += " ※URL未検証"
    return [f"{r.status.emoji} {link}{extra}"]


def ulen(s: str) -> int:
    """Discord 流の文字数（UTF-16 の単位）。🟢 などの絵文字は 2 と数えられるので len() では足りない。"""
    return len(s.encode("utf-16-le")) // 2


def _embed_bytes(e: discord.Embed) -> int:
    parts = [e.title or "", e.description or "", e.footer.text if e.footer and e.footer.text else ""]
    parts += [f.name or "" for f in e.fields] + [f.value or "" for f in e.fields]
    return sum(len(p.encode("utf-8")) for p in parts)


def _embed_len(e: discord.Embed) -> int:
    n = ulen(e.title or "") + ulen(e.description or "")
    if e.footer and e.footer.text:
        n += ulen(e.footer.text)
    for f in e.fields:
        n += ulen(f.name or "") + ulen(f.value or "")
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
    stores = [s for r in results for s in r.stocks]
    n_in = sum(1 for s in stores if s.status == Status.IN_STOCK)
    n_low = sum(1 for s in stores if s.status == Status.LOW)
    lines = [code.label()]
    if meta.author or meta.publisher:
        lines.append(" / ".join(x for x in (meta.author, meta.publisher) if x))
    if meta.price:
        lines.append(meta.price)
    lines.append(f"検索範囲: **{scope_label}**" + (f"（店名に {area} 系の地名を含む店舗）" if area else ""))
    lines.append(f"**在庫あり店舗 {n_in + n_low}店**（多い{n_in} 少ない{n_low}）／ 確認 {len(stores)}店")
    if groups:
        # 地域ごとの内訳（愛知 15店（多い3 少ない12） ／ 京都 3店（多い1 少ない2））
        parts = []
        for name, rs in split_by_region(results, groups).items():
            ss = [s for r in rs for s in r.stocks]
            a = sum(1 for s in ss if s.status == Status.IN_STOCK)
            b = sum(1 for s in ss if s.status == Status.LOW)
            parts.append(f"{name} {a + b}店（多い{a} 少ない{b}）")
        if parts:
            lines.append(" ／ ".join(parts))
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
                   groups: Optional[dict[str, list[str]]] = None,
                   manual: Optional[list[StoreConfig]] = None) -> list[list[discord.Embed]]:
    """Embed を複数メッセージに分けて返す。各メッセージは 10 Embed / 約6000 文字以内。
    area 指定時（地域モード）は該当のあるチェーンだけを個別表示し、残りは1つのフィールドにまとめる。
    groups（地元＝愛知・京都のように複数地域）があれば『📍 愛知』『📍 京都』の区画に分けて、
    各チェーンの店をそれぞれの地域側に振り分ける（店が多くても埋もれないように）。
    graph（Keepa の PNG）があれば先頭 Embed の画像にし、送る側は最初のメッセージにその PNG を添付する。
    manual（自動検索できない店）があれば『📱 アプリで確認』『📞 電話で確認』の欄を末尾に足す。"""
    messages: list[list[discord.Embed]] = []
    cur_msg: list[discord.Embed] = []
    cur_len = 0
    cur_bytes = 0
    cur = header_embed(code, meta, results, area, elapsed, scope_label, graph, groups if area else None)
    rest: list[CheckResult] = []
    if area:
        results, rest = split_for_region(results, serves or {})

    def has_content(e: discord.Embed) -> bool:
        return bool(e.fields or e.title or e.description)

    def flush_embed() -> None:
        nonlocal cur, cur_len, cur_bytes, cur_msg
        if cur is not None and has_content(cur):
            cur_msg.append(cur)
            cur_len += _embed_len(cur)
            cur_bytes += _embed_bytes(cur)
        cur = discord.Embed(color=COLOR)

    def flush_message() -> None:
        nonlocal cur_msg, cur_len, cur_bytes
        if cur_msg:
            messages.append(cur_msg)
        cur_msg, cur_len, cur_bytes = [], 0, 0

    def make_room(add_ulen: int, add_bytes: int) -> None:
        """これから add_ulen 文字 / add_bytes バイト足すとメッセージの上限を超えるなら、Embed とメッセージを区切る。"""
        if (cur_len + _embed_len(cur) + add_ulen > MESSAGE_CHAR_LIMIT
                or cur_bytes + _embed_bytes(cur) + add_bytes > MESSAGE_BYTE_LIMIT
                or len(cur_msg) >= 10 - 1 and has_content(cur)):
            flush_embed()
            flush_message()

    def add_field(name: str, value: str) -> None:
        """1 フィールド追加。Embed あたりのフィールド数・メッセージあたりの Embed 数／文字数を超えるなら先に区切る。"""
        if len(cur.fields) >= FIELDS_PER_EMBED:
            flush_embed()
        make_room(ulen(name) + ulen(value), len(name.encode("utf-8")) + len(value.encode("utf-8")))
        cur.add_field(name=name, value=value, inline=False)

    def add_line(ln: str) -> None:
        """本文（description）に 1 行足す。description の上限を超えるなら続きの Embed に、メッセージの上限なら次のメッセージに。"""
        while ulen(ln) > DESC_LIMIT:
            ln = ln[:-(ulen(ln) - DESC_LIMIT + 1)] + "…"
        d = cur.description or ""
        if d and ulen(d) + 1 + ulen(ln) > DESC_LIMIT:
            flush_embed()
            d = ""
        make_room(ulen(ln) + 1, len(ln.encode("utf-8")) + 1)
        d = cur.description or ""
        cur.description = f"{d}\n{ln}" if d else ln

    def start_section(title: str) -> None:
        """『📍 愛知』のような見出し付きの Embed を新しく始める。"""
        flush_embed()
        cur.title = title

    def add_chain(r: CheckResult, region: str = "", strip_label: bool = False) -> None:
        for ln in chain_lines(r, region, strip_label):
            add_line(ln)

    split = bool(area and groups and len(groups) >= 2)
    flush_embed()                                   # 書誌のヘッダーは単独の Embed にして、チェーンは本文に並べる
    if split:
        for name, rs in split_by_region(results, groups).items():
            start_section(f"📍 {name}")
            for r in rs:
                add_chain(r, region=name, strip_label=True)   # 区画名で地域は分かるので『（愛知・名古屋市）』は出さない
        others = [r for r in results if not r.stocks]
        if others:
            start_section(LINKS_SECTION)
            for r in others:
                add_chain(r)
    else:
        for r in results:
            add_chain(r)

    def add_lines(name: str, lines: list[str]) -> None:
        """行のリストを 1 フィールドに。長ければ同じ見出しで複数フィールドに分ける。"""
        chunk: list[str] = []
        for ln in lines:
            if chunk and sum(ulen(x) + 1 for x in chunk) + ulen(ln) > FIELD_LIMIT:
                add_field(name, "\n".join(chunk))
                chunk = []
            chunk.append(ln)
        if chunk:
            add_field(name, "\n".join(chunk))

    fields = manual_fields(manual or [])
    if fields:
        if split:
            start_section(MANUAL_SECTION)
        for name, lines in fields:
            add_lines(name, lines)
    if rest:
        add_lines(REST_FIELD_NAME, [compact_line(r) for r in rest])
    flush_embed()
    flush_message()
    return messages
