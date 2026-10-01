"""書店チェッカーのレジストリと一括実行。"""
from __future__ import annotations

import asyncio
import json
import os
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


def load_configs(path: Optional[Path] = None) -> list[StoreConfig]:
    p = path or stores_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data["stores"] if isinstance(data, dict) else data
    return [StoreConfig.from_dict(d) for d in items]


def save_configs(cfgs: list[StoreConfig], path: Optional[Path] = None) -> None:
    p = path or stores_path()
    p.write_text(json.dumps({"stores": [c.__dict__ for c in cfgs]}, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")


def build(cfg: StoreConfig) -> Checker:
    cls = CHECKERS.get(cfg.checker, GenericChecker)
    return cls(cfg)


def known_ids() -> set[str]:
    return {c.id for c in load_configs()}


async def check_all(code: Code, area: Optional[str] = None, only: Optional[set[str]] = None,
                    concurrency: int = 6) -> list[CheckResult]:
    # only 指定時は無効チェーンも明示指定なら対象にする
    cfgs = [c for c in load_configs() if (c.id in only if only else c.enabled)]
    sem = asyncio.Semaphore(concurrency)
    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(connector=connector, cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        async def run(cfg: StoreConfig) -> CheckResult:
            async with sem:
                try:
                    return await asyncio.wait_for(build(cfg).check(session, code, area), timeout=20)
                except asyncio.TimeoutError:
                    return CheckResult(chain_id=cfg.id, chain=cfg.name,
                                       url=code.fill(cfg.search) or code.fill(cfg.search_alt) or cfg.home,
                                       status=Status.ERROR, message="タイムアウト", verified=cfg.verified)
        results = await asyncio.gather(*(run(c) for c in cfgs))
    results.sort(key=lambda r: (r.status.rank, r.chain))
    return list(results)
