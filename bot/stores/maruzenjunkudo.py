"""丸善ジュンク堂書店（www.maruzenjunkudo.co.jp ＝ Shopify）
商品ページ /products/{isbn13} の『全店舗の在庫一覧』（/pages/shoplist）は Cloudflare のチャレンジ付きで開けないが、
同じページの JS（My店舗在庫）が呼んでいる公開 API はそのまま応答する：
  https://api-item-info-ttmpxhtqra-an.a.run.app?access_key=…&jan_isbn={isbn}&type=2
      → [{store_code, stock_quantity, …}]  全店ぶん（丸善・ジュンク堂以外のコードも混ざる）
  https://api-shopify-listLocations-ttmpxhtqra-an.a.run.app
      → Shopify の店舗一覧（店名・住所・metafield の店コード／在庫検索可否）
判定はページの JS と同じ（4以上=あり / 1〜3=わずか / 0以下=なし）。店舗一覧に無いコード（文教堂・戸田書店など）は捨てる。
honto.jp の紙の本ストア（旧 pd-store_{id}.html）は丸善ジュンク堂ネットストアに移管され電子書籍のみになったので使えない。
"""
from __future__ import annotations

import asyncio
import json
import unicodedata

import aiohttp

from ..codes import Code
from .base import PREFECTURES, Checker, CheckResult, Status, StoreStock, short_area

ITEM_INFO = "https://api-item-info-ttmpxhtqra-an.a.run.app"
LOCATIONS = "https://api-shopify-listLocations-ttmpxhtqra-an.a.run.app"
ACCESS_KEY = "Ydoa7ygeyUMDGRtDqEvpGGzNRYco6XK4"   # 商品ページの JS に埋め込まれている公開キー（サーバー側で検証はされていない）
_LOC_CACHE: dict[str, tuple[str, str]] = {}

# Shopify の province はローマ字（Tōkyō / Ōsaka …）。マクロンを落として JIS 順の都道府県に対応させる
_ROMAJI = ["hokkaido", "aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima", "ibaraki", "tochigi", "gunma",
           "saitama", "chiba", "tokyo", "kanagawa", "niigata", "toyama", "ishikawa", "fukui", "yamanashi", "nagano",
           "gifu", "shizuoka", "aichi", "mie", "shiga", "kyoto", "osaka", "hyogo", "nara", "wakayama",
           "tottori", "shimane", "okayama", "hiroshima", "yamaguchi", "tokushima", "kagawa", "ehime", "kochi", "fukuoka",
           "saga", "nagasaki", "kumamoto", "oita", "miyazaki", "kagoshima", "okinawa"]
_PREF_BY_ROMAJI = dict(zip(_ROMAJI, PREFECTURES))


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower().strip()


def stock_url(isbn: str) -> str:
    return f"{ITEM_INFO}?access_key={ACCESS_KEY}&jan_isbn={isbn}&type=2"


def parse_locations(text: str) -> dict[str, tuple[str, str]]:
    """listLocations → {店コード: (店名, 地域ラベル)}。在庫検索非対応の店は除く。"""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for edge in (data.get("locations") or {}).get("edges") or []:
        node = edge.get("node") or {}
        mf = {m["node"]["key"]: m["node"].get("value") for m in (node.get("metafields") or {}).get("edges") or []
              if isinstance(m.get("node"), dict) and "key" in m["node"]}
        code = str(mf.get("code") or "")
        if not code or mf.get("store_inventory_search_availability") == "0":
            continue
        addr = node.get("address") or {}
        pref = _PREF_BY_ROMAJI.get(_ascii(addr.get("province") or ""), "")
        area = short_area(pref + (addr.get("city") or ""))
        out[code] = ((node.get("name") or "").strip(), area)
    return out


def stock_status(n: int) -> tuple[Status, str]:
    if n >= 4:
        return Status.IN_STOCK, f"在庫あり {n}点"
    if n >= 1:
        return Status.LOW, f"残り{n}点"
    return Status.OUT, "在庫なし"


def merge(locations: dict[str, tuple[str, str]], stock_text: str) -> list[StoreStock]:
    try:
        rows = json.loads(stock_text)
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    out: list[StoreStock] = []
    seen: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        code = str(r.get("store_code") or "")
        loc = locations.get(code)
        if not loc or code in seen:
            continue
        try:
            n = int(r.get("stock_quantity") or 0)
        except (TypeError, ValueError):
            continue
        seen.add(code)
        st, note = stock_status(n)
        name, area = loc
        out.append(StoreStock(store=f"{name}（{area}）" if area else name, status=st, note=note))
    return out


class MaruzenJunkudoChecker(Checker):
    needs_search_page = False   # 商品ページ（約 600KB）は取らず、表示用リンクにだけ使う

    async def _locations(self, session: aiohttp.ClientSession) -> dict[str, tuple[str, str]]:
        if not _LOC_CACHE:
            try:
                status, text = await self.fetch(session, LOCATIONS)
                if status < 400:
                    _LOC_CACHE.update(parse_locations(text))
            except Exception:  # noqa: BLE001  店舗一覧が取れないときは在庫だけでは店名が分からないので空
                pass
        return _LOC_CACHE

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        isbn = code.isbn13 or code.jan
        if not isbn:
            res.message = "ISBN/JAN の無いコードでは在庫を引けません"
            return []
        (status, text), locations = await asyncio.gather(self.fetch(session, stock_url(isbn)), self._locations(session))
        if status >= 400:
            res.message = f"在庫 API が HTTP {status}"
            return []
        if not locations:
            res.message = "店舗一覧 API を取得できませんでした"
            return []
        stocks = merge(locations, text)
        if not stocks:
            res.message = "丸善ジュンク堂の在庫 API に該当商品がありません"
        return stocks
