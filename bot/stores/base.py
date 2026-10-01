"""書店チェッカーの共通部品。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from ..codes import Code
from ..lookup import HEADERS


class Status(str, Enum):
    IN_STOCK = "in_stock"
    LOW = "low"
    OUT = "out"
    UNKNOWN = "unknown"   # ページは取れたが在庫情報を読み取れず（リンクで確認）
    ERROR = "error"       # 取得失敗
    LINK = "link"         # リンクのみ（自動取得しない店）

    @property
    def emoji(self) -> str:
        return {
            Status.IN_STOCK: "🟢", Status.LOW: "🟡", Status.OUT: "🔴",
            Status.UNKNOWN: "⚪", Status.ERROR: "⚠️", Status.LINK: "🔗",
        }[self]

    @property
    def text(self) -> str:
        return {
            Status.IN_STOCK: "在庫あり", Status.LOW: "在庫わずか", Status.OUT: "在庫なし",
            Status.UNKNOWN: "要確認", Status.ERROR: "取得失敗", Status.LINK: "リンク",
        }[self]

    @property
    def rank(self) -> int:
        return [Status.IN_STOCK, Status.LOW, Status.UNKNOWN, Status.LINK, Status.ERROR, Status.OUT].index(self)


@dataclass
class StoreStock:
    store: str
    status: Status
    note: str = ""


@dataclass
class CheckResult:
    chain_id: str
    chain: str
    url: str
    status: Status = Status.UNKNOWN
    stocks: list[StoreStock] = field(default_factory=list)
    message: str = ""
    verified: bool = True

    def summarize(self) -> None:
        if self.stocks:
            self.status = min((s.status for s in self.stocks), key=lambda s: s.rank)


@dataclass
class StoreConfig:
    id: str
    name: str
    search: str
    checker: str = "generic"
    enabled: bool = True
    verified: bool = True
    stores: list[str] = field(default_factory=list)   # 店名フィルタ（部分一致）。空なら全店
    note: str = ""
    home: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "StoreConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# --- 在庫表現の正規化 ---------------------------------------------------------
_IN = re.compile(r"在庫あり|在庫有り|在庫有|在庫◯|在庫○|◎|○|〇|◯|在庫:あり|在庫：あり|店頭在庫あり|在庫数\s*[1-9]|残り\s*[1-9]\d*|あります")
_LOW = re.compile(r"在庫わずか|在庫僅少|在庫少|残りわずか|わずか|僅少|△|▲|残り\s*[1-3]\s*[点冊]")
_OUT = re.compile(r"在庫なし|在庫無し|在庫無|品切れ|品切|欠品|×|✕|取り寄せ|お取寄せ|お取り寄せ|入荷待ち|販売終了|在庫:なし|在庫：なし|ありません")
_STORE_LINE = re.compile(
    r"([^\s　|｜:：,、。\[\]()（）]{2,40}?(?:店|書店|ブックセンター|BOOK\s*STORE|BOOKS|センター|蔦屋書店|TSUTAYA[^\s]{0,20}))"
    r"[\s　|｜:：]*"
    r"(在庫あり|在庫有り|在庫わずか|在庫僅少|在庫少|残りわずか|在庫なし|在庫無し|品切れ|欠品|お取り寄せ|取り寄せ|入荷待ち|在庫数\s*\d+|残り\s*\d+\s*[点冊]|[○◯〇◎△▲×✕])",
    re.I,
)


def classify(text: str) -> Status:
    t = text.strip()
    if _LOW.search(t):
        return Status.LOW
    if _OUT.search(t):
        return Status.OUT
    if _IN.search(t):
        return Status.IN_STOCK
    return Status.UNKNOWN


def scan_text_for_stocks(text: str) -> list[StoreStock]:
    """ページ全文から「店名 … 在庫状況」の並びを拾う汎用パーサ。サイト固有パーサが無い店のフォールバック。"""
    out: list[StoreStock] = []
    seen: set[str] = set()
    flat = re.sub(r"[ \t　]+", " ", text)
    for m in _STORE_LINE.finditer(flat):
        store, st = m.group(1).strip(), m.group(2)
        if store in seen or len(store) < 2:
            continue
        status = classify(st)
        if status == Status.UNKNOWN:
            continue
        seen.add(store)
        out.append(StoreStock(store=store, status=status, note=st.strip()))
    return out


def filter_stores(stocks: list[StoreStock], wanted: list[str], area: Optional[str]) -> list[StoreStock]:
    res = stocks
    if wanted:
        res = [s for s in res if any(w in s.store for w in wanted)]
    if area:
        res = [s for s in res if area in s.store or area in s.note]
    return res


class Checker:
    """1チェーン分の在庫チェック。サブクラスは `parse` または `check` を上書きする。"""

    timeout = aiohttp.ClientTimeout(total=15)

    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg

    def url_for(self, code: Code) -> str:
        return code.fill(self.cfg.search)

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> tuple[int, str]:
        async with session.get(url, headers=HEADERS, timeout=self.timeout, allow_redirects=True) as r:
            return r.status, await r.text(errors="ignore")

    async def check(self, session: aiohttp.ClientSession, code: Code, area: Optional[str] = None) -> CheckResult:
        url = self.url_for(code)
        res = CheckResult(chain_id=self.cfg.id, chain=self.cfg.name, url=url or self.cfg.home,
                          verified=self.cfg.verified)
        if not url:
            res.status, res.message = Status.LINK, "この店はこのコード種別では検索URLを作れません"
            return res
        try:
            status, html = await self.fetch(session, url)
        except Exception as e:  # noqa: BLE001
            res.status, res.message = Status.ERROR, f"接続失敗（{type(e).__name__}）"
            return res
        if status >= 400:
            res.status, res.message = Status.ERROR, f"HTTP {status}"
            return res
        try:
            stocks = await self.parse(session, code, html, res)
        except Exception as e:  # noqa: BLE001
            res.status, res.message = Status.UNKNOWN, f"解析失敗（{type(e).__name__}）"
            return res
        res.stocks = filter_stores(stocks, self.cfg.stores, area)
        if res.stocks:
            res.summarize()
        elif stocks:
            res.status, res.message = Status.OUT, "指定店舗・エリアに該当なし（他店には在庫表示あり）"
        else:
            res.status = Status.UNKNOWN
            res.message = res.message or "在庫表示を読み取れませんでした。リンクで確認してください"
        return res

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        return scan_text_for_stocks(soup.get_text("\n"))
