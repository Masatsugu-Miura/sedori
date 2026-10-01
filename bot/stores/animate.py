"""アニメイト：商品ページの「店舗在庫を確認」は zaiko.shoptech.jp の在庫 API（HTML 断片）を JS で読み込んでいる。
同じ URL を JAN で直接開く（api_key は animate-onlineshop.jp の kaitos.js に公開で埋め込まれている値。変わったら更新）。
"""
from __future__ import annotations

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, Status, StoreStock, classify, dedupe

API_KEY = "LbkUfpMLuyzC5wjnnUM2q3Q5gF8KNBmV"
STOCK_API = "https://zaiko.shoptech.jp/api/stocks/?api_key={key}&product_code={jan}&latitude=&longitude="


def parse_stock_table(html: str) -> list[StoreStock]:
    """<table id="stock_table"> の 店舗名 / 在庫数量（在庫あり・なし～残りわずか 等）。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[StoreStock] = []
    for tr in soup.select("table#stock_table tbody tr, table.stock_table tbody tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue
        name, mark = tds[0].get_text(" ", strip=True), tds[-1].get_text(" ", strip=True)
        st = classify(mark)          # 「なし～残りわずか」は わずか 扱い
        if name and st != Status.UNKNOWN:
            out.append(StoreStock(store=name, status=st, note=mark))
    return dedupe(out)


class AnimateChecker(Checker):
    needs_search_page = False

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        jan = code.jan or code.isbn13
        if not jan:
            res.message = "JAN/ISBN が無いと店舗在庫を引けません"
            return []
        status, page = await self.fetch(session, STOCK_API.format(key=API_KEY, jan=jan),
                                        headers={"Referer": "https://www.animate-onlineshop.jp/"})
        if status >= 400:
            res.message = f"店舗在庫 API が HTTP {status}"
            return []
        stocks = parse_stock_table(page)
        if not stocks:
            res.message = "アニメイトの店舗在庫データに該当商品がありません"
        return stocks
