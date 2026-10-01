"""TSUTAYA / 蔦屋書店（store-tsutaya.tsite.jp の商品在庫検索）
1. /search?productkey={jan} → 商品ページ（/search/item/sell_book/{workId}/{jan}）に「在庫のある店舗を探す」リンク
2. /search/result/stock/result?workId=..&productKey=..&storeSearchKeyword={住所や店名} に店舗ごとの在庫（20店/ページ）
キーワード無しでは一覧が出ないため、全国指定のときは取得しない。
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, urljoin

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import (AREA_ONLY_MSG, Checker, CheckResult, Status, StoreStock, classify, dedupe, prefs_in_keywords)

BASE = "https://store-tsutaya.tsite.jp/"
MAX_PAGES = 3          # 1キーワードあたり最大 3 ページ（60店）
MAX_TERMS = 3


def stock_page_url(html: str) -> str:
    """商品ページから「在庫のある店舗を探す」URL を取る。無ければ ""（該当商品なし）。"""
    soup = BeautifulSoup(html, "html.parser")
    a = soup.select_one('a[href*="/search/result/stock?"]')
    return urljoin(BASE, a["href"]) if a else ""


def result_url(stock_url: str, term: str, page: int = 1) -> str:
    url = stock_url.replace("/search/result/stock?", "/search/result/stock/result?", 1)
    url += "&storeSearchKeyword=" + quote(term)
    return url + (f"&dispPageNo={page}" if page > 1 else "")


def parse_result(html: str, label: str = "") -> tuple[list[StoreStock], int]:
    """在庫結果ページ → (店舗リスト, 最終ページ番号)。label は店名に添える地域（例: 愛知県）。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[StoreStock] = []
    for box in soup.select("div.stock_store_info_list"):
        name_el = box.select_one(".store_name")
        if not name_el:
            continue
        name = name_el.get_text(" ", strip=True)
        texts = [p.get_text(" ", strip=True) for p in box.select(".stock_info p")]
        texts = [t for t in texts if t]
        msg_el = box.select_one(".stock_message")
        msg = msg_el.get_text(" ", strip=True) if msg_el else ""
        status = classify(texts[0]) if texts else Status.UNKNOWN
        if status == Status.UNKNOWN and msg:
            status = classify(msg)          # 「取り扱いがありません」→ なし
        if status == Status.UNKNOWN:
            continue
        note = " ".join(texts + ([msg] if msg else []))[:30]
        out.append(StoreStock(store=f"{name}（{label}）" if label else name, status=status, note=note))
    pages = [int(n) for n in re.findall(r"dispPageNo=(\d+)", html)]
    return out, max(pages, default=1)


def search_terms(keywords: list[str]) -> list[str]:
    """都道府県名があれば『愛知県』のように正式名で（『京都』だと東京都もヒットするため）。無ければ地名そのまま。"""
    prefs = [p for _, p in prefs_in_keywords(keywords)]
    return (prefs or keywords)[:MAX_TERMS]


class TsutayaChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        stock_url = stock_page_url(html)
        if not stock_url:
            res.message = "TSUTAYA の商品在庫検索に該当商品がありません"
            return []
        res.url = stock_url
        terms = search_terms(self.keywords)
        if not terms:
            res.message = AREA_ONLY_MSG
            return []
        res.url = result_url(stock_url, terms[0])

        async def one_term(term: str) -> list[StoreStock]:
            status, page1 = await self.fetch(session, result_url(stock_url, term))
            if status >= 400:
                return []
            stocks, last = parse_result(page1, term)
            more = await asyncio.gather(*(self.fetch(session, result_url(stock_url, term, p))
                                          for p in range(2, min(last, MAX_PAGES) + 1)), return_exceptions=True)
            for r in more:
                if isinstance(r, tuple) and r[0] < 400:
                    stocks += parse_result(r[1], term)[0]
            return stocks

        got = await asyncio.gather(*(one_term(t) for t in terms), return_exceptions=True)
        stocks: list[StoreStock] = []
        for g in got:
            if isinstance(g, list):
                stocks += g
        return dedupe(stocks)
