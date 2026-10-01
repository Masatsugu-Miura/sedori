"""honto（丸善・ジュンク堂・文教堂・戸田書店 ほか）
検索 https://honto.jp/netstore/search_10{isbn13}.html → 商品ID → 店舗在庫 https://honto.jp/netstore/pd-store_{id}.html
※ honto の紙の本ストアは丸善ジュンク堂ネットストア（maruzenjunkudo.co.jp）に移管され、現在 netstore の検索は
   電子書籍（/ebook/）に転送、pd-store_ は 404 になる。丸善・ジュンク堂の店舗在庫は maruzenjunkudo.py を使う。
"""
from __future__ import annotations

import re

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, StoreStock, rows_to_stocks, scan_text_for_stocks, strip_noise

_PD = re.compile(r"/netstore/pd[-_](?:store_)?(\d+)\.html")


class HontoChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        m = _PD.search(html)
        if m:
            store_url = f"https://honto.jp/netstore/pd-store_{m.group(1)}.html"
            try:
                status, page = await self.fetch(session, store_url)
                if status < 400:
                    html, res.url = page, store_url
            except Exception:  # noqa: BLE001
                pass
        else:
            res.message = "honto に該当商品が見つかりませんでした"
            return []
        soup = BeautifulSoup(html, "html.parser")
        strip_noise(soup)
        stocks = rows_to_stocks(soup, "table tr, li, dl")
        return stocks or scan_text_for_stocks(soup.get_text("\n"))
