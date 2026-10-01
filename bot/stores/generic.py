"""汎用チェッカー：検索URLを開いて全文から在庫表記を拾う。/ リンクのみ。"""
from __future__ import annotations

from typing import Optional

import aiohttp

from ..codes import Code
from .base import Checker, CheckResult, Status


class GenericChecker(Checker):
    pass


class LinkOnlyChecker(Checker):
    async def check(self, session: aiohttp.ClientSession, code: Code, area: Optional[str] = None) -> CheckResult:
        url = self.url_for(code)
        res = CheckResult(chain_id=self.cfg.id, chain=self.cfg.name, url=url or self.cfg.home,
                          status=Status.LINK, verified=self.cfg.verified)
        if not url:
            res.message = "このコード種別では検索URLを作れません"
        return res
