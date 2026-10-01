"""書誌情報（タイトル等）の取得と、B0… ASIN から JAN/ISBN への解決。"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .codes import Code, isbn13_to_10

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}

# Keepa の価格・ランキング推移グラフ（波形）。Discord は画像URLを自分で取りに行くが Keepa がそれをブロックするので、
# bot 側で PNG を取得して添付ファイルとして送る。KEEPA_API_KEY があれば公式 API の graphimage を使う。
GRAPH_FILENAME = "keepa.png"
KEEPA_GRAPH = ("https://graph.keepa.com/pricehistory.png?asin={asin}&domain=co.jp"
               "&width=800&height=300&range=365&salesrank=1&amazon=1&new=1&used=1")
KEEPA_API_GRAPH = ("https://api.keepa.com/graphimage?key={key}&domain=5&asin={asin}"
                   "&width=800&height=300&range=365&salesrank=1&amazon=1&new=1&used=1")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """PNG の (幅, 高さ)。PNG でなければ (0, 0)。"""
    if len(data) < 24 or not data.startswith(_PNG_SIG):
        return 0, 0
    return struct.unpack(">II", data[16:24])


async def fetch_keepa_graph(session: aiohttp.ClientSession, asin: Optional[str]) -> Optional[bytes]:
    """Keepa のグラフ PNG を返す。ASIN が無い／取れない／ブロック画像（500x200 の案内）なら None。"""
    if not asin:
        return None
    key = os.environ.get("KEEPA_API_KEY", "").strip()
    url = KEEPA_API_GRAPH.format(key=key, asin=asin) if key else KEEPA_GRAPH.format(asin=asin)
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status != 200 or "image/png" not in (r.headers.get("Content-Type") or ""):
                return None
            data = await r.read()
    except Exception:  # noqa: BLE001
        return None
    w, _ = png_size(data)
    return data if w >= 700 else None


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
