"""有隣堂（search.yurindo.bscentral.jp ＝ Bookstore Central の在庫検索 SPA）
ページ /item?ic={isbn13} の JS は同じホストの /api/search-public/* に JSON を POST する。送受信の「難読化」は
  送信: JSON → フォーム URL エンコード → 各文字の文字コード +6
  受信: 各文字の文字コード -6 → URL デコード → JSON
だけ（署名やトークンは無い）なので同じ手順で読む。
  POST /api/search-public/stores      body: null
      → 店舗一覧 [{id, code, name, address, prefecture, area, …}]
  POST /api/search-public/items/_get  body: {"q": enc('{"code":"<isbn>","code_seq":0}')}
      → 商品 {name, store_data_list: [{store_id, stock_quantity, location_name, …}]}（該当なしは空）
判定はページと同じ（4以上=あり / 1〜3=わずか / 0以下=なし）。store_data_list に無い店は 0 ＝ なし。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from urllib.parse import quote_plus, unquote_plus

import aiohttp

from ..codes import Code
from .base import Checker, CheckResult, Status, StoreStock, short_area

API = "https://search.yurindo.bscentral.jp/api"
SHIFT = 6
JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
                "Origin": "https://search.yurindo.bscentral.jp"}
_STORE_CACHE: list[dict] = []


def encode(text: str, shift: int = SHIFT) -> str:
    """ページの ct(): URLSearchParams でエンコードしてから文字コードを +shift。"""
    return "".join(chr(ord(c) + shift) for c in quote_plus(text, safe="*"))


def decode(text: str, shift: int = SHIFT) -> str:
    """ページの j(): 文字コードを -shift してから URL デコード。"""
    return unquote_plus("".join(chr(ord(c) - shift) for c in text))


def query(params: dict) -> str:
    """POST ボディ。JSON.stringify と同じく空白なし・非 ASCII そのまま。"""
    return json.dumps({"q": encode(json.dumps(params, ensure_ascii=False, separators=(",", ":")))})


def decode_response(body: str) -> Optional[Any]:
    """API の応答（JSON 文字列の中に難読化テキスト）→ Python オブジェクト。読めない／該当なしは None。"""
    try:
        val = json.loads(body)
        if isinstance(val, str):
            val = json.loads(decode(val))
        return val
    except (ValueError, TypeError):
        return None


def stock_status(n: int) -> tuple[Status, str]:
    if n >= 4:
        return Status.IN_STOCK, f"在庫あり {n}点"
    if n >= 1:
        return Status.LOW, f"残り{n}点"
    return Status.OUT, "在庫なし"


def merge(stores: list[dict], item: dict) -> list[StoreStock]:
    data = {d.get("store_id"): d for d in item.get("store_data_list") or [] if isinstance(d, dict)}
    out: list[StoreStock] = []
    for s in stores:
        d = data.get(s.get("id")) or {}
        try:
            n = int(d.get("stock_quantity") or 0)
        except (TypeError, ValueError):
            n = 0
        st, note = stock_status(n)
        loc = (d.get("location_name") or "").strip()
        if n > 0 and loc and loc != "-":
            note = f"{note} / {loc}"
        area = short_area(s.get("address") or "") or (s.get("prefecture") or "")
        name = (s.get("name") or "").strip()
        out.append(StoreStock(store=f"{name}（{area}）" if area else name, status=st, note=note[:30]))
    return out


class YurindoChecker(Checker):
    needs_search_page = False   # SPA のページは空の殻なので取らない

    async def _stores(self, session: aiohttp.ClientSession) -> list[dict]:
        if not _STORE_CACHE:
            try:
                status, body = await self.fetch(session, f"{API}/search-public/stores", data="null", headers=JSON_HEADERS)
                stores = decode_response(body) if status < 400 else None
                if isinstance(stores, list):
                    _STORE_CACHE.extend(s for s in stores if isinstance(s, dict) and s.get("id") is not None)
            except Exception:  # noqa: BLE001
                pass
        return _STORE_CACHE

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        isbn = code.isbn13 or code.jan
        if not isbn:
            res.message = "ISBN/JAN の無いコードでは在庫を引けません"
            return []
        (status, body), stores = await asyncio.gather(
            self.fetch(session, f"{API}/search-public/items/_get", data=query({"code": isbn, "code_seq": 0}),
                       headers=JSON_HEADERS),
            self._stores(session))
        if status >= 400:
            res.message = f"在庫 API が HTTP {status}"
            return []
        item = decode_response(body)
        if not isinstance(item, dict) or not item:
            res.message = "有隣堂の在庫検索に該当商品がありません"
            return []
        if not stores:
            res.message = "店舗一覧 API を取得できませんでした"
            return []
        return merge(stores, item)
