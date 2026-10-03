"""書誌情報（タイトル等）の取得と、B0… ASIN から JAN/ISBN への解決。"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass
from typing import Optional

import aiohttp

from .codes import Code, isbn13_to_10

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}

# Keepa の価格・ランキング推移グラフ（波形）。Discord は画像URLを自分で取りに行くが Keepa がそれをブロックするので、
# bot 側で PNG を取得して添付ファイルとして送る。KEEPA_API_KEY があれば公式 API の graphimage を使う。
GRAPH_FILENAME = "keepa.png"
KEEPA_GRAPH = ("https://graph.keepa.com/pricehistory.png?asin={asin}&domain=co.jp"
               "&width=800&height=300&range=365&salesrank=1&amazon=1&new=1&used=1")
KEEPA_API_GRAPH = ("https://api.keepa.com/graphimage?key={key}&domain=5&asin={asin}"
                   "&width=800&height=300&range=365&salesrank=1&amazon=1&new=1&used=1")
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """PNG の (幅, 高さ)。PNG でなければ (0, 0)。"""
    if len(data) < 24 or not data.startswith(_PNG_SIG):
        return 0, 0
    return struct.unpack(">II", data[16:24])


async def fetch_keepa_graph(session: aiohttp.ClientSession, asin: Optional[str]) -> Optional[bytes]:
    """Keepa のグラフ PNG を返す。ASIN が無い／取れない／ブロック画像（500x200 の案内）なら None。"""
    if not asin:
        return None
    key = os.environ.get("KEEPA_API_KEY", "").strip()
    url = KEEPA_API_GRAPH.format(key=key, asin=asin) if key else KEEPA_GRAPH.format(asin=asin)
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status != 200 or "image/png" not in (r.headers.get("Content-Type") or ""):
                return None
            data = await r.read()
    except Exception:  # noqa: BLE001
        return None
    w, _ = png_size(data)
    return data if w >= 700 else None


@dataclass
class AmazonInfo:
    """Amazon 商品ページから拾った価格。取れなかった項目は空文字。"""
    asin: str
    price: str = ""          # 新品（Amazon / カート）の価格表示（例: ￥693）
    other_price: str = ""    # 中古の最安（SP-API なら送料込み。ページ読みなら「その他中古品… が ￥318から」）
    availability: str = ""   # 在庫あり / 一時的に在庫切れ など
    fetched: bool = False    # 取れたか（False なら Amazon に拒否された等）
    new_count: int = 0       # SP-API: 新品の出品数
    used_count: int = 0      # SP-API: 中古の出品数
    fba_new: str = ""        # SP-API: FBA 新品の最安
    source: str = ""         # "spapi" / "page"
    title: str = ""          # SP-API: Amazon 上の商品名（openBD に無い本の補完用）
    image: str = ""          # SP-API: 商品画像 URL（書影の補完用）

    @property
    def url(self) -> str:
        return f"https://www.amazon.co.jp/dp/{self.asin}"

    @property
    def keepa_url(self) -> str:
        return f"https://keepa.com/#!product/5-{self.asin}"


@dataclass
class BookMeta:
    title: str = ""
    author: str = ""
    publisher: str = ""
    price: str = ""
    cover: str = ""
    source: str = ""
    amazon: Optional["AmazonInfo"] = None


async def fetch_meta(session: aiohttp.ClientSession, code: Code) -> BookMeta:
    """openBD → Google Books の順で書誌を引く。失敗時は空の BookMeta。"""
    isbn = code.isbn13
    if not isbn:
        return BookMeta()
    try:
        async with session.get(f"https://api.openbd.jp/v1/get?isbn={isbn}", headers=HEADERS,
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                if data and data[0]:
                    s = data[0].get("summary", {})
                    return BookMeta(title=s.get("title", ""), author=s.get("author", ""),
                                    publisher=s.get("publisher", ""), cover=s.get("cover", ""),
                                    price=_openbd_price(data[0]), source="openBD")
    except Exception:
        pass
    try:
        async with session.get(f"https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}",
                               headers=HEADERS, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                items = data.get("items") or []
                if items:
                    v = items[0].get("volumeInfo", {})
                    return BookMeta(title=v.get("title", ""), author="、".join(v.get("authors", [])),
                                    publisher=v.get("publisher", ""),
                                    cover=(v.get("imageLinks") or {}).get("thumbnail", ""),
                                    source="Google Books")
    except Exception:
        pass
    return BookMeta()


def _openbd_price(rec: dict) -> str:
    try:
        prices = rec["onix"]["ProductSupply"]["SupplyDetail"]["Price"]
        for p in prices:
            if p.get("PriceAmount"):
                return f"¥{int(float(p['PriceAmount'])):,}（税抜）"
    except Exception:
        pass
    return ""


_ISBN13_RE = re.compile(r"ISBN[-‐]?13\D{0,20}(97[89][\d\-‐]{10,16})", re.I)
_JAN_RE = re.compile(r"(?:JAN|EAN|GTIN)\D{0,20}(\d{13})", re.I)


_PRICE_TXT = re.compile(r"[￥¥]\s*[\d,]+|USD\s*[\d.,]+|[\d,]+\s*円")
_OTHER = re.compile(r"(?:中古品|コレクター商品)[^\d￥¥]{0,20}([￥¥]\s*[\d,]+|USD\s*[\d.,]+)\s*から")


def parse_amazon(html: str, asin: str) -> AmazonInfo:
    """商品ページの HTML から新品価格・中古の最安・在庫表示を拾う。構造が変わっても落ちないように緩く取る。"""
    from bs4 import BeautifulSoup   # noqa: PLC0415  （lookup は軽く保つため関数内 import）
    soup = BeautifulSoup(html, "html.parser")
    info = AmazonInfo(asin=asin, fetched=True)
    for sel in ("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen", "#corePrice_feature_div .a-price .a-offscreen",
                "#buybox .a-price .a-offscreen", "#price", "#priceblock_ourprice", ".a-price .a-offscreen"):
        el = soup.select_one(sel)
        if el and _PRICE_TXT.search(el.get_text(" ", strip=True)):
            info.price = re.sub(r"\s+", "", el.get_text(" ", strip=True)).replace("USD", "USD ")
            break
    for el in soup.select(".olp-link, #olp_feature_div, #olpLinkWidget_feature_div, [data-feature-name=olp]"):
        m = _OTHER.search(el.get_text(" ", strip=True).replace("\xa0", " "))
        if m:
            info.other_price = re.sub(r"\s+", "", m.group(1)).replace("USD", "USD ")
            break
    av = soup.select_one("#availability")
    if av:
        info.availability = re.sub(r"\s+", " ", av.get_text(" ", strip=True))[:30]
    # 書名・商品画像（openBD に無い本や雑誌 JAN の補完用）
    t = soup.select_one("#productTitle")
    if t:
        info.title = re.sub(r"\s+", " ", t.get_text(" ", strip=True))[:120]
    img = soup.select_one("#landingImage, #imgBlkFront, #ebooksImgBlkFront")
    if img:
        info.image = img.get("data-old-hires") or img.get("src") or ""
    return info


def merge_amazon(meta: "BookMeta", amazon: Optional[AmazonInfo]) -> "BookMeta":
    """Amazon の情報を書誌に合流させる。openBD / Google Books で取れなかった書名・書影は Amazon のもので補う。"""
    meta.amazon = amazon
    if amazon:
        if not meta.title and amazon.title:
            meta.title = amazon.title
            meta.source = meta.source or "Amazon"
        if not meta.cover and amazon.image:
            meta.cover = amazon.image
    return meta


def _is_amazon_block(html: str) -> bool:
    """ロボット確認ページか（商品ページの JS にも "captcha" の語はあるので、確認ページ特有の印で判定）。"""
    return ('id="captchacharacters"' in html or "Amazon CAPTCHA" in html or "ロボットではないことを確認" in html
            or "/errors_page/validateCaptcha" in html or "<title>Amazon.co.jp</title>" in html)


async def _fetch_amazon_curl(url: str) -> str:
    """aiohttp だと Amazon が確認ページを返すことがあるので、OS の curl（Windows 10 以降と Mac に標準）でも試す。
    curl が無ければ空文字。"""
    import asyncio   # noqa: PLC0415
    try:
        proc = await asyncio.create_subprocess_exec(
            "curl", "-sSL", "--compressed", "--max-time", "20", "-A", UA, "-H", "Accept-Language: ja,en;q=0.8", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
    except Exception:  # noqa: BLE001
        return ""
    return out.decode("utf-8", errors="replace")


async def fetch_amazon(session: aiohttp.ClientSession, asin: Optional[str]) -> Optional[AmazonInfo]:
    """Amazon の価格。SP-API のキーがあれば SP-API（確実）、無ければ商品ページを読む。
    ASIN が無ければ None、取れなければ fetched=False の AmazonInfo。
    確認ページ（ロボット判定）が返ったときは突破せず、curl で 1 回だけ取り直す。"""
    if not asin:
        return None
    from . import spapi   # noqa: PLC0415
    if spapi.configured():
        try:
            d = await spapi.fetch_prices(session, asin)
            return AmazonInfo(asin=asin, price=d["price"], other_price=d["other_price"], fetched=True,
                              new_count=d["new_count"], used_count=d["used_count"], fba_new=d["fba_new"], source="spapi",
                              title=d.get("title", ""), image=d.get("image", ""))
        except Exception as e:  # noqa: BLE001
            import logging   # noqa: PLC0415
            logging.getLogger("zaikobot").warning("SP-API で価格を取れず、商品ページに切り替え: %s", e)
    url = f"https://www.amazon.co.jp/dp/{asin}"
    html = ""
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status == 200:
                html = await r.text(errors="replace")
    except Exception:  # noqa: BLE001
        pass
    if not html or _is_amazon_block(html):
        html = await _fetch_amazon_curl(url)
    if not html or _is_amazon_block(html):
        return AmazonInfo(asin=asin)
    info = parse_amazon(html, asin)
    info.source = "page"
    return info


async def resolve_asin(session: aiohttp.ClientSession, code: Code) -> Optional[str]:
    """B0… の ASIN について Amazon.co.jp の商品ページから ISBN-13 / JAN を拾う。見つからなければ None。
    Amazon はボット対策で 503 を返すことがあるため、あくまで best-effort。"""
    if not code.asin:
        return None
    url = f"https://www.amazon.co.jp/dp/{code.asin}"
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=12)) as r:
            if r.status != 200:
                code.notes.append(f"Amazon が {r.status} を返したため ASIN→JAN 変換できませんでした。")
                return None
            html = await r.text(errors="ignore")
    except Exception as e:
        code.notes.append(f"Amazon に接続できませんでした（{type(e).__name__}）。")
        return None

    m = _ISBN13_RE.search(html)
    if m:
        isbn = re.sub(r"\D", "", m.group(1))
        if len(isbn) == 13:
            code.isbn13 = code.jan = isbn
            code.isbn10 = isbn13_to_10(isbn)
            return isbn
    m = _JAN_RE.search(html)
    if m:
        code.jan = m.group(1)
        return code.jan
    code.notes.append("Amazon ページに ISBN/JAN が見つかりませんでした。JAN を直接入力してください。")
    return None
