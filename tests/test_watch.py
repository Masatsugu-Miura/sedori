"""日販系の在庫連携の監視: 通知文の出し分けと次回時刻。"""
from datetime import datetime

from bot.watch import JST, LinkStatus, load_state, message_for, save_state, seconds_until


def _st(ok: int, total: int = 8) -> LinkStatus:
    return LinkStatus("2026-10-02T12:00+09:00", total, ok, ["BOOKSえみたす新甚目寺店（愛知・あま市）"] * ok,
                      ["BOOKSえみたすピアゴ中村店"] * (total - ok))


def test_message_only_on_first_run_and_state_change():
    first = message_for(_st(0), None)
    assert first and "監視を始めました" in first and "エラー中" in first and "8店中 0店" in first
    assert message_for(_st(0), _st(0)) is None                    # 変化なし → 通知しない
    rec = message_for(_st(5), _st(0))
    assert rec and rec.startswith("✅") and "復旧しました" in rec and "5店が応答" in rec
    assert message_for(_st(5), _st(3)) is None                    # 復旧中のまま → 通知しない
    broken = message_for(_st(0), _st(5))
    assert broken and broken.startswith("⚠️")
    forced = message_for(_st(0), _st(0), force=True)
    assert forced and forced.startswith("ℹ️") and "まだエラー中" in forced
    none_listed = message_for(LinkStatus("t", 0, 0), None)
    assert none_listed and "見つかりません" in none_listed


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    assert load_state(p) is None
    save_state(_st(2), p)
    back = load_state(p)
    assert back == _st(2) and back.recovered


def test_seconds_until_noon_jst():
    morning = datetime(2026, 10, 2, 9, 30, tzinfo=JST)
    assert seconds_until(12, morning) == 2.5 * 3600
    afternoon = datetime(2026, 10, 2, 12, 0, 1, tzinfo=JST)
    assert abs(seconds_until(12, afternoon) - (24 * 3600 - 1)) < 1
