"""地元（愛知・京都）検索の結果を地域ごとの区画に分ける表示。"""
from bot import codes
from bot.lookup import BookMeta
from bot.render import LINKS_SECTION, build_messages
from bot.stores import region_groups
from bot.stores.base import CheckResult, Status, StoreStock, assign_region


def test_region_groups():
    g = region_groups("地元")
    assert list(g) == ["愛知", "京都"] and "名古屋" in g["愛知"] and "宇治" in g["京都"]
    assert region_groups("愛知,京都府") == g
    assert region_groups("愛知") == {} and region_groups(None) == {} and region_groups("全国") == {}


def test_assign_region_uses_label_then_keywords():
    g = region_groups("地元")
    assert assign_region("丸善 名古屋本店（愛知・名古屋市）", g) == "愛知"
    assert assign_region("丸善 京都本店（京都・京都市）", g) == "京都"
    assert assign_region("TSUTAYA 大曽根店（愛知県）", g) == "愛知"
    assert assign_region("名古屋本店", g) == "愛知"                      # ラベル無し → 地名キーワード
    assert assign_region("アバンティブックセンター洛北店（京都市）", g) == "京都"
    assert assign_region("ジュンク堂書店 池袋本店（東京・豊島区）", g) is None
    assert assign_region("新宿店（東京都）", g) is None                   # 『東京都』を京都と間違えない


def test_local_results_are_split_into_aichi_and_kyoto_sections():
    code = codes.parse("9784101010014")
    g = region_groups("地元")
    mj = CheckResult("mj", "丸善ジュンク堂書店", "https://mj", stocks=[
        StoreStock("丸善 名古屋本店（愛知・名古屋市）", Status.LOW, "残り2点"),
        StoreStock("丸善 京都本店（京都・京都市）", Status.IN_STOCK, "在庫あり 6点"),
        StoreStock("ジュンク堂書店 名古屋店（愛知・名古屋市）", Status.OUT, "在庫なし")])
    mj.summarize()
    ss = CheckResult("ss", "三省堂書店", "https://ss", stocks=[StoreStock("名古屋本店", Status.IN_STOCK, "○")])
    ss.summarize()
    link = CheckResult("ogaki", "大垣書店", "https://og", status=Status.LINK)
    far = CheckResult("yurindo", "有隣堂", "https://yu", status=Status.OUT, message="指定店舗・地域の行なし（他 36 店は表示あり）")
    msgs = build_messages(code, BookMeta(title="t"), [mj, ss, link, far], "地元", 1.0, "地元（愛知・京都）",
                          serves={"mj": True, "ss": True, "ogaki": True, "yurindo": False}, groups=g)
    embeds = [e for m in msgs for e in m]
    titles = [e.title for e in embeds]
    assert titles[0] == "t" and "愛知 2店（多い1 少ない1） ／ 京都 1店（多い1 少ない0）" in embeds[0].description
    assert titles[1:4] == ["📍 愛知", "📍 京都", LINKS_SECTION]
    aichi, kyoto, links = embeds[1], embeds[2], embeds[3]
    assert [f.name for f in aichi.fields] == ["三省堂書店", "丸善ジュンク堂書店"]
    assert "丸善 名古屋本店 ─ 2個" in aichi.fields[1].value and "京都本店" not in aichi.fields[1].value
    assert "（愛知" not in aichi.fields[1].value            # 区画名で分かる地域ラベルは出さない
    assert aichi.fields[1].value.startswith("多い 0店 / 少ない 1店 / なし 1店（確認 2店）")
    assert [f.name for f in kyoto.fields] == ["丸善ジュンク堂書店"]
    assert "丸善 京都本店 ─ 6個" in kyoto.fields[0].value and "名古屋" not in kyoto.fields[0].value
    assert links.fields[0].name == "🔗 大垣書店"
    # 地域外のチェーンは従来どおり末尾の 1 フィールドにまとまる
    assert embeds[-1].fields[-1].name.startswith("この地域に該当店舗なし")
    assert "[有隣堂](https://yu)" in embeds[-1].fields[-1].value


def test_single_region_is_not_split():
    code = codes.parse("9784101010014")
    r = CheckResult("ss", "三省堂書店", "https://ss", stocks=[StoreStock("名古屋本店", Status.IN_STOCK, "○")])
    r.summarize()
    msgs = build_messages(code, BookMeta(title="t"), [r], "愛知", 1.0, "愛知", serves={"ss": True},
                          groups=region_groups("愛知"))
    assert [e.title for e in msgs[0]] == ["t"] and [f.name for f in msgs[0][0].fields] == ["三省堂書店"]
