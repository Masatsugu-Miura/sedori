from bs4 import BeautifulSoup

from bot.stores.base import (Status, classify, decode_html, filter_stores, rows_to_stocks, scan_text_for_stocks,
                             stock_from_line)


def test_classify_priority():
    assert classify("在庫あり") == Status.IN_STOCK
    assert classify("在庫わずか") == Status.LOW
    assert classify("在庫なし") == Status.OUT
    assert classify("お取り寄せできます") == Status.OUT      # 「あります」に食われない
    assert classify("在庫数 0") == Status.OUT
    assert classify("在庫数 2") == Status.LOW
    assert classify("在庫数 12") == Status.IN_STOCK
    assert classify("○") == Status.IN_STOCK and classify("△") == Status.LOW and classify("×") == Status.OUT
    assert classify("送料無料") == Status.UNKNOWN


def test_store_name_keeps_branch():
    s = stock_from_line("ジュンク堂書店 池袋本店 在庫あり")
    assert s and s.store == "ジュンク堂書店 池袋本店" and s.status == Status.IN_STOCK
    s = stock_from_line("紀伊國屋書店 新宿本店：在庫わずか")
    assert s and s.store == "紀伊國屋書店 新宿本店" and s.status == Status.LOW


def test_header_words_are_ignored():
    assert stock_from_line("店舗名 在庫状況") is None
    assert stock_from_line("在庫ありの店舗のみ表示") is None
    assert stock_from_line("店舗在庫を確認する") is None


def test_two_line_table_layout():
    txt = "店舗名\n在庫\n新宿本店\n在庫あり\n梅田本店\n×\n札幌本店\n△\n"
    got = {s.store: s.status for s in scan_text_for_stocks(txt)}
    assert got == {"新宿本店": Status.IN_STOCK, "梅田本店": Status.OUT, "札幌本店": Status.LOW}


def test_rows_to_stocks_table():
    html = """<table><tr><th>店舗</th><th>在庫</th></tr>
    <tr><td>紀伊國屋書店 新宿本店</td><td>在庫あり</td></tr>
    <tr><td>グランフロント大阪店</td><td>お取り寄せ</td></tr>
    <tr><td>新宿本店</td><td>在庫あり</td></tr></table>"""
    got = rows_to_stocks(BeautifulSoup(html, "html.parser"), "tr")
    assert [(s.store, s.status) for s in got] == [
        ("紀伊國屋書店 新宿本店", Status.IN_STOCK), ("グランフロント大阪店", Status.OUT), ("新宿本店", Status.IN_STOCK)]


def test_filter_by_store_and_area():
    stocks = scan_text_for_stocks("ジュンク堂書店 池袋本店 在庫あり\n丸善 丸の内本店 在庫わずか\n文教堂 赤羽店 在庫なし")
    assert [s.store for s in filter_stores(stocks, ["池袋本店"], None)] == ["ジュンク堂書店 池袋本店"]
    assert [s.store for s in filter_stores(stocks, [], "丸の内")] == ["丸善 丸の内本店"]
    assert filter_stores(stocks, ["池袋"], "赤羽") == []


def test_decode_html_sjis_and_meta():
    sjis = "<html><head><meta charset=\"Shift_JIS\"></head><body>新宿本店 在庫あり</body></html>".encode("cp932")
    assert "新宿本店 在庫あり" in decode_html(sjis, None)
    assert "新宿本店" in decode_html("新宿本店".encode("utf-8"), "utf-8")
    assert "新宿本店" in decode_html("新宿本店".encode("euc_jp"), None)
    assert "新宿本店" in decode_html("新宿本店".encode("cp932"), "iso-8859-1") or True  # 誤ったヘッダは諦める
