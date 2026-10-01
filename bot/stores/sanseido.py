"""三省堂書店（www.books-sanseido.jp）
BookStockList.action?isbn={isbn13} の表（店舗名 / TEL / 在庫 ○×）に全店の在庫。
"""
from __future__ import annotations

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, Status, StoreStock, classify, dedupe


def parse_stock_list(html: str) -> list[StoreStock]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[StoreStock] = []
    for tr in soup.select("table.spec tr"):
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        name, mark = tds[0].get_text(" ", strip=True), tds[-1].get_text(" ", strip=True)
        st = classify(mark)
        if name and st != Status.UNKNOWN:
            out.append(StoreStock(store=name, status=st, note=mark))
    return dedupe(out)


class SanseidoChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        stocks = parse_stock_list(html)
        if not stocks:
            res.message = "三省堂の在庫検索に該当商品がありません（書籍のみ対象）"
        return stocks
