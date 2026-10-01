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
        return _EMOJI[self]

    @property
    def text(self) -> str:
        return _TEXT[self]

    @property
    def rank(self) -> int:
        return _RANK[self]


_EMOJI = {Status.IN_STOCK: "🟢", Status.LOW: "🟡", Status.OUT: "🔴",
          Status.UNKNOWN: "⚪", Status.ERROR: "⚠️", Status.LINK: "🔗"}
_TEXT = {Status.IN_STOCK: "在庫あり", Status.LOW: "在庫わずか", Status.OUT: "在庫なし",
         Status.UNKNOWN: "要確認", Status.ERROR: "取得失敗", Status.LINK: "リンク"}
_RANK = {s: i for i, s in enumerate([Status.IN_STOCK, Status.LOW, Status.UNKNOWN,
                                     Status.LINK, Status.ERROR, Status.OUT])}


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
    search_alt: str = ""                              # search が埋められない／404 のときの代替URL
    prefectures: list[str] = field(default_factory=list)  # 出店地域（["全国"] or 県名）。地域検索の表示判定に使う

    def serves(self, keywords: list[str]) -> bool:
        """地域キーワードのどれかに出店しているか（prefectures 未設定なら不明＝True）。"""
        if not self.prefectures or "全国" in self.prefectures:
            return True
        return any(any(k in p or p in k for p in self.prefectures) for k in keywords)

    @classmethod
    def from_dict(cls, d: dict) -> "StoreConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# --- 在庫表現の正規化 ---------------------------------------------------------
# 判定順は LOW → OUT → IN。「在庫わずか」は IN の「在庫」系に先に食われないように、
# 「お取り寄せできます」は IN の「あります」に食われないようにしている。
_LOW = re.compile(r"在庫わずか|在庫僅少|在庫少|残りわずか|残り少|わずか|僅少|[△▲]|残り\s*[1-3]\s*[点冊]|在庫数\s*[1-3](?!\d)")
_OUT = re.compile(r"在庫なし|在庫無し|在庫無(?!料)|品切れ|品切|欠品|[×✕✖]|取り?寄せ|入荷待ち|販売終了|在庫[:：]\s*なし"
                  r"|ありません|在庫数\s*0(?!\d)|残り\s*0\s*[点冊]")
_IN = re.compile(r"在庫あり|在庫有り|在庫有(?!料)|店頭在庫あり|在庫[:：]\s*あり|在庫数\s*[1-9]\d*|残り\s*[1-9]\d*\s*[点冊]?"
                 r"|[○◯〇◎]|あります")
# 行の中で「在庫状況」とみなす語（店名との区切りに使う）
_STATUS_TOKEN = re.compile(
    r"在庫あり|在庫有り|在庫有(?!料)|店頭在庫あり|在庫わずか|在庫僅少|在庫少|残りわずか|残り少|在庫なし|在庫無し|在庫無(?!料)"
    r"|品切れ|品切|欠品|お取り?寄せ|取り?寄せ|入荷待ち|販売終了|在庫[:：]\s*(?:あり|なし)|在庫数\s*\d+|残り\s*\d+\s*[点冊]?"
    r"|(?<![\w○◯〇◎△▲×✕✖])[○◯〇◎△▲×✕✖](?![\w○◯〇◎△▲×✕✖])")
# 店名らしさ：書店系の語を含み、見出し語（店舗名/在庫状況 など）ではない
_NAME_HINT = re.compile(r"店|書店|ブック|BOOK|センター|TSUTAYA|蔦屋|堂|屋|BOOKOFF|ブックオフ", re.I)
_NAME_BAD = re.compile(r"^(?:店舗名?|店名|在庫(?:状況|数)?|状況|店舗在庫|取扱店舗|書店名|地域|エリア|都道府県)$")
_TRIM = " \t　:：|｜・-－—→>＞[]【】()（）"


def classify(text: str) -> Status:
    t = text.strip()
    if _LOW.search(t):
        return Status.LOW
    if _OUT.search(t):
        return Status.OUT
    if _IN.search(t):
        return Status.IN_STOCK
    return Status.UNKNOWN


def _clean_name(name: str) -> str:
    name = re.sub(r"[\s　]+", " ", name).strip(_TRIM)
    # 「店舗名 新宿本店」のような見出し混入を落とす
    parts = [p for p in name.split(" ") if p and not _NAME_BAD.match(p)]
    name = " ".join(parts)
    if len(name) > 40:
        name = name[-40:]
    return name


def stock_from_line(line: str, prev_line: str = "") -> Optional[StoreStock]:
    """「店名 … 在庫表記」の1行（または直前行が店名で当該行が在庫表記だけ）から StoreStock を作る。"""
    m = _STATUS_TOKEN.search(line)
    if not m:
        return None
    name = _clean_name(line[:m.start()])
    tail = line[m.start():].strip()
    if not name:
        # 表組みで店名と在庫が別行に出るケース：直前行を店名として採用（在庫表記だけの短い行に限る）
        if len(tail) > 16 or not prev_line:
            return None
        name = _clean_name(prev_line)
    if len(name) < 2 or _NAME_BAD.match(name) or not _NAME_HINT.search(name):
        return None
    status = classify(tail[:40])
    if status == Status.UNKNOWN:
        return None
    note = re.sub(r"\s+", " ", tail)[:30]
    return StoreStock(store=name, status=status, note=note)


def dedupe(stocks: list[StoreStock]) -> list[StoreStock]:
    seen: set[str] = set()
    out: list[StoreStock] = []
    for s in stocks:
        if s.store not in seen:
            seen.add(s.store)
            out.append(s)
    return out


def scan_text_for_stocks(text: str) -> list[StoreStock]:
    """ページ全文から「店名 … 在庫状況」を拾う汎用パーサ。サイト固有パーサが無い店のフォールバック。"""
    lines = [ln.strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]
    out: list[StoreStock] = []
    for i, line in enumerate(lines):
        if len(line) > 160:
            continue
        s = stock_from_line(line, lines[i - 1] if i else "")
        if s:
            out.append(s)
    return dedupe(out)


def rows_to_stocks(soup: BeautifulSoup, selector: str) -> list[StoreStock]:
    """表の行や li ごとに「店名 在庫」を読む。行単位なので全文スキャンより誤検出が少ない。"""
    out: list[StoreStock] = []
    for row in soup.select(selector):
        txt = row.get_text(" ", strip=True)
        if not txt or len(txt) > 160:
            continue
        s = stock_from_line(txt)
        if s:
            out.append(s)
    return dedupe(out)


def filter_stores(stocks: list[StoreStock], wanted: list[str], keywords: Optional[list[str]]) -> list[StoreStock]:
    res = stocks
    if wanted:
        res = [s for s in res if any(w in s.store for w in wanted)]
    if keywords:
        res = [s for s in res if any(k in s.store for k in keywords)]
    return res


def strip_noise(soup: BeautifulSoup) -> None:
    for t in soup(["script", "style", "noscript", "svg", "head"]):
        t.decompose()


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?\s*([\w\-]+)""", re.I)


def _jp_score(text: str) -> int:
    """日本語として自然なら高い。半角カナや置換文字が多い＝誤った文字コード。"""
    good = len(re.findall(r"[぀-ヿ一-龿Ａ-Ｚａ-ｚ０-９A-Za-z0-9]", text))
    bad = len(re.findall(r"[｡-ﾟ\ufffd]", text)) + len(re.findall(r"[\u0080-\u00ff]", text))
    return good - 3 * bad


def decode_html(raw: bytes, header_charset: Optional[str]) -> str:
    """HTTP ヘッダ → <meta charset> を優先し、無ければ UTF-8 / CP932 / EUC-JP を試して最も日本語らしいものを採用。"""
    declared: list[str] = []
    if header_charset:
        declared.append(header_charset)
    m = _META_CHARSET.search(raw[:4096])
    if m:
        declared.append(m.group(1).decode("ascii", "ignore"))
    for enc in declared:
        key = re.sub(r"shift[_-]?jis|sjis|x-sjis|windows-31j", "cp932", enc.lower())
        if key in ("iso-8859-1", "latin-1", "ascii", "us-ascii"):
            continue  # 既定値として付いているだけのことが多い
        try:
            return raw.decode(key)
        except (UnicodeDecodeError, LookupError):
            continue
    best, best_score = None, None
    for enc in ("utf-8", "cp932", "euc_jp"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        score = _jp_score(text[:20000])
        if best_score is None or score > best_score:
            best, best_score = text, score
    return best if best is not None else raw.decode("utf-8", errors="replace")


# --- 地域（都道府県）まわり ------------------------------------------------------
# JIS 都道府県コード順（index+1 がコード）
PREFECTURES = ["北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
               "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
               "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
               "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
               "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]
AREA_ONLY_MSG = "全国指定では店舗別在庫を取りません。地域を指定すると取得します（例: 地元 / 愛知）"


def pref_short(full: str) -> str:
    return full if full == "北海道" else full[:-1]


def prefs_in_keywords(keywords: Optional[list[str]]) -> list[tuple[int, str]]:
    """地域キーワードのうち都道府県名そのもの（『愛知』『京都府』）を (JISコード, 正式名) にする。"""
    out: list[tuple[int, str]] = []
    for k in keywords or []:
        for i, p in enumerate(PREFECTURES, 1):
            if k in (p, pref_short(p)) and (i, p) not in out:
                out.append((i, p))
    return out


def short_area(address: str) -> str:
    """住所から『愛知・名古屋市』『京都市』のような地域ラベルを作る（店名に添えて地域で絞れるようにする）。
    『東京都』のままだと地域キーワード『京都』に部分一致してしまうので、都道府県は短縮形＋『・』で区切る。"""
    a = re.sub(r"\s+", "", address or "")
    pref = next((p for p in PREFECTURES if a.startswith(p)), "")
    m = re.match(r"(.+?[市区町村郡])", a[len(pref):])
    city = m.group(1) if m else ""
    return "・".join(x for x in (pref_short(pref) if pref else "", city) if x)


class Checker:
    """1チェーン分の在庫チェック。サブクラスは `parse` または `check` を上書きする。"""

    timeout = aiohttp.ClientTimeout(total=12)

    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self.keywords: list[str] = []   # check() 中の地域キーワード（サイト側で地域を絞る店が使う）

    def url_for(self, code: Code) -> str:
        return code.fill(self.cfg.search) or code.fill(self.cfg.search_alt)

    async def fetch(self, session: aiohttp.ClientSession, url: str, data: Optional[dict] = None,
                    headers: Optional[dict] = None) -> tuple[int, str]:
        """data を渡すと POST（フォーム送信）。"""
        h = {**HEADERS, **(headers or {})}
        req = session.post(url, data=data, headers=h, timeout=self.timeout) if data is not None else \
            session.get(url, headers=h, timeout=self.timeout, allow_redirects=True)
        async with req as r:
            raw = await r.read()
            return r.status, decode_html(raw, r.charset)

    async def check(self, session: aiohttp.ClientSession, code: Code, keywords: Optional[list[str]] = None) -> CheckResult:
        self.keywords = list(keywords or [])
        url = self.url_for(code)
        res = CheckResult(chain_id=self.cfg.id, chain=self.cfg.name, url=url or self.cfg.home,
                          verified=self.cfg.verified)
        if not url:
            res.status, res.message = Status.LINK, "この店はこのコード種別では検索URLを作れません"
            return res
        try:
            status, html = await self.fetch(session, url)
            alt = code.fill(self.cfg.search_alt)
            if status in (404, 410) and alt and alt != url:
                # 商品ページ形式のURLが外れたときは検索ページに切り替える
                status, html = await self.fetch(session, alt)
                res.url = alt
                res.message = "商品ページが無かったため検索ページで確認"
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
        res.stocks = filter_stores(stocks, self.cfg.stores, keywords)
        if res.stocks:
            res.summarize()
        elif stocks:
            res.status = Status.OUT
            res.message = f"指定店舗・地域の行なし（他 {len(stocks)} 店は表示あり）"
        else:
            res.status = Status.UNKNOWN
            res.message = res.message or "在庫表示を読み取れませんでした。リンクで確認してください"
        return res

    async def parse(self, session: aiohttp.ClientSession, code: Code, html: str, res: CheckResult) -> list[StoreStock]:
        soup = BeautifulSoup(html, "html.parser")
        strip_noise(soup)
        stocks = rows_to_stocks(soup, "tr, li, dd, dl, p, div[class*=stock], div[class*=store], div[class*=shop]")
        return stocks or scan_text_for_stocks(soup.get_text("\n"))
