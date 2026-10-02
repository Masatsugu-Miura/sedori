"""SP-API の応答から価格をまとめる部分（通信はしない）。"""
from bot import spapi
from bot.render import amazon_line
from bot.lookup import AmazonInfo

NEW = {"Summary": {"TotalOfferCount": 7,
                   "LowestPrices": [{"condition": "new", "fulfillmentChannel": "Merchant",
                                     "ListingPrice": {"Amount": 650, "CurrencyCode": "JPY"}, "Shipping": {"Amount": 250, "CurrencyCode": "JPY"},
                                     "LandedPrice": {"Amount": 900, "CurrencyCode": "JPY"}}],
                   "BuyBoxPrices": [{"condition": "New", "ListingPrice": {"Amount": 693, "CurrencyCode": "JPY"},
                                     "Shipping": {"Amount": 0, "CurrencyCode": "JPY"}, "LandedPrice": {"Amount": 693, "CurrencyCode": "JPY"}}]},
       "Offers": [{"IsFulfilledByAmazon": True, "ListingPrice": {"Amount": 693, "CurrencyCode": "JPY"}, "Shipping": {"Amount": 0, "CurrencyCode": "JPY"}},
                  {"IsFulfilledByAmazon": False, "ListingPrice": {"Amount": 650, "CurrencyCode": "JPY"}, "Shipping": {"Amount": 250, "CurrencyCode": "JPY"}}]}
USED = {"Summary": {"TotalOfferCount": 23,
                    "LowestPrices": [{"condition": "used", "ListingPrice": {"Amount": 1, "CurrencyCode": "JPY"},
                                      "Shipping": {"Amount": 350, "CurrencyCode": "JPY"}, "LandedPrice": {"Amount": 351, "CurrencyCode": "JPY"}}]},
        "Offers": [{"SubCondition": "good", "ListingPrice": {"Amount": 318, "CurrencyCode": "JPY"}, "Shipping": {"Amount": 0, "CurrencyCode": "JPY"}}]}


def test_summarize_prefers_buybox_and_landed_used_price():
    d = spapi.summarize(NEW, USED)
    assert d == {"price": "￥693", "other_price": "￥318", "new_count": 7, "used_count": 23, "fba_new": "￥693"}
    a = AmazonInfo(asin="4101010013", fetched=True, source="spapi", **d)
    assert amazon_line(a).startswith("[Amazon](https://www.amazon.co.jp/dp/4101010013) ￥693（新品 7件） ／ 中古 ￥318〜（23件） ・ [Keepa]")


def test_summarize_without_buybox_uses_lowest_new():
    new = {"Summary": {"TotalOfferCount": 1, "LowestPrices": NEW["Summary"]["LowestPrices"]}, "Offers": []}
    d = spapi.summarize(new, {})
    assert d["price"] == "￥900" and d["other_price"] == "" and d["used_count"] == 0 and d["fba_new"] == ""


def test_configured_requires_all_three(monkeypatch):
    for k in ("SPAPI_CLIENT_ID", "SPAPI_CLIENT_SECRET", "SPAPI_REFRESH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert not spapi.configured()
    monkeypatch.setenv("SPAPI_CLIENT_ID", "a"); monkeypatch.setenv("SPAPI_CLIENT_SECRET", "b")
    assert not spapi.configured()
    monkeypatch.setenv("SPAPI_REFRESH_TOKEN", "c")
    assert spapi.configured()
