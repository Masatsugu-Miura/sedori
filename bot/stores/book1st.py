"""ブックファースト（b1st.e-netservice.biz の書籍在庫検索）
stock.asp?isbn={isbn13} の HTML に対象店舗の一覧（店名・住所）と hdIsbn / hdStIdList があり、
店ごとの在庫はページの JS が MeRemote.asp に 1 店ずつ POST して取っている（cnt=1..店舗数）。同じ POST を店ごとに送る。
"""
from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, Status, StoreStock, classify, short_area

REMOTE = "https://b1st.e-netservice.biz/book1stnet/searchbook/MeRemote.asp"
PARALLEL = 4


def parse_stock_page(html: str) -> tuple[str, str, list[tuple[int, str, str]]]:
    """stock.asp → (hdIsbn, hdStIdList, [(cnt, 店名, 住所)])。該当商品なしなら hdIsbn が空。"""
    soup = BeautifulSoup(html, "html.parser")
    isbn = soup.select_one("input[name=hdIsbn]")
    ids = soup.select_one("input[name=hdStIdList]")
    stores: list[tuple[int, str, str]] = []
    for i, row in enumerate(soup.select(".card-body .row.mb-3"), 1):
        name = row.select_one(".fw-bold")
        addr = row.select_one(".ShopList_Value")
        if name:
            stores.append((i, name.get_text(strip=True), addr.get_text(" ", strip=True).replace("MAP", "").strip()
                           if addr else ""))
    return (isbn.get("value", "") if isbn else ""), (ids.get("value", "") if ids else ""), stores


def parse_remote(text: str) -> Optional[tuple[str, Status, str]]:
    """MeRemote.asp の応答（RTC=1 / LIST=<li…> / TIME=）→ (店名, 在庫, 表記)。"""
    fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    if fields.get("RTC") != "1" or not fields.get("LIST"):
        return None
    soup = BeautifulSoup(fields["LIST"], "html.parser")
    name = soup.select_one(".fw-bold")
    mark = soup.select_one(".storebox_tcenter")
    if not name or not mark:
        return None
    m = mark.get_text(strip=True)
    return name.get_text(strip=True), classify(m), m


class Book1stChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        isbn, ids, stores = parse_stock_page(html)
        if not isbn or not stores:
            res.message = "ブックファーストの在庫検索に該当商品がありません（書籍のみ対象）"
            return []
        total = len(stores)
        wanted = self.cfg.stores + self.keywords
        if wanted:
            # 地域指定時は店名か住所に地名を含む店だけ問い合わせる
            stores = [s for s in stores if any(w in s[1] or w in short_area(s[2]) for w in wanted)]
            if not stores:
                res.message = "指定地域にブックファーストの対象店舗がありません"
                return []
        sem = asyncio.Semaphore(PARALLEL)

        async def one(cnt: int, addr: str) -> Optional[StoreStock]:
            data = {"SyoriFlg": "zaikoinfo", "isbn": isbn, "storeid": ids, "kns": "1",
                    "cnt": str(cnt), "maxcnt": str(total)}
            async with sem:
                status, text = await self.fetch(session, REMOTE, data=data, headers={"Referer": res.url})
            got = parse_remote(text) if status < 400 else None
            if not got or got[1] == Status.UNKNOWN:
                return None
            name, st, mark = got
            area = short_area(addr)
            return StoreStock(store=f"{name}（{area}）" if area else name, status=st, note=mark)

        got = await asyncio.gather(*(one(c, a) for c, _, a in stores), return_exceptions=True)
        return [g for g in got if isinstance(g, StoreStock)]
