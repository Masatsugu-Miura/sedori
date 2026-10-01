"""くまざわ書店（www.search.kumabook.com/kumazawa/html/ ＝ EC-CUBE 系の在庫検索）
kumabook.com トップの検索フォームは products/list?mode=books&name={isbn13}（GET）に飛び、商品は
products/detail/{id}?mode=books&name=… にリンクされる（トップページの新刊リンクがこの形式）。
商品詳細に店舗別在庫の表がある想定で、行ごとに「店名 ＋ 在庫表記（○△× / 在庫あり…）」を読む。

※ この環境（データセンター回線）からは www.search.kumabook.com / search.kumabook.com への接続が
   egress ポリシーで拒否される（HTTP は 403 host_not_allowed、HTTPS は切断）ため本番の HTML は未確認。
   読めない画面構成なら『要確認』（リンクで確認）に落ちる。
"""
from __future__ import annotations

from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, StoreStock, rows_to_stocks, scan_text_for_stocks, strip_noise

BASE = "https://www.search.kumabook.com/kumazawa/html/"


def detail_url(html: str) -> str:
    """商品一覧 → 最初の商品詳細 URL（無ければ空）。"""
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one('a[href*="products/detail/"]')
    return urljoin(BASE, a["href"]) if a and a.get("href") else ""


def parse_detail(html: str) -> list[StoreStock]:
    soup = BeautifulSoup(html, "html.parser")
    strip_noise(soup)
    stocks = rows_to_stocks(soup, "table tr, dl, li, div[class*=stock], div[class*=shop], div[class*=store]")
    return stocks or scan_text_for_stocks(soup.get_text("\n"))


class KumazawaChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        url = detail_url(html)
        if not url:
            # 検索結果が 1 件のとき詳細に直接飛ぶ構成かもしれないので、開いたページ自体も読んでみる
            stocks = parse_detail(html)
            if not stocks:
                res.message = "くまざわ書店の検索に該当商品がありません（または画面構成が想定と違います）"
            return stocks
        status, page = await self.fetch(session, url)
        if status >= 400:
            res.message = f"商品ページが HTTP {status}"
            return []
        res.url = url
        return parse_detail(page)
