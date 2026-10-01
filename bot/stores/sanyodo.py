"""三洋堂書店（www.sanyodo.co.jp/lookup）
products-list?isbncd={isbn13}&booksearch=1 → products-detail?productcode=… の「お店の在庫状況」に全店の あり/なし。
店名だけでは地域が分からないので、店舗一覧 /shop の住所から『愛知県豊川市』等を店名に添える（プロセス内でキャッシュ）。
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, Status, StoreStock, classify, dedupe, short_area

BASE = "https://www.sanyodo.co.jp/"
SHOP_LIST = BASE + "shop"
_AREA_CACHE: dict[str, str] = {}


def detail_url(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one('a[href*="products-detail?productcode="]')
    return urljoin(BASE, a["href"]) if a else ""


def parse_shop_areas(html: str) -> dict[str, str]:
    """店舗一覧 → {店名: 地域ラベル}。"""
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, str] = {}
    for li in soup.select("li.sl-listitem"):
        name, addr = li.select_one(".sl-shop-name"), li.select_one(".sl-shop-address")
        if name and addr:
            out[name.get_text(strip=True)] = short_area(addr.get_text(strip=True))
    return out


def parse_detail(html: str, areas: Optional[dict[str, str]] = None) -> list[StoreStock]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[StoreStock] = []
    for dl in soup.select(".stock-shop-block dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if not dt or not dd:
            continue
        name, mark = dt.get_text(strip=True), dd.get_text(" ", strip=True)
        icon = dd.find("i")
        cls = " ".join(icon.get("class", [])) if icon else ""
        if "circle" in cls or mark == "あり":
            st = Status.IN_STOCK
        elif "closs" in cls or "cross" in cls or mark == "なし":
            st = Status.OUT
        else:
            st = classify(mark)
        if st == Status.UNKNOWN:
            continue
        area = (areas or {}).get(name, "")
        out.append(StoreStock(store=f"{name}（{area}）" if area else name, status=st, note=mark[:30]))
    return dedupe(out)


class SanyodoChecker(Checker):
    async def _areas(self, session: aiohttp.ClientSession) -> dict[str, str]:
        if not _AREA_CACHE:
            try:
                status, html = await self.fetch(session, SHOP_LIST)
                if status < 400:
                    _AREA_CACHE.update(parse_shop_areas(html))
            except Exception:  # noqa: BLE001  店舗一覧が取れなくても在庫は出す
                pass
        return _AREA_CACHE

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        url = detail_url(html)
        if not url:
            res.message = "三洋堂の商品検索に該当商品がありません"
            return []
        (status, page), areas = await asyncio.gather(self.fetch(session, url), self._areas(session))
        if status >= 400:
            res.message = f"商品ページが HTTP {status}"
            return []
        res.url = url
        return parse_detail(page, areas)
