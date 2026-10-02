"""書店在庫情報プロジェクト（openBS：JPIC・カーリル・版元ドットコムの共同事業、info.openbs.jp）
版元ドットコムの書誌ページ /bd/isbn/{isbn13} の『書店の店頭在庫を確認』（demo.openbs.jp/{isbn}・hanmoto.openbs.jp の JS）は
  1. https://openbs-api-l3cmcn337q-an.a.run.app/recommend?lat=..&lon=..&limit=N
       → 位置から近い順の書店一覧（最大 200 店。systemid/libkey が入っている店だけ在庫連携あり、空の店は未連携）
  2. https://api.calil.jp/check?appkey=..&isbn=..&systemid=A,B,..&format=json&callback=no
       → カーリル蔵書検索 API（公開仕様 calil.jp/doc/api_ref.html）。図書館の代わりに書店の systemid（Shop_Kumazawa /
         Shop_Ogaki / Shop_Housendou / Shop_Tohan_{店コード} / Shop_Nippan_{店コード} …）を渡す。continue=1 のあいだ
         session で再問い合わせ。books[isbn][systemid].libkey = {店キー: 在庫あり|在庫わずか|在庫なし|注文可能|要連絡}
の順で取る。店キーは recommend の libkey と対応させる（くまざわ『アピタ名古屋北店』、トーハン系は店コード『028553』）。
連携店でも libkey に出ない店は在庫なし（demo.openbs.jp も outofstock 扱い）。status が Error/TimeOut の系統（2026-10 時点で
日販系 Shop_Nippan_* ＝ BOOKSえみたす・リブロ等は全店 Error）は『受信できず』として除く。
位置情報が必要なので、都道府県ごとに決めた代表点（県庁所在地、愛知は豊橋・岡崎・一宮も）から 200 店ずつ集める。全国は全点。
appkey は hanmoto.openbs.jp のページ JS に埋め込まれている公開キー。環境変数 CALIL_APPKEY で自分のキー（calil.jp で無料登録）に差し替えられる。
"""
from __future__ import annotations

import asyncio
import json
import os
import unicodedata

import aiohttp

from ..codes import Code
from .base import PREFECTURES, Checker, CheckResult, Status, StoreStock, pref_short, prefs_in_keywords, short_area

RECOMMEND = "https://openbs-api-l3cmcn337q-an.a.run.app/recommend"
CALIL_CHECK = "https://api.calil.jp/check"
DEFAULT_APPKEY = "fee76a993f32b475049d9a0232e241b4"   # hanmoto.openbs.jp/assets/index.js に埋め込まれている公開キー
LIMIT = 200          # recommend の上限（500 を渡しても 200 で頭打ち）
PARALLEL = 4
POLL_INTERVAL = 1.5  # calil のポーリング間隔（秒）。ページ JS は 1 秒
MAX_POLLS = 20       # 約 30 秒で打ち切り（愛知4点＋京都の 30 系統で 1 回のポーリングで完了した）
_NEARBY_CACHE: dict[tuple[float, float], list[dict]] = {}

# 都道府県ごとの代表点（緯度, 経度）。recommend は近い順 200 店までなので、広い県は複数点
_CAPITALS = [(43.0642, 141.3469), (40.8244, 140.7400), (39.7036, 141.1527), (38.2688, 140.8721), (39.7186, 140.1024),
             (38.2404, 140.3633), (37.7503, 140.4676), (36.3418, 140.4468), (36.5657, 139.8836), (36.3911, 139.0608),
             (35.8570, 139.6489), (35.6047, 140.1233), (35.6895, 139.6917), (35.4478, 139.6425), (37.9026, 139.0232),
             (36.6953, 137.2113), (36.5947, 136.6256), (36.0652, 136.2216), (35.6642, 138.5684), (36.6513, 138.1810),
             (35.3912, 136.7223), (34.9769, 138.3831), (35.1802, 136.9066), (34.7303, 136.5086), (35.0045, 135.8686),
             (35.0212, 135.7556), (34.6863, 135.5200), (34.6913, 135.1830), (34.6851, 135.8329), (34.2260, 135.1675),
             (35.5039, 134.2377), (35.4723, 133.0505), (34.6618, 133.9344), (34.3966, 132.4596), (34.1859, 131.4714),
             (34.0658, 134.5593), (34.3401, 134.0434), (33.8416, 132.7657), (33.5597, 133.5311), (33.6064, 130.4183),
             (33.2494, 130.2988), (32.7448, 129.8737), (32.7898, 130.7417), (33.2382, 131.6126), (31.9111, 131.4239),
             (31.5602, 130.5581), (26.2124, 127.6809)]
_EXTRA = {"北海道": [(43.7706, 142.3650), (41.7687, 140.7288)],                      # 旭川・函館
          "愛知県": [(34.7692, 137.3915), (34.9540, 137.1740), (35.3040, 136.8030)],  # 豊橋・岡崎・一宮
          "京都府": [(35.2963, 135.1265)],                                             # 福知山
          "静岡県": [(34.7108, 137.7261)],                                             # 浜松
          "兵庫県": [(34.8151, 134.6855)],                                             # 姫路
          "福岡県": [(33.8835, 130.8752)]}                                             # 北九州
POINTS: dict[str, list[tuple[float, float]]] = {p: [c] + _EXTRA.get(p, []) for p, c in zip(PREFECTURES, _CAPITALS)}

_STATUS = {"在庫あり": (Status.IN_STOCK, "在庫あり"), "在庫わずか": (Status.LOW, "在庫わずか"),
           "在庫なし": (Status.OUT, "在庫なし"), "注文可能": (Status.OUT, "店頭在庫なし（注文可能）"),
           "要連絡": (Status.UNKNOWN, "要連絡")}


def appkey() -> str:
    return os.environ.get("CALIL_APPKEY") or DEFAULT_APPKEY


def recommend_url(lat: float, lon: float) -> str:
    return f"{RECOMMEND}?lat={lat}&lon={lon}&limit={LIMIT}"


def check_url(isbn: str, systemids: list[str]) -> str:
    return f"{CALIL_CHECK}?appkey={appkey()}&isbn={isbn}&systemid={','.join(systemids)}&format=json&callback=no"


def poll_url(session_id: str) -> str:
    return f"{CALIL_CHECK}?appkey={appkey()}&session={session_id}&format=json&callback=no"


def points_for(keywords: list[str]) -> list[tuple[float, float]]:
    """地域キーワードに都道府県があればその代表点、無ければ（全国・市名だけ）全点。"""
    prefs = [p for _, p in prefs_in_keywords(keywords)] or PREFECTURES
    out: list[tuple[float, float]] = []
    for p in prefs:
        out += [pt for pt in POINTS[p] if pt not in out]
    return out


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


def linked_stores(text: str) -> list[dict]:
    """recommend の応答 → 在庫連携のある店 [{id, name, systemid, libkey, area}]。未連携（systemid 空）は捨てる。"""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out: list[dict] = []
    for s in (data.get("nearby") or []) if isinstance(data, dict) else []:
        if not isinstance(s, dict) or not s.get("systemid") or not s.get("libkey"):
            continue
        out.append({"id": str(s.get("id") or ""), "name": _nfkc(s.get("name")), "systemid": s["systemid"],
                    "libkey": _nfkc(s["libkey"]), "area": short_area(_nfkc(s.get("address")))})
    return out


def merge(stores: list[dict], check_text: str, isbn: str) -> tuple[list[StoreStock], int]:
    """calil check の最終応答と店一覧を突き合わせる。戻りは (在庫行, 受信できなかった店数)。"""
    try:
        data = json.loads(check_text)
    except ValueError:
        return [], len(stores)
    books = (data.get("books") or {}).get(isbn) or {} if isinstance(data, dict) else {}
    out: list[StoreStock] = []
    failed = 0
    for s in stores:
        r = books.get(s["systemid"])
        if not isinstance(r, dict) or r.get("status") not in ("OK", "Cache"):
            failed += 1   # Error / TimeOut / Running（打ち切り）/ 系統ごと無い
            continue
        libkey = {_nfkc(k): v for k, v in (r.get("libkey") or {}).items()}   # 店キーも全角混じり（ＴＯＵＴＥＮ）
        mark = libkey.get(s["libkey"])
        st, note = _STATUS.get(mark, (Status.OUT, "在庫なし")) if mark else (Status.OUT, "在庫なし")
        if mark and mark not in _STATUS:
            st, note = Status.UNKNOWN, mark[:30]
        out.append(StoreStock(store=f"{s['name']}（{s['area']}）" if s["area"] else s["name"], status=st, note=note))
    return out, failed


class OpenBSChecker(Checker):
    needs_search_page = False   # 版元ドットコムの書誌ページは表示用リンクにだけ使う

    async def _nearby(self, session: aiohttp.ClientSession, pt: tuple[float, float], sem: asyncio.Semaphore) -> list[dict]:
        if pt not in _NEARBY_CACHE:
            async with sem:
                status, text = await self.fetch(session, recommend_url(*pt))
            if status >= 400:
                return []
            _NEARBY_CACHE[pt] = linked_stores(text)
        return _NEARBY_CACHE[pt]

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        isbn = code.isbn13
        if not isbn:
            res.message = "書籍（ISBN）のみ検索できます"
            return []
        sem = asyncio.Semaphore(PARALLEL)
        lists = await asyncio.gather(*(self._nearby(session, pt, sem) for pt in points_for(self.keywords)),
                                     return_exceptions=True)
        stores: dict[str, dict] = {}
        for lst in lists:
            for s in (lst if isinstance(lst, list) else []):
                stores.setdefault(s["id"] or f"{s['systemid']}:{s['libkey']}", s)
        if not stores:
            res.message = "書店在庫情報プロジェクトの近隣書店一覧を取得できませんでした"
            return []
        systemids = sorted({s["systemid"] for s in stores.values()})
        status, text = await self.fetch(session, check_url(isbn, systemids))
        if status >= 400:
            res.message = f"カーリル蔵書検索 API が HTTP {status}"
            return []
        polls = 0
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        while isinstance(data, dict) and data.get("continue") == 1 and data.get("session") and polls < MAX_POLLS:
            polls += 1
            await asyncio.sleep(POLL_INTERVAL)
            status, text = await self.fetch(session, poll_url(data["session"]))
            if status >= 400:
                break
            try:
                data = json.loads(text)
            except ValueError:
                break
        stocks, failed = merge(list(stores.values()), text, isbn)
        res.group_brands = True      # くまざわ・えみたす・らくだ … が混ざるので表示は系列ごとにまとめる
        if failed:
            res.message = f"{failed} 店は在庫情報を受信できず（日販系など連携エラーの系統）"
        return stocks
