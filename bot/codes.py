"""ASIN / JAN / ISBN の判定と相互変換。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_AMAZON_URL = re.compile(r"amazon\.co\.jp/(?:[^/\s]+/)?(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/([0-9A-Z]{10})", re.I)
_CODE_IN_TEXT = re.compile(r"(?<![0-9A-Za-z])(B[0-9A-Z]{9}|\d{13}|\d{8}|\d{9}[\dXx])(?![0-9A-Za-z])")


@dataclass
class Code:
    raw: str
    kind: str                       # "ASIN" | "ISBN10" | "ISBN13" | "JAN13" | "JAN8" | ""
    isbn13: Optional[str] = None    # 978/979 で始まる13桁（本なら JAN と同じ）
    isbn10: Optional[str] = None
    asin: Optional[str] = None      # Amazon の ASIN（本なら ISBN10 と同じ）
    jan: Optional[str] = None       # 13桁または8桁のJAN
    notes: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return bool(self.kind)

    @property
    def is_book(self) -> bool:
        return self.isbn13 is not None

    def label(self) -> str:
        parts = []
        if self.asin:
            parts.append(f"ASIN `{self.asin}`")
        if self.isbn13:
            parts.append(f"ISBN/JAN `{self.isbn13}`")
        elif self.jan:
            parts.append(f"JAN `{self.jan}`")
        return " / ".join(parts) or f"`{self.raw}`"

    def fill(self, template: str) -> str:
        """URLテンプレートの {isbn13} {isbn10} {jan} {asin} {code} %s を埋める。足りない値があれば None。"""
        code = self.isbn13 or self.jan or self.asin or self.raw
        values = {
            "isbn13": self.isbn13 or "",
            "isbn10": self.isbn10 or "",
            "jan": self.jan or self.isbn13 or "",
            "asin": self.asin or "",
            "code": code,
        }
        out = template.replace("%s", "{code}")
        for k, v in values.items():
            if ("{" + k + "}") in out:
                if not v:
                    return ""
                out = out.replace("{" + k + "}", v)
        return out


def _clean(s: str) -> str:
    return re.sub(r"[\s\-‐‑–—ー_]", "", s.strip()).upper()


def isbn10_check(digits9: str) -> str:
    total = sum((10 - i) * int(c) for i, c in enumerate(digits9))
    r = (11 - total % 11) % 11
    return "X" if r == 10 else str(r)


def ean13_check(digits12: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits12))
    return str((10 - total % 10) % 10)


def is_valid_isbn10(s: str) -> bool:
    return bool(re.fullmatch(r"\d{9}[\dX]", s)) and isbn10_check(s[:9]) == s[9]


def is_valid_ean13(s: str) -> bool:
    return bool(re.fullmatch(r"\d{13}", s)) and ean13_check(s[:12]) == s[12]


def isbn10_to_13(isbn10: str) -> str:
    core = "978" + isbn10[:9]
    return core + ean13_check(core)


def isbn13_to_10(isbn13: str) -> Optional[str]:
    if not isbn13.startswith("978"):
        return None
    core = isbn13[3:12]
    return core + isbn10_check(core)


def extract_candidate(text: str) -> Optional[str]:
    """メッセージ本文から ASIN / JAN / ISBN っぽい文字列、または Amazon URL の ASIN を1つ拾う。"""
    m = _AMAZON_URL.search(text)
    if m:
        return m.group(1).upper()
    cleaned = _clean(text)
    if re.fullmatch(r"B[0-9A-Z]{9}|\d{13}|\d{8}|\d{9}[\dX]", cleaned):
        return cleaned
    # 978-4-10-101001-4 のようなハイフン区切りをつなげてから探す
    joined = re.sub(r"(?<=\d)[\-‐‑–—](?=[\dXx])", "", text)
    m = _CODE_IN_TEXT.search(joined)
    return m.group(1).upper() if m else None


def parse(text: str) -> Code:
    raw = text.strip()
    s = extract_candidate(raw) or _clean(raw)
    c = Code(raw=raw, kind="")

    if re.fullmatch(r"B[0-9A-Z]{9}", s):
        c.kind, c.asin = "ASIN", s
        c.notes.append("本以外のASIN形式です。AmazonページからJAN/ISBNを探します。")
        return c

    if re.fullmatch(r"\d{9}[\dX]", s):
        if not is_valid_isbn10(s):
            c.notes.append("ISBN-10 のチェックディジットが合いません（そのまま検索します）。")
        c.kind, c.isbn10, c.asin = "ISBN10", s, s
        c.isbn13 = isbn10_to_13(s)
        c.jan = c.isbn13
        return c

    if re.fullmatch(r"\d{13}", s):
        if not is_valid_ean13(s):
            c.notes.append("JAN のチェックディジットが合いません（そのまま検索します）。")
        c.jan = s
        if s.startswith(("978", "979")):
            c.kind, c.isbn13 = "ISBN13", s
            c.isbn10 = isbn13_to_10(s)
            c.asin = c.isbn10
        else:
            c.kind = "JAN13"
            if s.startswith("491"):
                c.notes.append("雑誌コード(491)のJANです。雑誌は在庫検索に対応していない書店があります。")
        return c

    if re.fullmatch(r"\d{8}", s):
        c.kind, c.jan = "JAN8", s
        return c

    return c


_PLACEHOLDER = re.compile(r"\{(isbn13|isbn10|jan|asin|code)\}|%s")


def has_placeholder(url: str) -> bool:
    return bool(_PLACEHOLDER.search(url))


def templatize_url(url: str) -> str:
    """検索URLにコードがそのまま入っている場合（例: ...?q=9784088820002）をプレースホルダに置き換える。
    すでに {isbn13} 等があればそのまま返す。"""
    url = url.strip()
    if not url or has_placeholder(url):
        return url
    # 直前に数字が付くURL（honto の search_10{isbn13} など）もあるので、位置をずらしながら正しい ISBN-13 を探す
    for m in re.finditer(r"(?=(97[89]\d{10}))", url):
        if is_valid_ean13(m.group(1)) and not re.match(r"\d", url[m.start() + 13:m.start() + 14]):
            return url[:m.start()] + "{isbn13}" + url[m.start() + 13:]
    m = re.search(r"(?<![0-9A-Za-z])(B[0-9A-Z]{9})(?![0-9A-Za-z])", url)
    if m:
        return url[:m.start()] + "{asin}" + url[m.end():]
    m = re.search(r"(?<!\d)(\d{9}[\dX])(?![\dA-Za-z])", url)
    if m and is_valid_isbn10(m.group(1)):
        return url[:m.start()] + "{isbn10}" + url[m.end():]
    m = re.search(r"(?<!\d)(\d{13})(?!\d)", url)
    if m and is_valid_ean13(m.group(1)):
        return url[:m.start()] + "{jan}" + url[m.end():]
    return url
