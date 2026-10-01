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
from .base import Checker, CheckResult, Status, StoreConfig
from .generic import GenericChecker, LinkOnlyChecker
from .honto import HontoChecker
from .kinokuniya import KinokuniyaChecker

CHECKERS: dict[str, type[Checker]] = {
    "generic": GenericChecker,
    "link": LinkOnlyChecker,
    "kinokuniya": KinokuniyaChecker,
    "honto": HontoChecker,
}

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "stores.json"


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
    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        async def run(cfg: StoreConfig) -> CheckResult:
            async with sem:
                try:
                    return await asyncio.wait_for(build(cfg).check(session, code, keywords), timeout=20)
                except asyncio.TimeoutError:
                    return CheckResult(chain_id=cfg.id, chain=cfg.name,
                                       url=code.fill(cfg.search) or code.fill(cfg.search_alt) or cfg.home,
                                       status=Status.ERROR, message="タイムアウト", verified=cfg.verified)
        results = await asyncio.gather(*(run(c) for c in cfgs))
    results.sort(key=lambda r: (r.status.rank, r.chain))
    return list(results)
