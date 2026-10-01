"""ローカルの疑似書店サイトでチェッカー全体を通す。"""
import json

import pytest
import pytest_asyncio
from aiohttp import web

from bot import codes
from bot.stores import check_all
from bot.stores import honto as honto_mod
from bot.stores.base import Status

KINO = """<html><body><table id="stock"><tr><th>店舗</th><th>在庫</th></tr>
<tr><td>新宿本店</td><td>在庫あり</td></tr><tr><td>梅田本店</td><td>在庫わずか</td></tr>
<tr><td>札幌本店</td><td>在庫なし</td></tr></table></body></html>"""
HONTO_SEARCH = '<html><body><a href="/netstore/pd_12345.html">本</a></body></html>'
HONTO_STORE = """<html><body><ul><li>ジュンク堂書店 池袋本店 在庫あり</li><li>丸善 丸の内本店 在庫わずか</li>
<li>文教堂 赤羽店 在庫なし</li></ul></body></html>"""
GENERIC_SJIS = "<html><head><meta charset=\"Shift_JIS\"></head><body><div>横浜西口店：○</div><div>藤沢店：×</div></body></html>"


@pytest_asyncio.fixture
async def server(tmp_path, monkeypatch):
    app = web.Application()
    app.router.add_get("/kino/{x}", lambda r: web.Response(text=KINO, content_type="text/html"))
    app.router.add_get("/netstore/search_{x}.html", lambda r: web.Response(text=HONTO_SEARCH, content_type="text/html"))
    app.router.add_get("/gen", lambda r: web.Response(body=GENERIC_SJIS.encode("cp932"), content_type="text/html"))
    app.router.add_get("/err", lambda r: web.Response(status=503))
    app.router.add_get("/empty", lambda r: web.Response(text="<html>nothing</html>", content_type="text/html"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    base = f"http://127.0.0.1:{port}"
    cfg = {"stores": [
        {"id": "k", "name": "紀伊國屋(mock)", "checker": "kinokuniya", "search": base + "/kino/{isbn13}", "stores": ["新宿", "梅田"]},
        {"id": "h", "name": "honto(mock)", "checker": "honto", "search": base + "/netstore/search_10{isbn13}.html"},
        {"id": "g", "name": "generic(mock)", "checker": "generic", "search": base + "/gen"},
        {"id": "e", "name": "error(mock)", "checker": "generic", "search": base + "/err"},
        {"id": "n", "name": "empty(mock)", "checker": "generic", "search": base + "/empty"},
        {"id": "l", "name": "link(mock)", "checker": "link", "search": "http://example.com/?q={asin}"},
        {"id": "m", "name": "mag-only(mock)", "checker": "link", "search": "http://example.com/{isbn13}",
         "search_alt": "http://example.com/s?k={code}"},
        {"id": "x", "name": "unreachable(mock)", "checker": "generic", "search": "http://127.0.0.1:1/x"},
        {"id": "off", "name": "disabled(mock)", "checker": "link", "search": "http://example.com/", "enabled": False},
    ]}
    p = tmp_path / "stores.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STORES_FILE", str(p))

    orig = honto_mod.HontoChecker.fetch

    async def fetch(self, session, url):
        if "pd-store_" in url:
            return 200, HONTO_STORE
        return await orig(self, session, url)
    monkeypatch.setattr(honto_mod.HontoChecker, "fetch", fetch)
    yield base
    await runner.cleanup()


@pytest.mark.asyncio
async def test_all_checkers(server):
    code = codes.parse("9784101010014")
    res = {r.chain_id: r for r in await check_all(code)}
    assert "off" not in res
    assert res["k"].status == Status.IN_STOCK and [s.store for s in res["k"].stocks] == ["新宿本店", "梅田本店"]
    assert res["h"].status == Status.IN_STOCK and res["h"].url.endswith("pd-store_12345.html")
    assert [s.store for s in res["h"].stocks] == ["ジュンク堂書店 池袋本店", "丸善 丸の内本店", "文教堂 赤羽店"]
    assert res["g"].status == Status.IN_STOCK and {s.store for s in res["g"].stocks} == {"横浜西口店", "藤沢店"}
    assert res["e"].status == Status.ERROR and "503" in res["e"].message
    assert res["n"].status == Status.UNKNOWN
    assert res["l"].status == Status.LINK and res["l"].url == "http://example.com/?q=4101010013"
    assert res["x"].status == Status.ERROR


@pytest.mark.asyncio
async def test_area_filter_and_only(server):
    code = codes.parse("9784101010014")
    res = {r.chain_id: r for r in await check_all(code, area="池袋", only={"h", "k"})}
    assert set(res) == {"h", "k"}
    assert [s.store for s in res["h"].stocks] == ["ジュンク堂書店 池袋本店"]
    assert res["k"].status == Status.OUT and "行なし" in res["k"].message


@pytest.mark.asyncio
async def test_magazine_uses_alt_url(server):
    mag = codes.parse("4910012345678")
    res = {r.chain_id: r for r in await check_all(mag, only={"m", "l"})}
    assert res["m"].url == "http://example.com/s?k=4910012345678" and res["m"].status == Status.LINK
    assert res["l"].status == Status.LINK and "作れません" in res["l"].message


@pytest.mark.asyncio
async def test_404_falls_back_to_alt(server, tmp_path, monkeypatch):
    import json as _json
    cfg = {"stores": [{"id": "f", "name": "fallback(mock)", "checker": "generic",
                       "search": server + "/missing/{isbn13}", "search_alt": server + "/gen"}]}
    p = tmp_path / "s2.json"
    p.write_text(_json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("STORES_FILE", str(p))
    res = (await check_all(codes.parse("9784101010014")))[0]
    assert res.status == Status.IN_STOCK and res.url.endswith("/gen") and "検索ページ" in res.message
