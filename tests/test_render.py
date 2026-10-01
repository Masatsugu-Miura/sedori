from bot import codes
from bot.lookup import BookMeta
from bot.render import build_messages
from bot.stores.base import CheckResult, Status, StoreStock


def _results(n: int, rows: int) -> list[CheckResult]:
    out = []
    for i in range(n):
        r = CheckResult(f"c{i}", f"チェーン{i} とても長い書店チェーンの名前", "https://example.com/" + "x" * 60,
                        stocks=[StoreStock(f"店舗{j} かなり長い支店名サンプル", Status.IN_STOCK, "在庫あり") for j in range(rows)])
        r.summarize()
        out.append(r)
    return out


def _total(msgs):
    n = 0
    for embeds in msgs:
        assert 1 <= len(embeds) <= 10
        for e in embeds:
            s = len(e.title or "") + len(e.description or "") + (len(e.footer.text) if e.footer and e.footer.text else 0)
            s += sum(len(f.name) + len(f.value) for f in e.fields)
            n += s
            assert all(len(f.value) <= 1024 for f in e.fields)
    return n


def test_small_result_is_single_message():
    c = codes.parse("9784101010014")
    msgs = build_messages(c, BookMeta(title="テスト"), _results(3, 2), None, 0.4)
    assert len(msgs) == 1 and msgs[0][0].title == "テスト"
    assert sum(len(e.fields) for e in msgs[0]) == 3


def test_large_result_splits_within_limits():
    c = codes.parse("9784101010014")
    results = _results(40, 12)
    msgs = build_messages(c, BookMeta(title="テスト"), results, "新宿", 3.2)
    assert len(msgs) >= 2
    for embeds in msgs:
        total = sum(len(e.title or "") + len(e.description or "") + (len(e.footer.text) if e.footer and e.footer.text else 0)
                    + sum(len(f.name) + len(f.value) for f in e.fields) for e in embeds)
        assert total <= 6000
    assert sum(len(e.fields) for m in msgs for e in m) == 40
    _total(msgs)


def test_long_store_list_shows_in_stock_first_and_counts_rest():
    c = codes.parse("9784101010014")
    stocks = [StoreStock(f"店{j}", Status.LOW, "在庫僅少") for j in range(60)]
    stocks += [StoreStock("札幌本店", Status.OUT, "在庫なし"), StoreStock("新宿本店", Status.IN_STOCK, "在庫あり"),
               StoreStock("梅田本店", Status.IN_STOCK, "在庫あり")] + [StoreStock(f"店x{j}", Status.OUT, "在庫なし") for j in range(9)]
    r = CheckResult("k", "紀伊國屋", "https://k", stocks=stocks)
    r.summarize()
    v = build_messages(c, BookMeta(), [r], None, 1.0)[0][0].fields[0].value
    rows = v.split("\n")
    assert rows[0].startswith("🟢 新宿本店") and rows[1].startswith("🟢 梅田本店") and rows[2].startswith("🟡 店0")
    assert rows[8] == "…他 64 店（🟡54 🔴10）リンク先で全件" and len(v) <= 1024
    # 行数に収まるときは元の順のまま
    short = CheckResult("k", "紀伊國屋", "https://k", stocks=stocks[60:63])
    v2 = build_messages(c, BookMeta(), [short], None, 1.0)[0][0].fields[0].value
    assert v2.split("\n")[0].startswith("🔴 札幌本店") and "…他" not in v2


def test_error_and_unverified_rows():
    c = codes.parse("B0C1234XYZ")
    r = CheckResult("h", "honto", "https://y", status=Status.ERROR, message="HTTP 503", verified=False)
    msgs = build_messages(c, BookMeta(), [r], None, 1.0)
    v = msgs[0][0].fields[0].value
    assert "HTTP 503" in v and "未検証" in v and "https://y" in v
    assert "書誌情報なし" == msgs[0][0].title
