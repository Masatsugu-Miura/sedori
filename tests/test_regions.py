import json

import pytest

from bot import codes
from bot.lookup import BookMeta
from bot.render import build_messages
from bot.stores import home_regions, load_configs, load_settings, region_keywords, save_configs
from bot.stores.base import CheckResult, Status, StoreConfig, StoreStock, filter_stores


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    p = tmp_path / "stores.json"
    p.write_text(json.dumps({
        "home_regions": ["愛知", "京都"],
        "regions": {"愛知": ["愛知", "名古屋", "豊橋"], "京都": ["京都", "四条", "宇治"]},
        "stores": [
            {"id": "a", "name": "A", "search": "https://a/{isbn13}", "prefectures": ["全国"]},
            {"id": "b", "name": "B", "search": "https://b/{isbn13}", "prefectures": ["神奈川", "東京"]},
            {"id": "c", "name": "C", "search": "https://c/{isbn13}", "prefectures": ["愛知", "岐阜"]},
        ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STORES_FILE", str(p))
    return p


def test_region_keywords(settings_file):
    assert region_keywords("愛知") == ["愛知", "名古屋", "豊橋"]
    assert region_keywords("愛知県") == ["愛知", "名古屋", "豊橋"]
    assert region_keywords("京都府") == ["京都", "四条", "宇治"]
    assert region_keywords("地元") == ["愛知", "名古屋", "豊橋", "京都", "四条", "宇治"]
    assert region_keywords("愛知,京都") == region_keywords("地元")
    assert region_keywords("新宿") == ["新宿"]
    assert region_keywords(None) == [] and region_keywords("") == []
    assert home_regions() == ["愛知", "京都"]


def test_filter_by_keywords():
    stocks = [StoreStock("名古屋本店", Status.IN_STOCK), StoreStock("新宿本店", Status.IN_STOCK),
              StoreStock("イオンモール京都店", Status.LOW)]
    got = filter_stores(stocks, [], ["名古屋", "京都"])
    assert [s.store for s in got] == ["名古屋本店", "イオンモール京都店"]


def test_serves(settings_file):
    cfgs = {c.id: c for c in load_configs()}
    kws = region_keywords("愛知")
    assert cfgs["a"].serves(kws) and cfgs["c"].serves(kws) and not cfgs["b"].serves(kws)
    assert StoreConfig(id="x", name="x", search="").serves(kws)   # 未設定は不明＝表示


def test_save_keeps_regions(settings_file):
    cfgs = load_configs()
    cfgs.append(StoreConfig(id="d", name="D", search="https://d/{isbn13}"))
    save_configs(cfgs)
    s = load_settings()
    assert s["home_regions"] == ["愛知", "京都"] and "愛知" in s["regions"] and [c["id"] for c in s["stores"]] == ["a", "b", "c", "d"]


def test_region_mode_rendering():
    code = codes.parse("9784101010014")
    hit = CheckResult("a", "A", "https://a", stocks=[StoreStock("名古屋本店", Status.IN_STOCK, "在庫あり")])
    hit.summarize()
    miss = CheckResult("b", "B", "https://b", status=Status.OUT, message="指定店舗・地域の行なし（他 5 店は表示あり）")
    local_link = CheckResult("c", "C", "https://c", status=Status.LINK)
    far_link = CheckResult("d", "D", "https://d", status=Status.LINK)
    msgs = build_messages(code, BookMeta(title="t"), [hit, miss, local_link, far_link], "愛知", 1.0, "愛知",
                          serves={"a": True, "b": False, "c": True, "d": False})
    e = msgs[0][1]
    assert e.description.split("\n") == ["**[A](https://a)** 1/1", "名古屋本店 ─ 多い", "🔗 [C](https://c)"]
    assert e.fields[-1].name.startswith("この地域に該当店舗なし")
    rest = e.fields[-1].value
    assert "[B](https://b)" in rest and "[D](https://d)" in rest and "行なし" in rest
    assert "検索範囲: **愛知**" in msgs[0][0].description


def test_nationwide_rendering_has_no_rest_field():
    code = codes.parse("9784101010014")
    r = CheckResult("b", "B", "https://b", status=Status.OUT, message="x")
    msgs = build_messages(code, BookMeta(), [r], None, 1.0)
    assert msgs[0][1].description == "🔴 [B](https://b) x" and "全国" in msgs[0][0].description
