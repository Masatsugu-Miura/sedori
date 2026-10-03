"""Amazon SP-API（Selling Partner API）で商品の価格を取る。出品者アカウントで自分用アプリを作ると使える。

必要な環境変数（.env）:
  SPAPI_CLIENT_ID      … LWA（Login with Amazon）のクライアント ID
  SPAPI_CLIENT_SECRET  … LWA のクライアントシークレット
  SPAPI_REFRESH_TOKEN  … セルフ認可で得たリフレッシュトークン
  SPAPI_MARKETPLACE_ID … 省略時は日本（A1VC38T7YXB528）
3 つが揃っていれば fetch_amazon は SP-API を使い、無ければ商品ページを読む方式に戻る。
Product Pricing API v0 の getItemOffers を 新品 / 中古 で 1 回ずつ呼ぶ（SigV4 署名は不要、LWA トークンだけ）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import aiohttp

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
ENDPOINT = os.environ.get("SPAPI_ENDPOINT", "https://sellingpartnerapi-fe.amazon.com")   # 極東（日本）リージョン
MARKETPLACE_JP = "A1VC38T7YXB528"
TIMEOUT = aiohttp.ClientTimeout(total=15)

_token_cache: dict[str, Any] = {"token": "", "expires": 0.0}


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ("SPAPI_CLIENT_ID", "SPAPI_CLIENT_SECRET", "SPAPI_REFRESH_TOKEN"))


def marketplace() -> str:
    return os.environ.get("SPAPI_MARKETPLACE_ID", "").strip() or MARKETPLACE_JP


async def access_token(session: aiohttp.ClientSession) -> str:
    """LWA のアクセストークン（約 1 時間有効）。期限内はキャッシュを返す。"""
    if _token_cache["token"] and time.time() < _token_cache["expires"] - 60:
        return _token_cache["token"]
    data = {"grant_type": "refresh_token", "refresh_token": os.environ["SPAPI_REFRESH_TOKEN"].strip(),
            "client_id": os.environ["SPAPI_CLIENT_ID"].strip(), "client_secret": os.environ["SPAPI_CLIENT_SECRET"].strip()}
    async with session.post(LWA_TOKEN_URL, data=data, timeout=TIMEOUT) as r:
        body = await r.json(content_type=None)
        if r.status != 200 or "access_token" not in body:
            raise RuntimeError(f"LWA token error HTTP {r.status}: {str(body)[:200]}")
    _token_cache["token"] = body["access_token"]
    _token_cache["expires"] = time.time() + float(body.get("expires_in", 3600))
    return _token_cache["token"]


async def get_item_offers(session: aiohttp.ClientSession, asin: str, condition: str) -> dict:
    """getItemOffers の payload（Summary / Offers）。condition は New / Used。"""
    token = await access_token(session)
    url = f"{ENDPOINT}/products/pricing/v0/items/{asin}/offers"
    params = {"MarketplaceId": marketplace(), "ItemCondition": condition, "CustomerType": "Consumer"}
    async with session.get(url, params=params, headers={"x-amz-access-token": token, "Accept": "application/json"},
                           timeout=TIMEOUT) as r:
        body = await r.json(content_type=None)
        if r.status != 200:
            raise RuntimeError(f"SP-API HTTP {r.status}: {str(body)[:200]}")
    return body.get("payload") or {}


def _yen(money: Optional[dict]) -> str:
    if not money or money.get("Amount") is None:
        return ""
    amt = float(money["Amount"])
    cur = money.get("CurrencyCode", "JPY")
    return f"￥{int(round(amt)):,}" if cur == "JPY" else f"{cur} {amt:,.2f}"


def _landed(offer_or_price: dict) -> Optional[dict]:
    """送料込み（LandedPrice）があればそれ、無ければ ListingPrice ＋ Shipping。"""
    if offer_or_price.get("LandedPrice"):
        return offer_or_price["LandedPrice"]
    lp, sh = offer_or_price.get("ListingPrice"), offer_or_price.get("Shipping")
    if lp and lp.get("Amount") is not None:
        total = float(lp["Amount"]) + (float(sh["Amount"]) if sh and sh.get("Amount") is not None else 0.0)
        return {"Amount": total, "CurrencyCode": lp.get("CurrencyCode", "JPY")}
    return None


def summarize(new_payload: dict, used_payload: dict) -> dict:
    """新品・中古の payload → {price（カート or 新品最安）, other_price（中古最安・送料込み）, new_count, used_count, fba_new}。"""
    out = {"price": "", "other_price": "", "new_count": 0, "used_count": 0, "fba_new": ""}
    ns = new_payload.get("Summary") or {}
    out["new_count"] = int(ns.get("TotalOfferCount") or 0)
    buybox = [b for b in (ns.get("BuyBoxPrices") or []) if (b.get("condition") or "").lower() == "new"]
    if buybox:
        out["price"] = _yen(_landed(buybox[0]))
    else:
        lows = [p for p in (ns.get("LowestPrices") or []) if (p.get("condition") or "").lower() == "new"]
        if lows:
            out["price"] = _yen(min((_landed(p) for p in lows if _landed(p)), key=lambda m: float(m["Amount"]), default=None))
    fba = [o for o in (new_payload.get("Offers") or []) if o.get("IsFulfilledByAmazon")]
    if fba:
        out["fba_new"] = _yen(min((_landed(o) for o in fba if _landed(o)), key=lambda m: float(m["Amount"]), default=None))
    us = used_payload.get("Summary") or {}
    out["used_count"] = int(us.get("TotalOfferCount") or 0)
    lows = [p for p in (us.get("LowestPrices") or []) if (p.get("condition") or "").lower() == "used"]
    offers = [o for o in (used_payload.get("Offers") or [])]
    cands = [_landed(p) for p in lows] + [_landed(o) for o in offers]
    cands = [c for c in cands if c]
    if cands:
        out["other_price"] = _yen(min(cands, key=lambda m: float(m["Amount"])))
    return out


async def get_catalog_item(session: aiohttp.ClientSession, asin: str) -> dict:
    """Catalog Items API（2022-04-01）で商品名と画像を取る → {"title", "image"}。無ければ空文字。"""
    token = await access_token(session)
    url = f"{ENDPOINT}/catalog/2022-04-01/items/{asin}"
    params = {"marketplaceIds": marketplace(), "includedData": "summaries,images"}
    async with session.get(url, params=params, headers={"x-amz-access-token": token, "Accept": "application/json"},
                           timeout=TIMEOUT) as r:
        body = await r.json(content_type=None)
        if r.status != 200:
            raise RuntimeError(f"SP-API catalog HTTP {r.status}: {str(body)[:200]}")
    return catalog_summary(body)


def catalog_summary(body: dict) -> dict:
    title = ""
    for s in body.get("summaries") or []:
        title = s.get("itemName") or title
        if title:
            break
    image = ""
    best = 0
    for grp in body.get("images") or []:
        for im in grp.get("images") or []:
            if (im.get("variant") or "MAIN") != "MAIN":
                continue
            size = int(im.get("width") or 0)
            if im.get("link") and size >= best:
                image, best = im["link"], size
    return {"title": title, "image": image}


async def fetch_prices(session: aiohttp.ClientSession, asin: str) -> dict:
    """新品・中古の価格と、商品名・画像をまとめた dict。価格が取れなければ例外、商品名・画像は取れなくても続行。"""
    new_payload = await get_item_offers(session, asin, "New")
    used_payload = await get_item_offers(session, asin, "Used")
    out = summarize(new_payload, used_payload)
    try:
        out.update(await get_catalog_item(session, asin))
    except Exception:  # noqa: BLE001
        out.update({"title": "", "image": ""})
    return out


def dumps(payload: dict) -> str:   # テスト・デバッグ用
    return json.dumps(payload, ensure_ascii=False)
