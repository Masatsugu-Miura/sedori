"""書店チェッカーのレジストリと一括実行。"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Optional

import aiohttp

from ..codes import Code
from .animate import AnimateChecker
from .base import Checker, CheckResult, Status, StoreConfig
from .book1st import Book1stChecker
from .generic import GenericChecker, LinkOnlyChecker
from .honto import HontoChecker
from .kinokuniya import KinokuniyaChecker
from .kumazawa import KumazawaChecker
from .maruzenjunkudo import MaruzenJunkudoChecker
from .miraiya import MiraiyaChecker
from .openbs import OpenBSChecker
from .sanseido import SanseidoChecker
from .sanyodo import SanyodoChecker
from .tsutaya import TsutayaChecker
from .yurindo import YurindoChecker

CHECKERS: dict[str, type[Checker]] = {
    "generic": GenericChecker,
    "link": LinkOnlyChecker,
    "kinokuniya": KinokuniyaChecker,
    "honto": HontoChecker,
    "tsutaya": TsutayaChecker,
    "book1st": Book1stChecker,
    "animate": AnimateChecker,
    "miraiya": MiraiyaChecker,
    "sanyodo": SanyodoChecker,
    "sanseido": SanseidoChecker,
    "maruzenjunkudo": MaruzenJunkudoChecker,
    "yurindo": YurindoChecker,
    "kumazawa": KumazawaChecker,
    "openbs": OpenBSChecker,
}

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "stores.json"
# 1 チェーンあたりの上限秒。全国指定で紀伊國屋（72 店 POST ≒ 20 秒）・TSUTAYA（約 33 ページ ≒ 50 秒、自前の打ち切りあり）が収まる値
CHAIN_TIMEOUT = 75


def stores_path() -> Path:
    return Path(os.environ.get("STORES_FILE", DEFAULT_PATH))


def load_settings(path: Optional[Path] = None) -> dict:
    """stores.json 全体（stores / regions / home_regions）。"""
    p = path or stores_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"stores": data}
    data.setdefault("stores", [])
    data.setdefault("regions", {})
    data.setdefault("home_regions", [])
    return data


def load_configs(path: Optional[Path] = None) -> list[StoreConfig]:
    return [StoreConfig.from_dict(d) for d in load_settings(path)["stores"]]


def save_configs(cfgs: list[StoreConfig], path: Optional[Path] = None) -> None:
    p = path or stores_path()
    data = load_settings(p) if p.exists() else {}
    data["stores"] = [c.__dict__ for c in cfgs]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_id(name: str, url: str, taken: set[str]) -> str:
    """店名（無ければ URL のホスト名）から英数字の ID を作る。taken と重ならないよう番号を付け、taken に追加する。"""
    base = re.sub(r"[^a-z0-9]+", "", name.lower().encode("ascii", "ignore").decode())
    if not base and url:
        host = (re.match(r"https?://([^/]+)", url) or [None, ""])[1]
        base = re.sub(r"^www\.|\.(co\.jp|com|jp|net)$", "", host).replace(".", "")
    base = base or "store"
    sid, i = base, 2
    while sid in taken:
        sid, i = f"{base}{i}", i + 1
    taken.add(sid)
    return sid


def region_keywords(area: Optional[str], path: Optional[Path] = None) -> list[str]:
    """『愛知』のような地域名なら登録キーワード群に展開、それ以外はそのまま1語。『地元』は home_regions 全部。"""
    if not area:
        return []
    settings = load_settings(path)
    regions: dict[str, list[str]] = settings["regions"]
    names = [a.strip() for a in re.split(r"[、,/／\s+]+", area) if a.strip()]
    out: list[str] = []
    for n in names:
        if n in ("地元", "home", "local"):
            for r in settings["home_regions"]:
                out += regions.get(r, [r])
            continue
        key = n[:-1] if n.endswith(("県", "府", "都", "道")) and n[:-1] in regions else n
        out += regions.get(key, [n])
    seen: set[str] = set()
    return [k for k in out if not (k in seen or seen.add(k))]


def home_regions(path: Optional[Path] = None) -> list[str]:
    return list(load_settings(path)["home_regions"])


def manual_checks(area: Optional[str], path: Optional[Path] = None) -> list[StoreConfig]:
    """自動検索できないが、アプリや電話で調べられる店（enabled=false で check_by 付き）。
    地域指定時はその地域に出店している店だけ。"""
    kws = region_keywords(area, path)
    return [c for c in load_configs(path)
            if not c.enabled and c.check_by and (not kws or c.serves(kws))]


def region_groups(area: Optional[str], path: Optional[Path] = None) -> dict[str, list[str]]:
    """『地元』『愛知,京都』のように複数地域をまとめて検索するとき、表示を地域ごとに分けるための
    {地域名: キーワード群}。単一地域や未指定なら {}（分けない）。"""
    if not area:
        return {}
    settings = load_settings(path)
    names: list[str] = []
    for n in (a.strip() for a in re.split(r"[、,/／\s+]+", area) if a.strip()):
        if n in ("地元", "home", "local"):
            names += settings["home_regions"]
        else:
            names.append(n[:-1] if n.endswith(("県", "府", "都", "道")) and n[:-1] in settings["regions"] else n)
    names = [n for i, n in enumerate(names) if n not in names[:i]]
    if len(names) < 2:
        return {}
    return {n: region_keywords(n, path) for n in names}


def build(cfg: StoreConfig) -> Checker:
    cls = CHECKERS.get(cfg.checker, GenericChecker)
    return cls(cfg)


def known_ids() -> set[str]:
    return {c.id for c in load_configs()}


async def check_all(code: Code, area: Optional[str] = None, only: Optional[set[str]] = None,
                    concurrency: int = 6) -> list[CheckResult]:
    """area は地域名（regions のキー）／『地元』／任意の文字列。キーワード群に展開して店名を絞る。"""
    keywords = region_keywords(area)
    # only 指定時は無効チェーンも明示指定なら対象にする
    cfgs = [c for c in load_configs() if (c.id in only if only else c.enabled)]
    sem = asyncio.Semaphore(concurrency)
    # 1チェーンの中で複数ページを並行取得する店があるので、全体の上限は多めに・同一サイトへは 4 本まで
    connector = aiohttp.TCPConnector(limit=concurrency * 3, limit_per_host=4)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True),
                                     trust_env=True) as session:
        async def run(cfg: StoreConfig) -> CheckResult:
            async with sem:
                try:
                    return await asyncio.wait_for(build(cfg).check(session, code, keywords), timeout=CHAIN_TIMEOUT)
                except asyncio.TimeoutError:
                    return CheckResult(chain_id=cfg.id, chain=cfg.name,
                                       url=code.fill(cfg.search) or code.fill(cfg.search_alt) or cfg.home,
                                       status=Status.ERROR, message="タイムアウト", verified=cfg.verified,
                                       icon=cfg.icon)
        results = await asyncio.gather(*(run(c) for c in cfgs))
    results.sort(key=lambda r: (r.status.rank, r.chain))
    return list(results)
