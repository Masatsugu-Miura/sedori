"""自動検索できない店を『アプリで確認』『電話で確認』として結果に載せる。"""
from bot import codes
from bot.lookup import BookMeta
from bot.render import MANUAL_SECTION, build_messages, manual_fields
from bot.stores import manual_checks, region_groups
from bot.stores.base import CheckResult, Status, StoreConfig, StoreStock


def test_manual_checks_filtered_by_area():
    ids_all = {c.id for c in manual_checks(None)}
    assert {"sanseidokyoto", "villagevanguard", "seibunkan"} <= ids_all
    assert "emitasu" not in ids_all and "culcos" not in ids_all   # 在庫が自動で出る店はアプリ一覧に出さない（重複）
    kyoto = {c.id for c in manual_checks("京都")}
    assert {"sanseidokyoto", "whitebooks", "otowado", "tojishoin", "endoshoten", "keibunsha", "villagevanguard"} <= kyoto
    assert "yumeya" not in kyoto
    aichi = {c.id for c in manual_checks("愛知")}
    assert {"honnookoku", "yumeya", "shobunkan", "bonanza", "comicalhouse", "doumeishorin", "onsevendays",
            "ryusuishobo", "libretto", "meglia", "honyaclubaichi"} <= aichi and "sanseidokyoto" not in aichi
    assert "surugaya" not in aichi                    # 駿河屋はリンク付きで有効（自動検索欄に出る）なので手動一覧には載せない
    assert {c.id for c in manual_checks("地元")} == kyoto | aichi
    assert all(not c.enabled and c.check_by for c in manual_checks(None))


def test_manual_fields_grouped_by_method():
    cfgs = [StoreConfig(id="a", name="えみたす", search="", enabled=False, check_by=["ほんらぶ", "本コレ"], hint="約15店"),
            StoreConfig(id="b", name="三盛堂", search="", enabled=False, check_by=["電話"], phone="075-431-2937", hint="千本"),
            StoreConfig(id="c", name="対象外", search="", enabled=False)]
    fields = manual_fields(cfgs)
    assert [n for n, _ in fields] == ["📱 ほんらぶ（日販のアプリ）", "📱 本コレ（TSUTAYA / CCC のアプリ）", "📞 電話で確認"]
    assert fields[0][1] == ["えみたす（約15店）"] and fields[2][1] == ["三盛堂 `075-431-2937`（千本）"]


def test_manual_section_rendered_after_stock_sections():
    code = codes.parse("9784101010014")
    r = CheckResult("ss", "三省堂書店", "https://ss", stocks=[StoreStock("名古屋本店", Status.IN_STOCK, "○")])
    r.summarize()
    manual = manual_checks("地元")
    msgs = build_messages(code, BookMeta(title="t"), [r], "地元", 1.0, "地元", serves={"ss": True},
                          groups=region_groups("地元"), manual=manual)
    embeds = [e for m in msgs for e in m]
    titles = [e.title for e in embeds]
    assert MANUAL_SECTION in titles and titles.index(MANUAL_SECTION) > titles.index("📍 愛知")
    sec = embeds[titles.index(MANUAL_SECTION)]
    names = [f.name for f in sec.fields]
    assert names[0].startswith("📱 ほんらぶ") and any(n.startswith("📞 電話") for n in names)
    text = "\n".join(f.value for f in sec.fields)
    assert "精文館" in text and "075-431-2937" in text
    # 全国・単一地域では見出し Embed を作らずフィールドとして続ける
    msgs2 = build_messages(code, BookMeta(title="t"), [r], None, 1.0, manual=manual_checks(None))
    assert [e.title for e in msgs2[0]] == ["t", None] and any(f.name.startswith("📞 電話") for f in msgs2[0][1].fields)
