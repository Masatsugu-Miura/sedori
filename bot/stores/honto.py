"""honto（丸善・ジュンク堂・文教堂・戸田書店 ほか）
検索 https://honto.jp/netstore/search_10{isbn13}.html → 商品ID → 店舗在庫 https://honto.jp/netstore/pd-store_{id}.html
"""
from __future__ import annotations

import re

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import Checker, CheckResult, StoreStock, classify, scan_text_for_stocks

_PD = re.compile(r"/netstore/pd[-_](?:store_)?(\d+)\.html")


class HontoChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        m = _PD.search(html)
        if m:
            pd_id = m.group(1)
            store_url = f"https://honto.jp/netstore/pd-store_{pd_id}.html"
            try:
                status, html = await self.fetch(session, store_url)
                if status < 400:
                    res.url = store_url
            except Exception:  # noqa: BLE001
                pass
        soup = BeautifulSoup(html, "html.parser")
        out: list[StoreStock] = []
        for row in soup.select("table tr, li, dl"):
            txt = row.get_text(" ", strip=True)
            if len(txt) > 140 or ("店" not in txt and "書店" not in txt):
                continue
            m2 = re.match(r"(.+?(?:店|書店|ブックセンター))\s*(.+)$", txt)
            if not m2:
                continue
            st = classify(m2.group(2))
            if st.name != "UNKNOWN":
                out.append(StoreStock(store=m2.group(1).strip(), status=st, note=m2.group(2).strip()[:30]))
        if out:
            seen, uniq = set(), []
            for s in out:
                if s.store not in seen:
                    seen.add(s.store); uniq.append(s)
            return uniq
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        return scan_text_for_stocks(soup.get_text("\n"))
