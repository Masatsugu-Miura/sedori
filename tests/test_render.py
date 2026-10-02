from bot import codes
from bot.lookup import BookMeta
from bot.render import build_messages, ulen
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


def _body(msgs) -> str:
    """ヘッダー以外の Embed の本文（description）とフィールドを全部つなげる。"""
    out = []
    for i, m in enumerate(msgs):
        for j, e in enumerate(m):
            if i == 0 and j == 0:
                continue                                  # 先頭は書誌のヘッダー
            if e.description:
                out.append(e.description)
            out += [f"{f.name}\n{f.value}" for f in e.fields]
    return "\n".join(out)


def test_small_result_is_single_message():
    c = codes.parse("9784101010014")
    msgs = build_messages(c, BookMeta(title="テスト"), _results(3, 2), None, 0.4)
    assert len(msgs) == 1 and msgs[0][0].title == "テスト"
    body = _body(msgs)
    assert body.count("**[チェーン") == 3 and "](https://example.com/" in body


def test_large_result_splits_within_limits():
    c = codes.parse("9784101010014")
    results = _results(40, 12)
    msgs = build_messages(c, BookMeta(title="テスト"), results, "新宿", 3.2)
    assert len(msgs) >= 2
    for embeds in msgs:
        total = sum(len(e.title or "") + len(e.description or "") + (len(e.footer.text) if e.footer and e.footer.text else 0)
                    + sum(len(f.name) + len(f.value) for f in e.fields) for e in embeds)
        assert total <= 6000
    assert _body(msgs).count("**[チェーン") == 40
    _total(msgs)


def test_long_store_list_shows_every_store_in_stock_first():
    c = codes.parse("9784101010014")
    stocks = [StoreStock(f"ショッピングモール内の長い名前の店{j}", Status.LOW, "在庫僅少") for j in range(200)]
    stocks += [StoreStock("札幌本店", Status.OUT, "在庫なし"), StoreStock("新宿本店", Status.IN_STOCK, "在庫あり"),
               StoreStock("梅田本店", Status.IN_STOCK, "在庫あり 6点")] + [StoreStock(f"店x{j}", Status.OUT, "在庫なし") for j in range(9)]
    r = CheckResult("k", "紀伊國屋", "https://k", stocks=stocks)
    r.summarize()
    msgs = build_messages(c, BookMeta(), [r], None, 1.0)
    embeds = [e for m in msgs for e in m]
    assert len(embeds) >= 3 and all(ulen(e.description or "") <= 4096 for e in embeds)   # 続きの Embed に分かれる
    rows = _body(msgs).split("\n")
    icon = rows[0][0]
    assert rows[0] == f"{icon} **[紀伊國屋](https://k)** 202/212"          # 在庫あり店数/確認店数、リンクは名前に
    assert rows[1] == f"{icon} 新宿本店 ─ 多い" and rows[2] == f"{icon} 梅田本店 ─ 6個"
    assert rows[3] == f"{icon} ショッピングモール内の長い名前の店0 ─ 少ない"
    assert f"{icon} ショッピングモール内の長い名前の店199 ─ 少ない" in rows and not any("…他" in x or "サイトで確認" in x for x in rows)
    assert "札幌本店" not in rows                       # 在庫なしの店は行に出さない
    # 在庫あり店舗が無いチェーンは 1 行だけ
    none = CheckResult("k", "紀伊國屋", "https://k", stocks=stocks[:0] + [StoreStock("札幌本店", Status.OUT, "在庫なし")])
    none.summarize()
    assert _body(build_messages(c, BookMeta(), [none], None, 1.0)).endswith(" **[紀伊國屋](https://k)** 0/1")


def test_store_row_format():
    from bot.render import row_text
    assert row_text(StoreStock("TSUTAYA 大曽根店（愛知県）", Status.IN_STOCK, "在庫あり")) == "TSUTAYA 大曽根店（愛知） ─ 多い"
    assert row_text(StoreStock("丸善 京都本店（京都・京都市）", Status.IN_STOCK, "在庫あり 6点")) == "丸善 京都本店（京都・京都市） ─ 6個"
    assert row_text(StoreStock("丸善 名古屋本店（愛知・名古屋市）", Status.LOW, "残り2点"), strip_label=True) == "丸善 名古屋本店 ─ 2個"
    assert row_text(StoreStock("名古屋本店", Status.IN_STOCK, "○")) == "名古屋本店 ─ 多い"
    assert row_text(StoreStock("洛北店（京都市）", Status.LOW, "△"), strip_label=True) == "洛北店 ─ 少ない"
    assert row_text(StoreStock("札幌店（北海道）", Status.OUT, "なし")) == "札幌店（北海道） ─ なし"


def test_header_has_keepa_graph_and_store_totals():
    c = codes.parse("9784101010014")
    msgs = build_messages(c, BookMeta(title="t"), _results(2, 3), None, 1.0, graph=b"\x89PNG...")
    e = msgs[0][0]
    assert e.image.url == "attachment://keepa.png"      # 波形は添付ファイルで送る（Keepa は Discord の直リンク取得を拒む）
    assert "在庫あり店舗" not in e.description and "検索範囲" not in e.description or "全国" in e.description
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
    v = _body(msgs)
    assert v == "⚠️ [honto](https://y) HTTP 503 ※URL未検証"
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


def test_mixed_brand_results_are_grouped_by_brand():
    from bot.render import brand_of, chain_lines
    assert brand_of("くまざわ書店名古屋セントラルパーク店") == "くまざわ書店"
    assert brand_of("BOOKSえみたすピアゴ植田店") == "BOOKSえみたす"
    assert brand_of("あおい書店らくだ西春大日店") == "あおい書店"
    assert brand_of("豊川堂カルミア店") == "豊川堂" and brand_of("カルコス各務原店") == "カルコス"
    assert brand_of("NAgoya Book Center") == "NAgoya Book Center" and brand_of("TOUTEN BOOKSTORE") == "TOUTEN BOOKSTORE"
    r = CheckResult("hanmoto", "書店在庫情報プロジェクト", "https://h", group_brands=True, stocks=[
        StoreStock("NAgoya Book Center（愛知・名古屋市）", Status.IN_STOCK, "在庫あり"),
        StoreStock("くまざわ書店名古屋南店（愛知・名古屋市）", Status.LOW, "在庫わずか"),
        StoreStock("BOOKSえみたすピアゴ植田店（愛知・名古屋市）", Status.IN_STOCK, "在庫あり"),
        StoreStock("くまざわ書店鳴海店（愛知・名古屋市）", Status.OUT, "在庫なし"),
        StoreStock("BOOKSえみたす吉良店（愛知・西尾市）", Status.LOW, "在庫わずか"),
        StoreStock("くまざわ書店岩倉店（愛知・岩倉市）", Status.IN_STOCK, "在庫あり"),
        StoreStock("TOUTEN BOOKSTORE（愛知・名古屋市）", Status.OUT, "在庫なし")])
    r.summarize()
    r.icon = "📚"
    assert chain_lines(r, "愛知", strip_label=True) == [
        "📚 **[書店在庫情報プロジェクト](https://h)** 愛知 5/7",
        "🟠 **BOOKSえみたす** 2/2", "🟠 ピアゴ植田店 ─ 多い", "🟠 吉良店 ─ 少ない",
        "🔵 **くまざわ書店** 2/3", "🔵 岩倉店 ─ 多い", "🔵 名古屋南店 ─ 少ない",
        "🔹 NAgoya Book Center ─ 多い"]                    # 1 店だけの系列は小見出し無し。在庫なしの TOUTEN は出ない
    plain = CheckResult("k", "紀伊國屋", "https://k", icon="📔", stocks=r.stocks[:2])
    assert chain_lines(plain) == ["📔 **[紀伊國屋](https://k)** 2/2", "📔 NAgoya Book Center（愛知・名古屋市） ─ 多い",
                                  "📔 くまざわ書店名古屋南店（愛知・名古屋市） ─ 少ない"]


def test_icon_fallback_is_stable():
    from bot.render import PALETTE, icon_for
    assert icon_for("x", "📘") == "📘" and icon_for("くまざわ書店") == "🔵"
    a = icon_for("未知の書店"); assert a in PALETTE and icon_for("未知の書店") == a
    assert all(i not in ("🟢", "🟡", "🔴", "⚪") for i in PALETTE)


def test_amazon_line_and_parser():
    from bot.lookup import AmazonInfo, parse_amazon
    from bot.render import amazon_line
    html = """<html><body>
      <div id="corePriceDisplay_desktop_feature_div"><span class="a-price"><span class="a-offscreen">￥693</span></span></div>
      <div id="olp_feature_div"><a class="olp-link">その他中古品、新品、コレクター商品 が ￥318から</a></div>
      <div id="availability"><span> 在庫あり。 </span></div></body></html>"""
    a = parse_amazon(html, "4101010013")
    assert a.price == "￥693" and a.other_price == "￥318" and a.availability.startswith("在庫あり") and a.fetched
    line = amazon_line(a)
    assert line == ("Amazon ￥693 ／ 中古 ￥318〜 ・ [商品ページ](https://www.amazon.co.jp/dp/4101010013)"
                    " ・ [Keepa](https://keepa.com/#!product/5-4101010013)")
    blocked = AmazonInfo(asin="4101010013")
    assert amazon_line(blocked).startswith("Amazon 価格取得できず") and "[Keepa]" in amazon_line(blocked)
    c = codes.parse("9784101010014")
    meta = BookMeta(title="t", price="¥629（税抜）", amazon=a)
    e = build_messages(c, meta, [], None, 1.0)[0][0]
    desc = e.description.split("\n")
    assert "定価 ¥629（税抜）" in desc and desc[desc.index("定価 ¥629（税抜）") + 1] == line
