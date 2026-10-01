"""未来屋書店（search.miraiyashoten.co.jp 店頭在庫検索）
ページ /neighborhood/{isbn13}/?pref={JIS} の JS が
  /neighborhoodAPI/?isbn=..&prefecture=..  → 店舗一覧(JSON)
  /stockAPI/?isbn=..&storecodes=a,b,..     → {店コード: 在庫数}（3以上=あり / 1-2=僅少 / 0=なし / 負=取置不可）
を呼んでいるので同じ順で取る。都道府県単位なので全国指定では取得しない。
"""
from __future__ import annotations

import asyncio
import json

import aiohttp

from ..codes import Code
from .base import (AREA_ONLY_MSG, Checker, CheckResult, Status, StoreStock, prefs_in_keywords, short_area)

BASE = "https://search.miraiyashoten.co.jp"


def shops_from_json(text: str) -> list[dict]:
    """neighborhoodAPI の応答（dict でも list でも来る。0件は {"count":0}）。"""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data.values() if isinstance(data, dict) else data
    return [d for d in items if isinstance(d, dict) and d.get("shop_code")]


def stock_status(n: int) -> tuple[Status, str]:
    if n >= 3:
        return Status.IN_STOCK, "在庫あり"
    if n >= 1:
        return Status.LOW, "在庫僅少"
    if n == 0:
        return Status.OUT, "在庫なし"
    return Status.UNKNOWN, "お取り置きできません"


def merge(shops: list[dict], stock_text: str) -> list[StoreStock]:
    try:
        stock = json.loads(stock_text)
    except ValueError:
        return []
    out: list[StoreStock] = []
    for s in shops:
        raw = stock.get(str(s["shop_code"])) if isinstance(stock, dict) else None
        if raw is None:
            continue
        try:
            st, note = stock_status(int(raw))
        except (TypeError, ValueError):
            continue
        area = short_area(s.get("address", ""))
        name = s.get("tenpoName", "")
        out.append(StoreStock(store=f"{name}（{area}）" if area else name, status=st, note=note))
    return out


class MiraiyaChecker(Checker):
    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        isbn = code.isbn13
        if not isbn:
            res.message = "書籍（ISBN）のみ検索できます"
            return []
        prefs = prefs_in_keywords(self.keywords)
        if not prefs:
            res.message = AREA_ONLY_MSG
            return []
        res.url = f"{BASE}/neighborhood/{isbn}/?pref={prefs[0][0]}"
        lists = await asyncio.gather(*(self.fetch(session, f"{BASE}/neighborhoodAPI/?isbn={isbn}&prefecture={c}")
                                       for c, _ in prefs), return_exceptions=True)
        shops = [s for r in lists if isinstance(r, tuple) and r[0] < 400 for s in shops_from_json(r[1])]
        if not shops:
            res.message = "指定地域に未来屋書店の店舗が見つかりません"
            return []
        codes = ",".join(str(s["shop_code"]) for s in shops) + ","
        status, text = await self.fetch(session, f"{BASE}/stockAPI/?isbn={isbn}&storecodes={codes}")
        return merge(shops, text) if status < 400 else []
