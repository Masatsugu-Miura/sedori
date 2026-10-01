"""書誌情報（タイトル等）の取得と、B0… ASIN から JAN/ISBN への解決。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .codes import Code, isbn13_to_10

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}


@dataclass
class BookMeta:
    title: str = ""
    author: str = ""
    publisher: str = ""
    price: str = ""
    cover: str = ""
    source: str = ""


async def fetch_meta(session: aiohttp.ClientSession, code: Code) -> BookMeta:
    """openBD → Google Books の順で書誌を引く。失敗時は空の BookMeta。"""
    isbn = code.isbn13
    if not isbn:
        return BookMeta()
    try:
        async with session.get(f"https://api.openbd.jp/v1/get?isbn={isbn}", headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                if data and data[0]:
                    s = data[0].get("summary", {})
                    return BookMeta(title=s.get("title", ""), author=s.get("author", ""),
                                    publisher=s.get("publisher", ""), cover=s.get("cover", ""),
                                    price=_openbd_price(data[0]), source="openBD")
    except Exception:
        pass
    try:
        async with session.get(f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}",
                               headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                items = data.get("items") or []
                if items:
                    v = items[0].get("volumeInfo", {})
                    return BookMeta(title=v.get("title", ""), author="、".join(v.get("authors", [])),
                                    publisher=v.get("publisher", ""),
                                    cover=(v.get("imageLinks") or {}).get("thumbnail", ""),
                                    source="Google Books")
    except Exception:
        pass
    return BookMeta()


def _openbd_price(rec: dict) -> str:
    try:
        prices = rec["onix"]["ProductSupply"]["SupplyDetail"]["Price"]
        for p in prices:
            if p.get("PriceAmount"):
                return f"¥{int(float(p['PriceAmount'])):,}（税抜）"
    except Exception:
        pass
    return ""


_ISBN13_RE = re.compile(r"ISBN[-‐]?13\D{0,20}(97[89][\d\-‐]{10,16})", re.I)
_JAN_RE = re.compile(r"(?:JAN|EAN|GTIN)\D{0,20}(\d{13})", re.I)


async def resolve_asin(session: aiohttp.ClientSession, code: Code) -> Optional[str]:
    """B0… の ASIN について Amazon.co.jp の商品ページから ISBN-13 / JAN を拾う。見つからなければ None。
    Amazon はボット対策で 503 を返すことがあるため、あくまで best-effort。"""
    if not code.asin:
        return None
    url = f"https://www.amazon.co.jp/dp/{code.asin}"
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status != 200:
                code.notes.append(f"Amazon が {r.status} を返したため ASIN→JAN 変換できませんでした。")
                return None
            html = await r.text(errors="ignore")
    except Exception as e:
        code.notes.append(f"Amazon に接続できませんでした（{type(e).__name__}）。")
        return None

    m = _ISBN13_RE.search(html)
    if m:
        isbn = re.sub(r"\D", "", m.group(1))
        if len(isbn) == 13:
            code.isbn13 = code.jan = isbn
            code.isbn10 = isbn13_to_10(isbn)
            return isbn
    m = _JAN_RE.search(html)
    if m:
        code.jan = m.group(1)
        return code.jan
    code.notes.append("Amazon ページに ISBN/JAN が見つかりませんでした。JAN を直接入力してください。")
    return None
