#!/usr/bin/env python3
"""Googleスプレッドシート等からCSVで書き出した書店リストを stores.json に取り込む。

使い方:
  python scripts/import_sheet.py 書店リスト.csv            # 追記
  python scripts/import_sheet.py 書店リスト.csv --replace  # 置き換え
  python scripts/import_sheet.py 書店リスト.csv --auto     # 検索ページを開いて在庫表記を自動解析する店として登録

CSV の列は自動判定します（ヘッダー行があれば「店名/書店/名前」「URL/検索URL」「店舗/支店」「メモ」を優先）。
URL 列は {isbn13} などのプレースホルダでも、実際に ISBN で検索したときの URL をそのまま貼ったものでも構いません
（URL 中の ISBN をプレースホルダに置き換えます）。URL が無い行はホームページだけのリンク店として登録されます。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.codes import has_placeholder, templatize_url  # noqa: E402
from bot.stores import load_configs, make_id, save_configs, stores_path  # noqa: E402
from bot.stores.base import StoreConfig  # noqa: E402

NAME_KEYS = ("店名", "書店", "名前", "name", "チェーン")
URL_KEYS = ("検索url", "検索", "url", "リンク")
BRANCH_KEYS = ("店舗", "支店", "対象店", "stores")
NOTE_KEYS = ("メモ", "備考", "note")
_URL = re.compile(r"https?://\S+")


def pick(header: list[str], keys: tuple[str, ...]) -> int | None:
    low = [h.strip().lower() for h in header]
    for k in keys:
        for i, h in enumerate(low):
            if k in h:
                return i
    return None


def home_of(url: str) -> str:
    m = re.match(r"https?://[^/]+/?", url)
    return m.group(0) if m else ""


def rows_to_configs(rows: list[list[str]], taken: set[str], auto: bool) -> list[StoreConfig]:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header = rows[0]
    has_header = not any(_URL.search(c) for c in header)
    ni = pick(header, NAME_KEYS) if has_header else None
    ui = pick(header, URL_KEYS) if has_header else None
    bi = pick(header, BRANCH_KEYS) if has_header else None
    mi = pick(header, NOTE_KEYS) if has_header else None
    body = rows[1:] if has_header else rows

    out: list[StoreConfig] = []
    for r in body:
        cells = [c.strip() for c in r]
        url = cells[ui] if ui is not None and ui < len(cells) else next((c for c in cells if _URL.match(c)), "")
        name = cells[ni] if ni is not None and ni < len(cells) else next((c for c in cells if c and not _URL.match(c)), "")
        if not name:
            continue
        url = templatize_url(url)
        branches = []
        if bi is not None and bi < len(cells) and cells[bi]:
            branches = [b.strip() for b in re.split(r"[、,/／\s]+", cells[bi]) if b.strip()]
        note = cells[mi] if mi is not None and mi < len(cells) else ""
        searchable = has_placeholder(url)
        out.append(StoreConfig(
            id=make_id(name, url, taken), name=name,
            search=url if searchable else "",
            home=home_of(url),
            checker=("generic" if auto else "link") if searchable else "link",
            enabled=True, verified=False, stores=branches,
            note=note or "スプレッドシートから取り込み"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--replace", action="store_true", help="既存の stores.json を置き換える")
    ap.add_argument("--auto", action="store_true", help="検索URLを自動解析する(generic)。既定はリンクのみ")
    a = ap.parse_args()

    rows = list(csv.reader(Path(a.csv).open(encoding="utf-8-sig", newline="")))
    existing = [] if a.replace else load_configs()
    taken = {c.id for c in existing}
    added = rows_to_configs(rows, taken, a.auto)
    if not added:
        raise SystemExit("取り込める行がありませんでした（店名の列が見つかりません）")
    existing.extend(added)
    save_configs(existing)
    print(f"{len(added)} 件を取り込み → {stores_path()}（合計 {len(existing)} 件）")
    for c in added:
        print(f"  {c.id:14} {c.name}  {c.search or c.home}  {'/'.join(c.stores)}")


if __name__ == "__main__":
    main()
