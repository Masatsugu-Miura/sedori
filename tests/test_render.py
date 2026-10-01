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
    assert rows[0] == "🟢2 🟡60 🔴10（確認 72店）"
    assert rows[1] == "🟢 新宿本店" and rows[2] == "🟢 梅田本店" and rows[3] == "🟡 店0"
    assert rows[11] == "…他 52店はリンク先で" and rows[12].startswith("[サイトで確認]") and len(v) <= 1024
    assert "札幌本店" not in v      # 在庫なしの店は行に出さない
    # 在庫あり店舗が無いチェーン
    none = CheckResult("k", "紀伊國屋", "https://k", stocks=stocks[60:61])
    none.summarize()
    v2 = build_messages(c, BookMeta(), [none], None, 1.0)[0][0].fields[0].value
    assert v2.split("\n")[:2] == ["🟢0 🟡0 🔴1（確認 1店）", "在庫あり店舗なし"]


def test_store_label_is_unified_across_chains():
    from bot.render import store_label
    assert store_label(StoreStock("TSUTAYA 大曽根店（愛知県）", Status.IN_STOCK, "在庫あり")) == "TSUTAYA 大曽根店（愛知）"
    assert store_label(StoreStock("丸善 京都本店（京都・京都市）", Status.IN_STOCK, "在庫あり 6点")) == "丸善 京都本店（京都・京都市） ×6"
    assert store_label(StoreStock("丸善 名古屋本店（愛知・名古屋市）", Status.LOW, "残り2点")) == "丸善 名古屋本店（愛知・名古屋市） ×2"
    assert store_label(StoreStock("名古屋本店", Status.IN_STOCK, "○")) == "名古屋本店"
    assert store_label(StoreStock("札幌店（北海道）", Status.IN_STOCK, "あり")) == "札幌店（北海道）"
    assert store_label(StoreStock("洛北店（京都市）", Status.LOW, "△")) == "洛北店（京都市）"


def test_header_has_keepa_graph_and_store_totals():
    c = codes.parse("9784101010014")
    msgs = build_messages(c, BookMeta(title="t"), _results(2, 3), None, 1.0, graph=b"\x89PNG...")
    e = msgs[0][0]
    assert e.image.url == "attachment://keepa.png"      # 波形は添付ファイルで送る（Keepa は Discord の直リンク取得を拒む）
    assert "在庫あり店舗 6店" in e.description
    e2 = build_messages(c, BookMeta(), [], None, 1.0)[0][0]   # グラフが取れなかったときは画像なし
    assert not e2.image.url


def test_png_size_detects_keepa_block_image():
    import struct
    from bot.lookup import png_size
    png = lambda w, h: b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", w, h) + b"\x00" * 8  # noqa: E731
    assert png_size(png(800, 300)) == (800, 300)
    assert png_size(png(500, 200)) == (500, 200)          # 「Access to price history blocked」の案内画像のサイズ
    assert png_size(b"<html>") == (0, 0)


def test_error_and_unverified_rows():
    c = codes.parse("B0C1234XYZ")
    r = CheckResult("h", "honto", "https://y", status=Status.ERROR, message="HTTP 503", verified=False)
    msgs = build_messages(c, BookMeta(), [r], None, 1.0)
    v = msgs[0][0].fields[0].value
    assert "HTTP 503" in v and "未検証" in v and "https://y" in v
    assert "書誌情報なし" == msgs[0][0].title


def test_limits_use_discord_utf16_counting():
    from bot.render import ulen
    assert ulen("🟢") == 2 and ulen("在庫") == 2 and ulen("abc") == 3
    c = codes.parse("9784101010014")
    # 絵文字だらけの長い店名でも、Discord 流の数え方で 6000 以内に収まるように分割される
    results = []
    for i in range(30):
        r = CheckResult(f"c{i}", "🟢🟡🔴" * 10 + f"チェーン{i}", "https://x",
                        stocks=[StoreStock("🟢🟡🔴🟢🟡🔴 店舗" + "🟢" * 20 + str(j), Status.IN_STOCK, "在庫あり") for j in range(10)])
        r.summarize()
        results.append(r)
    msgs = build_messages(c, BookMeta(title="t"), results, None, 1.0)
    for embeds in msgs:
        assert sum(ulen(e.title or "") + ulen(e.description or "") + (ulen(e.footer.text) if e.footer and e.footer.text else 0)
                   + sum(ulen(f.name) + ulen(f.value) for f in e.fields) for e in embeds) <= 6000
        assert all(ulen(f.value) <= 1024 for e in embeds for f in e.fields)


def test_messages_stay_under_discord_byte_limit():
    """日本語だらけの結果は 6000 文字より先に Discord の約 10KB（UTF-8）の壁に当たる。"""
    from bot.render import _embed_bytes
    c = codes.parse("9784101010014")
    results = []
    for i in range(12):
        r = CheckResult(f"c{i}", f"書店チェーン{i}", "https://x",
                        stocks=[StoreStock(f"とても長い日本語の店舗名サンプル{j}（愛知・名古屋市）", Status.IN_STOCK, "在庫あり") for j in range(10)])
        r.summarize()
        results.append(r)
    msgs = build_messages(c, BookMeta(title="吾輩は猫である"), results, None, 1.0)
    assert len(msgs) >= 2
    for embeds in msgs:
        assert sum(_embed_bytes(e) for e in embeds) <= 9200
