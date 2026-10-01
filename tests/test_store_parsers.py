"""サイト固有チェッカーのパーサと流れ（保存した HTML/JSON 断片のみ、ネットワークなし）。"""
import asyncio
from pathlib import Path
from urllib.parse import quote

from bot import codes
from bot.stores import kinokuniya as kinokuniya_mod
from bot.stores import miraiya as miraiya_mod
from bot.stores import region_keywords
from bot.stores import tsutaya as tsutaya_mod
from bot.stores.animate import AnimateChecker, parse_stock_table
from bot.stores.base import CheckResult, Status, StoreConfig, prefs_in_keywords, short_area
from bot.stores.book1st import Book1stChecker, parse_remote, parse_stock_page
from bot.stores.kinokuniya import KinokuniyaChecker, parse_stock_view, parse_store_select
from bot.stores.miraiya import MiraiyaChecker, merge, shops_from_json, stock_status
from bot.stores.sanseido import SanseidoChecker, parse_stock_list
from bot.stores.sanyodo import SanyodoChecker, detail_url, parse_detail, parse_shop_areas
from bot.stores.tsutaya import TsutayaChecker, parse_result, result_url, search_terms, stock_page_url

FIX = Path(__file__).parent / "fixtures"
BOOK = codes.parse("9784101010014")


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def run(checker, routes: dict, keywords=None) -> CheckResult:
    """fetch を差し替えて check を通す。routes は {URLの一部: ファイル名 or (status, 本文)}。"""
    calls = []

    async def fetch(session, url, data=None, headers=None):
        calls.append((url, data))
        for key, v in routes.items():
            if key in url:
                return v if isinstance(v, tuple) else (200, fx(v))
        return 404, ""
    checker.fetch = fetch
    res = asyncio.run(checker.check(None, BOOK, keywords))
    res.calls = calls
    return res


def cfg(id_, checker, search, alt=""):
    return StoreConfig(id=id_, name=id_, checker=checker, search=search, search_alt=alt)


# --- 共通 ---------------------------------------------------------------------
def test_area_helpers():
    assert prefs_in_keywords(["愛知", "名古屋", "京都府", "東京"]) == [(23, "愛知県"), (26, "京都府"), (13, "東京都")]
    assert prefs_in_keywords(region_keywords("地元")) == [(23, "愛知県"), (26, "京都府")]
    assert short_area("愛知県豊川市馬場町宮脇166") == "愛知・豊川市"
    assert short_area("京都市左京区高野西開町36") == "京都市"
    assert "京都" not in short_area("東京都足立区千住旭町42-2")     # 『京都』キーワードに東京の店が混ざらない


# --- TSUTAYA -------------------------------------------------------------------
def test_tsutaya_parsers():
    url = stock_page_url(fx("tsutaya_item.html"))
    assert url.startswith("https://store-tsutaya.tsite.jp/search/result/stock?workId=40040093")
    assert "/stock/result?" in result_url(url, "愛知県") and result_url(url, "愛知県", 2).endswith("dispPageNo=2")
    stocks, last = parse_result(fx("tsutaya_result.html"), "京都府")
    assert last == 4
    got = {s.store: s.status for s in stocks}
    assert got["六本木 蔦屋書店（京都府）"] == Status.IN_STOCK
    assert got["TSUTAYA 田町駅前店（京都府）"] == Status.OUT          # 取り扱いがありません
    assert search_terms(region_keywords("地元")) == ["愛知県", "京都府"]
    assert search_terms(["新宿"]) == ["新宿"] and search_terms([]) == []


def test_tsutaya_flow():
    c = TsutayaChecker(cfg("tsutaya", "tsutaya", "https://store-tsutaya.tsite.jp/search?productkey={jan}"))
    res = run(c, {"productkey=": "tsutaya_item.html", "/stock/result": "tsutaya_result.html"}, ["京都"])
    assert res.status == Status.IN_STOCK and len(res.stocks) == 4
    assert sum("dispPageNo" in u for u, _ in res.calls) == 2           # 3 ページまで
    miss = run(TsutayaChecker(c.cfg), {"productkey=": (200, "<html>該当する商品がみつかりませんでした</html>")}, ["京都"])
    assert miss.status == Status.UNKNOWN and "該当商品" in miss.message


def test_tsutaya_nationwide_sweeps_all_terms_and_pages(monkeypatch):
    c = TsutayaChecker(cfg("tsutaya", "tsutaya", "https://store-tsutaya.tsite.jp/search?productkey={jan}"))
    nat = run(c, {"productkey=": "tsutaya_item.html", "/stock/result": "tsutaya_result.html"})
    pages = [u for u, _ in nat.calls if "/stock/result" in u]
    assert len(pages) == len(tsutaya_mod.NATIONAL_TERMS) * 4                  # 各語とも最終ページ(4)まで取る
    assert {quote(t) for t in tsutaya_mod.NATIONAL_TERMS} <= {u.split("storeSearchKeyword=")[1].split("&")[0] for u in pages}
    assert nat.status == Status.IN_STOCK and [s.store for s in nat.stocks][:2] == ["TSUTAYA 田町駅前店", "SHARE LOUNGE 神谷町駅前"]
    assert len(nat.stocks) == 4 and not nat.message                           # 地域ラベル無し・重複除去・全ページ取得
    assert nat.url.endswith("productKey=9784101010014")                       # リンクは店舗検索ページのまま
    monkeypatch.setattr(tsutaya_mod, "NATIONAL_MAX_PAGES", 6)
    capped = run(TsutayaChecker(c.cfg), {"productkey=": "tsutaya_item.html", "/stock/result": "tsutaya_result.html"})
    assert sum("/stock/result" in u for u, _ in capped.calls) == 6 and "未取得" in capped.message
    assert capped.status == Status.IN_STOCK


# --- ブックファースト ---------------------------------------------------------------
def test_book1st_parsers():
    isbn, ids, stores = parse_stock_page(fx("book1st_stock.html"))
    assert isbn == "4101010013" and ids.startswith(",17,11,") and len(stores) == 22
    assert stores[0][:2] == (1, "ルミネ北千住店") and stores[20][1] == "アバンティブックセンター洛北店"
    name, st, mark = parse_remote(fx("book1st_remote.txt"))
    assert name == "アバンティブックセンター洛北店" and mark in "○△×"
    assert parse_remote("RTC=0\nMSG=err") is None


def test_book1st_flow_area_only_queries_matching_stores():
    c = Book1stChecker(cfg("book1st", "book1st", "https://b1st.e-netservice.biz/book1stnet/searchbook/stock.asp?isbn={isbn13}"))
    res = run(c, {"stock.asp": "book1st_stock.html", "MeRemote.asp": "book1st_remote.txt"}, region_keywords("京都"))
    posts = [d for _, d in res.calls if d]
    assert [p["cnt"] for p in posts] == ["21"] and posts[0]["maxcnt"] == "22"   # 東京都の店は問い合わせない
    assert [s.store for s in res.stocks] == ["アバンティブックセンター洛北店（京都市）"]


# --- アニメイト -------------------------------------------------------------------
def test_animate_parser_and_flow():
    stocks = parse_stock_table(fx("animate_stock.html"))
    assert any(s.status == Status.IN_STOCK for s in stocks)
    assert all(s.status in (Status.IN_STOCK, Status.LOW) for s in stocks)      # なし～残りわずか → わずか
    assert parse_stock_table(fx("animate_none.html")) == []
    c = AnimateChecker(cfg("animate", "animate", "https://www.animate-onlineshop.jp/products/list.php?smt={code}"))
    res = run(c, {"list.php": (200, "<html></html>"), "zaiko.shoptech.jp": "animate_none.html"})
    assert res.status == Status.UNKNOWN and "該当商品" in res.message
    assert len(res.calls) == 1 and "product_code=9784101010014" in res.calls[0][0]   # 商品一覧ページは取得しない


# --- 未来屋 -----------------------------------------------------------------------
def test_miraiya_parsers_and_flow():
    shops = shops_from_json(fx("miraiya_shops.json"))
    assert len(shops) == 3 and shops_from_json('{"count":0}') == []
    assert [stock_status(n)[0] for n in (5, 1, 0, -1)] == [Status.IN_STOCK, Status.LOW, Status.OUT, Status.UNKNOWN]
    got = {s.store: s.status for s in merge(shops, fx("miraiya_stock.json"))}
    assert got["未来屋書店大高（愛知・名古屋市）"] == Status.OUT and got["未来屋書店守山（愛知・名古屋市）"] == Status.LOW
    c = MiraiyaChecker(cfg("miraiya", "miraiya", "https://search.miraiyashoten.co.jp/neighborhood/{isbn13}/"))
    res = run(c, {"neighborhoodAPI": "miraiya_shops.json", "stockAPI": "miraiya_stock.json",
                  "/neighborhood/": (200, "<html></html>")}, region_keywords("愛知"))
    assert res.status == Status.LOW and res.url.endswith("?pref=23")
    assert any("prefecture=23" in u for u, _ in res.calls)
    assert [u for u, _ in res.calls if "neighborhoodAPI" in u] == [f"{miraiya_mod.BASE}/neighborhoodAPI/?isbn=9784101010014&prefecture=23"]
    # 全国：47 都道府県の一覧を引き、在庫は全店コードをまとめて 1 回
    nat = run(MiraiyaChecker(c.cfg), {"neighborhoodAPI": "miraiya_shops.json", "stockAPI": "miraiya_stock.json"})
    lists = [u for u, _ in nat.calls if "neighborhoodAPI" in u]
    assert len(lists) == 47 and {u.rsplit("=", 1)[1] for u in lists} == {str(i) for i in range(1, 48)}
    assert sum("stockAPI" in u for u, _ in nat.calls) == 1 and nat.status == Status.LOW
    assert nat.url.endswith("/neighborhood/9784101010014/") and len(nat.stocks) == 3 * 47
    none = run(MiraiyaChecker(c.cfg), {"neighborhoodAPI": (200, '{"count":0}')})
    assert none.status == Status.UNKNOWN and "見つかりません" in none.message


# --- 三洋堂 -----------------------------------------------------------------------
def test_sanyodo_parsers_and_flow():
    assert "products-detail?productcode=0100000000000031150624" in detail_url(fx("sanyodo_list.html"))
    areas = parse_shop_areas(fx("sanyodo_shop.html"))
    assert areas["豊川店"] == "愛知・豊川市"
    got = {s.store: s.status for s in parse_detail(fx("sanyodo_detail.html"), areas)}
    assert got["豊川店（愛知・豊川市）"] == Status.IN_STOCK and got["碧南店（愛知・碧南市）"] == Status.OUT
    c = SanyodoChecker(cfg("sanyodo", "sanyodo", "https://www.sanyodo.co.jp/lookup/products-list?isbncd={isbn13}&booksearch=1"))
    res = run(c, {"products-list": "sanyodo_list.html", "products-detail": "sanyodo_detail.html",
                  "/shop": "sanyodo_shop.html"}, ["豊川"])
    assert [s.store for s in res.stocks] == ["豊川店（愛知・豊川市）"] and "products-detail" in res.url


# --- 三省堂 -----------------------------------------------------------------------
def test_sanseido_parser_and_flow():
    got = {s.store: s.status for s in parse_stock_list(fx("sanseido_stock.html"))}
    assert got["有楽町店"] == Status.OUT and got["名古屋本店"] == Status.IN_STOCK
    c = SanseidoChecker(cfg("sanseido", "sanseido", "https://www.books-sanseido.jp/booksearch/BookStockList.action?isbn={isbn13}"))
    res = run(c, {"BookStockList": "sanseido_stock.html"}, region_keywords("愛知"))
    assert {s.store for s in res.stocks} == {"名古屋本店", "一宮店"}
    err = run(SanseidoChecker(c.cfg), {"BookStockList": "sanseido_error.html"})
    assert err.status == Status.UNKNOWN and "該当商品" in err.message


# --- 紀伊國屋 ---------------------------------------------------------------------
def test_kinokuniya_parsers():
    stores = parse_store_select(fx("kinokuniya_select.html"))
    assert ("北海道", "札幌本店", "FA") in stores and ("愛知", "mozoワンダーシティ店", "N5") in stores
    name, st, text = parse_stock_view(fx("kinokuniya_view.html"))
    assert name == "名古屋空港店" and st == Status.LOW and "在庫僅少" in text


def test_kinokuniya_flow_posts_only_area_stores():
    c = KinokuniyaChecker(cfg("kinokuniya", "kinokuniya",
                              "https://www.kinokuniya.co.jp/disp/CKnSfStockSearchStoreEncrypt_001.jsp?CAT=01&GOODS_STK_NO={isbn13}"))
    res = run(c, {"Encrypt_001": "kinokuniya_select.html", "Encrypt_002": "kinokuniya_view.html"}, region_keywords("愛知"))
    posts = [d for _, d in res.calls if d]
    assert sorted(k for d in posts for k in d if k.startswith("MAN_ENTR_CD|")) == ["MAN_ENTR_CD|N3", "MAN_ENTR_CD|N5"]
    assert res.status == Status.LOW and res.stocks[0].store == "名古屋空港店（愛知）"
    miss = run(KinokuniyaChecker(c.cfg), {"Encrypt_001": (200, "<html>none</html>")}, ["愛知"])
    assert "該当商品" in miss.message


def test_kinokuniya_nationwide_posts_every_store(monkeypatch):
    c = KinokuniyaChecker(cfg("kinokuniya", "kinokuniya",
                              "https://www.kinokuniya.co.jp/disp/CKnSfStockSearchStoreEncrypt_001.jsp?CAT=01&GOODS_STK_NO={isbn13}"))
    stores = parse_store_select(fx("kinokuniya_select.html"))
    nat = run(c, {"Encrypt_001": "kinokuniya_select.html", "Encrypt_002": "kinokuniya_view.html"})
    posted = sorted(k.split("|", 1)[1] for _, d in nat.calls if d for k in d if k.startswith("MAN_ENTR_CD|"))
    assert posted == sorted(cd for _, _, cd in stores) and len(posted) == 6
    assert nat.status == Status.LOW and len(nat.stocks) == 6
    assert {s.store.split("（")[1] for s in nat.stocks} == {f"{p}）" for p, _, _ in stores}   # 店名に都道府県を添える
    monkeypatch.setattr(kinokuniya_mod, "MAX_STORES_NATIONAL", 2)                       # 安全弁
    capped = run(KinokuniyaChecker(c.cfg), {"Encrypt_001": "kinokuniya_select.html", "Encrypt_002": "kinokuniya_view.html"})
    assert len([d for _, d in capped.calls if d]) == 2
    # 店舗フィルタ（cfg.stores）があれば全国でもその店だけ
    c.cfg.stores = ["新宿"]
    only = run(KinokuniyaChecker(c.cfg), {"Encrypt_001": "kinokuniya_select.html", "Encrypt_002": "kinokuniya_view.html"})
    assert [k for _, d in only.calls if d for k in d if k.startswith("MAN_ENTR_CD|")] == ["MAN_ENTR_CD|G2"]
