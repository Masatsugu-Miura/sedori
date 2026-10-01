"""紀伊國屋書店：商品ページ https://www.kinokuniya.co.jp/f/dsg-01-{isbn13} に店舗在庫の一覧がある。"""
from __future__ import annotations

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, StoreStock, rows_to_stocks, scan_text_for_stocks, strip_noise


class KinokuniyaChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        soup = BeautifulSoup(html, "html.parser")
        strip_noise(soup)
        # 店舗在庫は表（店名 / 在庫状況）か li のリストで出る
        stocks = rows_to_stocks(soup, "table tr, [class*=stock] li, [class*=store] li, [id*=stock] li")
        return stocks or scan_text_for_stocks(soup.get_text("\n"))
