"""同梱の stores.json が壊れていないか。"""
from pathlib import Path

from bot import codes
from bot.stores import CHECKERS, DEFAULT_PATH, load_configs, load_settings, region_keywords
from bot.stores.base import Checker


def test_bundled_config():
    s = load_settings(DEFAULT_PATH)
    assert s["home_regions"] == ["愛知", "京都"]
    assert set(s["home_regions"]) <= set(s["regions"])
    cfgs = load_configs(DEFAULT_PATH)
    ids = [c.id for c in cfgs]
    assert len(ids) == len(set(ids))
    book = codes.parse("9784101010014")
    mag = codes.parse("4910012345678")
    for c in cfgs:
        assert c.checker in CHECKERS, c.id
        url = Checker(c).url_for(book)
        assert url.startswith("http"), (c.id, url)
    # 雑誌JAN（ISBNなし）でもキーワード検索型の店はURLが作れること
    by_id = {c.id: c for c in cfgs}
    for sid in ("kinokuniya", "tsutaya", "bookoff", "amazon", "keepa", "animate"):
        assert Checker(by_id[sid]).url_for(mag).startswith("http"), sid
    assert "名古屋" in region_keywords("愛知", DEFAULT_PATH) and "四条" in region_keywords("京都", DEFAULT_PATH)
    assert Path(DEFAULT_PATH).name == "stores.json"
