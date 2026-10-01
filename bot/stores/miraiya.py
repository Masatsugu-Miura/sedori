"""未来屋書店（search.miraiyashoten.co.jp 店頭在庫検索）
ページ /neighborhood/{isbn13}/?pref={JIS} の JS が
  /neighborhoodAPI/?isbn=..&prefecture=..  → 店舗一覧(JSON)
  /stockAPI/?isbn=..&storecodes=a,b,..     → {店コード: 在庫数}（3以上=あり / 1-2=僅少 / 0=なし / 負=取置不可）
を呼んでいるので同じ順で取る。店舗一覧は都道府県単位なので、全国指定では 47 都道府県ぶん（4 本並行で約 5 秒）取り、
在庫は全店コードをまとめて 1 回で引く（prefecture 無しだと既定地域の近隣店しか返らない）。
"""
from __future__ import annotations

import asyncio
import json

import aiohttp

from ..codes import Code
from .base import (PREFECTURES, Checker, CheckResult, Status, StoreStock, prefs_in_keywords, short_area)

BASE = "https://search.miraiyashoten.co.jp"
PARALLEL = 4
STOCK_CHUNK = 200   # stockAPI に一度に渡す店コード数（全国 195 店で URL 約 1KB。増えたら分割）


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
    needs_search_page = False

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        isbn = code.isbn13
        if not isbn:
            res.message = "書籍（ISBN）のみ検索できます"
            return []
        prefs = prefs_in_keywords(self.keywords)
        if prefs:
            res.url = f"{BASE}/neighborhood/{isbn}/?pref={prefs[0][0]}"
        else:
            prefs = list(enumerate(PREFECTURES, 1))   # 全国：47 都道府県すべて
            res.url = f"{BASE}/neighborhood/{isbn}/"
        sem = asyncio.Semaphore(PARALLEL)

        async def shops_of(pref_code: int) -> list[dict]:
            async with sem:
                status, text = await self.fetch(session, f"{BASE}/neighborhoodAPI/?isbn={isbn}&prefecture={pref_code}")
            return shops_from_json(text) if status < 400 else []

        lists = await asyncio.gather(*(shops_of(c) for c, _ in prefs), return_exceptions=True)
        shops = [s for r in lists if isinstance(r, list) for s in r]
        if not shops:
            res.message = "指定地域に未来屋書店の店舗が見つかりません"
            return []
        out: list[StoreStock] = []
        for i in range(0, len(shops), STOCK_CHUNK):
            chunk = shops[i:i + STOCK_CHUNK]
            codes = ",".join(str(s["shop_code"]) for s in chunk) + ","
            status, text = await self.fetch(session, f"{BASE}/stockAPI/?isbn={isbn}&storecodes={codes}")
            if status < 400:
                out += merge(chunk, text)
        return out
