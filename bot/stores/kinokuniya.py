"""紀伊國屋書店：商品ページ（/f/dsg-01-{isbn13}）には店舗在庫の表は無く、「店の在庫を確認・取置」ボタンから
  CKnSfStockSearchStoreEncrypt_001.jsp?CAT=01&GOODS_STK_NO={isbn13}  → 店舗選択ページ（都道府県ごとの店舗とボタン）
  CKnSfStockSearchStoreEncrypt_002.jsp に 1 店ずつ POST            → その店の在庫（○ 在庫あり / △ 在庫僅少 / × …）
と進む。店舗が 70 以上あるので、地域・店舗指定があるときだけ該当店に問い合わせる。
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from .base import AREA_ONLY_MSG, Checker, CheckResult, Status, StoreStock, classify

POST_URL = "https://www.kinokuniya.co.jp/disp/CKnSfStockSearchStoreEncrypt_002.jsp"
PARALLEL = 3
MAX_STORES = 12


def parse_store_select(html: str) -> list[tuple[str, str, str]]:
    """店舗選択ページ → [(都道府県, 店名, 店コード)]。該当商品なしなら空。"""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str, str]] = []
    pref = ""
    for el in soup.find_all(["h2", "ul"]):
        if el.name == "h2" and (el.get("id") or "").startswith("PREF"):
            img = el.find("img")
            pref = (img.get("alt") if img else "") or el.get_text(strip=True)
        elif el.name == "ul" and "list_detail2" in (el.get("class") or []):
            name = el.select_one("li.shop_name")
            btn = el.select_one('input[type=image][name^="ENTR_CD|"]')
            if name and btn:
                out.append((pref, name.get_text(strip=True), btn["name"].split("|", 1)[1]))
    return out


def post_url(html: str, page_url: str) -> str:
    """店舗選択フォームの送信先（無ければ既定の URL）。"""
    form = BeautifulSoup(html, "html.parser").select_one('form[action*="StockSearchStoreEncrypt_002"]')
    return urljoin(page_url, form["action"]) if form else POST_URL


def parse_stock_view(html: str) -> Optional[tuple[str, Status, str]]:
    """在庫表示ページ → (店名, 在庫, 表記)。"""
    soup = BeautifulSoup(html, "html.parser")
    for box in soup.select("div.list_parent2"):
        name = box.select_one(".list_h2 .shop_name")
        mark = box.select_one(".list_detail2 li.address b")
        if name and mark:
            text = " ".join(mark.get_text(" ", strip=True).split())   # &nbsp; を普通の空白に
            return name.get_text(strip=True), classify(text), text
    return None


class KinokuniyaChecker(Checker):
    timeout = aiohttp.ClientTimeout(total=9)

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        stores = parse_store_select(html)
        if not stores:
            res.message = "紀伊國屋の店舗在庫検索に該当商品がありません"
            return []
        wanted = self.cfg.stores + self.keywords
        if not wanted:
            res.message = AREA_ONLY_MSG + "。リンク先で店を選べます"
            return []
        stores = [s for s in stores if any(w in s[0] or w in s[1] for w in wanted)][:MAX_STORES]
        if not stores:
            res.message = "指定地域に紀伊國屋書店の店舗がありません"
            return []
        isbn = code.isbn13 or code.jan or ""
        target = post_url(html, res.url)
        # フォームのあるページ（店舗選択）を Referer にしないと在庫表示が 404 になる
        referer = urljoin(target, "CKnSfStockSearchStoreSelect.jsp")
        sem = asyncio.Semaphore(PARALLEL)

        async def one(pref: str, cd: str) -> Optional[StoreStock]:
            data = {"CAT": "01", "GOODS_STK_NO": isbn, f"ENTR_CD|{cd}.x": "10", f"ENTR_CD|{cd}.y": "10",
                    f"MAN_ENTR_CD|{cd}": cd}
            async with sem:
                status, page = await self.fetch(session, target, data=data, headers={"Referer": referer})
            got = parse_stock_view(page) if status < 400 else None
            if not got or got[1] == Status.UNKNOWN:
                return None
            name, st, text = got
            return StoreStock(store=f"{name}（{pref}）", status=st, note=text)

        got = await asyncio.gather(*(one(p, cd) for p, _, cd in stores), return_exceptions=True)
        return [g for g in got if isinstance(g, StoreStock)]
