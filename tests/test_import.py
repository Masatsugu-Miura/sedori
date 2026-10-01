import csv
import io

from scripts.import_sheet import rows_to_configs


def _rows(text: str):
    return list(csv.reader(io.StringIO(text)))


def test_header_detection_and_templating():
    rows = _rows("店名,検索URL,対象店舗,メモ\n"
                 "有隣堂,https://www.yurindo.co.jp/store/search?q=9784101010014,横浜西口店 / 藤沢店,神奈川\n"
                 "テスト書店,https://example.com/,,\n"
                 "紀伊國屋,https://www.kinokuniya.co.jp/f/dsg-01-{isbn13},新宿本店,\n")
    cfgs = rows_to_configs(rows, set(), auto=False)
    assert [c.id for c in cfgs] == ["yurindo", "example", "kinokuniya"]
    assert cfgs[0].search == "https://www.yurindo.co.jp/store/search?q={isbn13}"
    assert cfgs[0].stores == ["横浜西口店", "藤沢店"] and cfgs[0].note == "神奈川" and cfgs[0].checker == "link"
    assert cfgs[1].search == "" and cfgs[1].home == "https://example.com/"
    assert cfgs[2].search.endswith("{isbn13}") and cfgs[2].stores == ["新宿本店"]


def test_no_header_and_auto():
    rows = _rows("ジュンク堂,https://honto.jp/netstore/search_109784101010014.html\n"
                 "名前だけの店\n")
    cfgs = rows_to_configs(rows, {"honto"}, auto=True)
    assert cfgs[0].id == "honto2" and cfgs[0].checker == "generic"
    assert cfgs[0].search == "https://honto.jp/netstore/search_10{isbn13}.html"
    assert cfgs[1].checker == "link" and cfgs[1].home == "" and cfgs[1].search == ""


def test_empty():
    assert rows_to_configs(_rows("\n,\n"), set(), False) == []
