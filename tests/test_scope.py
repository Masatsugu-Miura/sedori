"""検索範囲の既定（地元）と『全国』『地域名』『地域でない言葉』の扱い。"""
from __future__ import annotations

import os

os.environ.setdefault("DISCORD_TOKEN", "")

from bot import main as m  # noqa: E402


def test_default_is_local_and_zenkoku_is_nationwide():
    assert m.resolve_area(None) == ("地元", "地元（愛知・京都）")
    assert m.resolve_area("") == ("地元", "地元（愛知・京都）")
    assert m.resolve_area("全国") == (None, "全国")
    assert m.resolve_area("all") == (None, "全国")
    assert m.resolve_area("地元")[0] == "地元"
    assert m.resolve_area("愛知") == ("愛知", "愛知")


def test_known_area():
    for ok in ("全国", "地元", "愛知", "愛知県", "京都", "京都府", "東京", "北海道", "愛知,京都", "愛知 京都"):
        assert m.known_area(ok), ok
    for ng in ("", "在庫ある？", "ありますか", "お願い", "新宿"):
        assert not m.known_area(ng), ng


def test_plain_message_ignores_non_area_words():
    assert m.parse_plain_message("9784101010014") == ("9784101010014", None)
    assert m.parse_plain_message("9784101010014 全国") == ("9784101010014", "全国")
    assert m.parse_plain_message("9784101010014 愛知") == ("9784101010014", "愛知")
    assert m.parse_plain_message("9784101010014 在庫ある？") == ("9784101010014", None)
