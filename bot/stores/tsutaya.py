"""TSUTAYA / 蔦屋書店（store-tsutaya.tsite.jp の商品在庫検索）
1. /search?productkey={jan} → 商品ページ（/search/item/sell_book/{workId}/{jan}）に「在庫のある店舗を探す」リンク
2. /search/result/stock/result?workId=..&productKey=..&storeSearchKeyword={住所や店名} に店舗ごとの在庫（20店/ページ）
キーワード無し（空文字）では一覧が出ず、1 ページの件数も変えられない。全国指定では住所に必ず含まれる
『県』『東京都』『北海道』『大阪府』『京都府』を順に引いて全ページ（約 600 店 ≒ 33 ページ）を集める。
1 ページ 4〜5 秒かかる（並行してもあまり縮まない）ので、ページ数と時間に上限を設けて取れたぶんを返す。
"""
from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import quote, urljoin

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import (Checker, CheckResult, Status, StoreStock, classify, dedupe, prefs_in_keywords)

BASE = "https://store-tsutaya.tsite.jp/"
MAX_PAGES = 3          # 地域指定時：1キーワードあたり最大 3 ページ（60店）
MAX_TERMS = 3
PARALLEL = 4
# 全国指定：この 5 語で 47 都道府県の住所を網羅する（『県』が 43 県、残り 4 都道府は正式名で）
NATIONAL_TERMS = ["県", "東京都", "北海道", "大阪府", "京都府"]
NATIONAL_MAX_PAGES = 40      # 全国指定で取るページ数の上限（全語合計）
NATIONAL_BUDGET_SEC = 50.0   # 全国指定でページ取得を打ち切る経過秒数（check_all のチェーン上限より短く）


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
    timeout = aiohttp.ClientTimeout(total=15)   # 1 ページ 4〜9 秒かかることがある

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        stock_url = stock_page_url(html)
        if not stock_url:
            res.message = "TSUTAYA の商品在庫検索に該当商品がありません"
            return []
        res.url = stock_url
        terms = search_terms(self.keywords)
        national = not terms
        if national:
            terms, max_pages = NATIONAL_TERMS, NATIONAL_MAX_PAGES   # リンクは店舗検索ページのまま（語を選べる）
        else:
            max_pages = MAX_PAGES
            if len(terms) == 1:
                res.url = result_url(stock_url, terms[0])   # 地域が 1 つなら、その地域の結果ページを直接開ける
            # 地元（愛知＋京都）のように複数地域なら、語を選べる店舗検索ページのままにする
        sem = asyncio.Semaphore(PARALLEL)
        deadline = time.monotonic() + NATIONAL_BUDGET_SEC
        left = [NATIONAL_MAX_PAGES]      # 全国指定での残りページ数（全語で共有）
        skipped = [0]

        async def get_page(term: str, page: int) -> tuple[int, str]:
            async with sem:
                if national and (left[0] <= 0 or time.monotonic() > deadline):
                    skipped[0] += 1
                    return 0, ""
                left[0] -= 1
                return await self.fetch(session, result_url(stock_url, term, page))

        async def one_term(term: str) -> list[StoreStock]:
            label = "" if national else term
            status, page1 = await get_page(term, 1)
            if status >= 400 or not page1:
                return []
            stocks, last = parse_result(page1, label)
            more = await asyncio.gather(*(get_page(term, p) for p in range(2, min(last, max_pages) + 1)),
                                        return_exceptions=True)
            for r in more:
                if isinstance(r, tuple) and r[0] < 400 and r[1]:
                    stocks += parse_result(r[1], label)[0]
            return stocks

        got = await asyncio.gather(*(one_term(t) for t in terms), return_exceptions=True)
        stocks: list[StoreStock] = []
        for g in got:
            if isinstance(g, list):
                stocks += g
        if skipped[0]:
            res.message = f"全国は時間・ページ数の上限で {NATIONAL_MAX_PAGES - left[0]} ページまで取得（未取得 {skipped[0]} ページ）"
        return dedupe(stocks)
