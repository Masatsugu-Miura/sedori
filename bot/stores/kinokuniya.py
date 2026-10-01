"""紀伊國屋書店：商品ページ https://www.kinokuniya.co.jp/f/dsg-01-{isbn13} に店舗在庫の一覧がある。"""
from __future__ import annotations

import re

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, StoreStock, classify, scan_text_for_stocks


class KinokuniyaChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[StoreStock] = []
        # 店舗在庫テーブル（行ごとに「店名 / 在庫状況」）
        for row in soup.select("table tr, ul li, div[class*=stock] li, div[class*=store] li"):
            txt = row.get_text(" ", strip=True)
            if "店" not in txt or len(txt) > 120:
                continue
            m = re.match(r"(.+?店)\s+(.+)$", txt)
            if not m:
                continue
            st = classify(m.group(2))
            if st.name != "UNKNOWN":
                out.append(StoreStock(store=m.group(1).strip(), status=st, note=m.group(2).strip()[:30]))
        if out:
            # 重複排除
            seen, uniq = set(), []
            for s in out:
                if s.store not in seen:
                    seen.add(s.store); uniq.append(s)
            return uniq
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        return scan_text_for_stocks(soup.get_text("\n"))
