"""Audit R-03: валюта в аукционе — честный договор вместо «cur из анкеты,
цены в рублях».

До этой правки Python отдавал `cur` из настроек партнёра, а цены — в RUB;
Go отвечал только RUB и отказывал всем, кто не принимает рубли. Договор:

* внутри всё в RUB (цена кандидата, резерв, ledger);
* валюта ответа = fx.choose_currency(req.cur, валюта расчётов партнёра);
* один курс (ЦБ × (1 + наценка владельца кампании)) на бид: floor -> RUB,
  цена RUB -> валюта ответа, ${AUCTION_PRICE} -> RUB при win;
* нет курса — no-bid, а не ставка «как будто в рублях».

Числа общие с bidder-go/internal/rtb/fx_test.go: кандидат 120 ₽ CPM, партнёр
в USD, курс ЦБ 90, наценка по умолчанию 3% -> 92.7; ставка 1.2945 USD.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import fx
import obs_metrics
from routers import rtb

USD_RATE = 90.0
EFFECTIVE = 92.7  # 90 × 1.03


# ------------------------------------------------------------- pure fx ---

def test_choose_currency_mirrors_go():
    cases = [
        (None, None, "RUB"),
        (None, "USD", "USD"),
        (["USD"], "USD", "USD"),
        (["usd", "rub"], "USD", "USD"),
        (["RUB"], "USD", "RUB"),
        (["USD", "RUB"], "EUR", "RUB"),
        (["USD"], "RUB", None),
        (["USD"], "EUR", None),
    ]
    for req_cur, partner, want in cases:
        assert fx.choose_currency(req_cur, partner) == want, (req_cur, partner)


def test_effective_rate_mirrors_go():
    rates = {"USD": USD_RATE}
    assert fx.effective_rate("RUB", None, None) == 1.0
    assert fx.effective_rate("USD", rates, None) == pytest.approx(EFFECTIVE)
    assert fx.effective_rate("usd", rates, 10) == pytest.approx(99.0)
    assert fx.effective_rate("EUR", rates, None) is None
    assert fx.effective_rate("USD", {"USD": 0}, None) is None
    assert fx.effective_rate("USD", {"USD": "garbage"}, None) is None


def test_floor_currency_mirrors_go():
    assert fx.floor_currency("eur", ["USD"], "RUB") == "EUR"
    assert fx.floor_currency(None, ["USD", "RUB"], "EUR") == "USD"
    assert fx.floor_currency(None, None, "usd") == "USD"
    assert fx.floor_currency(None, None, None) == "RUB"


# ------------------------------------------------------- bid harness -----

class _Redis:
    """Записывающий Redis: bidmeta:* сохраняем, всё остальное — нейтрально."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, *_, **__):
        self.store[key] = value
        return True

    async def get(self, key, *_):
        return self.store.get(key)

    async def exists(self, *_):
        return 0

    async def incr(self, *_):
        return 1

    async def expire(self, *_):
        return True

    async def eval(self, *_, **__):
        return 1

    async def evalsha(self, *_, **__):
        return 1

    async def delete(self, *_):
        return 1

    def bidmeta(self) -> dict:
        metas = [json.loads(v) for k, v in self.store.items() if k.startswith("bidmeta:")]
        assert len(metas) == 1, f"want exactly one bidmeta, got {len(metas)}"
        return metas[0]


def _partner(currency: str | None, bid_floor: float = 0.0) -> dict:
    return {
        "id": 5, "endpoint_key": "key1", "status": "active", "qps_limit": 0,
        "auth_token_hash": "x", "token_prev_hash": None, "token_prev_valid_until": None,
        "ip_whitelist": [], "allowed_ad_types": ["banner"], "seat_id": None,
        "bid_floor": bid_floor, "currency": currency,
    }


def _candidate(markup=None) -> dict:
    return {
        "li_id": 11, "bid_price": 120.0, "bid_type": "cpm", "max_bid": None, "li_budget": None,
        "targeting": {}, "campaign_id": 1, "owner_id": 7, "camp_budget": 1000.0,
        "frequency_cap_impressions": None, "frequency_cap_period_hours": None, "frequency_caps": [],
        "pacing": "asap", "advertiser_id": 3, "bid_shading": False, "objective": "awareness",
        "clean_traffic_only": False, "adv_domain": "adv.example", "adv_balance": 10_000.0,
        "total_budget": 0, "spent_total": 0,
        "creative_id": 21, "type": "banner", "width": 300, "height": 250,
        "asset_url": "https://cdn.example/banner.png", "click_url": "https://adv.example/promo",
        "html_snippet": None, "native_title": None, "native_description": None, "native_cta": None,
        "native_icon_url": None, "video_url": None, "video_duration": None, "vast_xml": None,
        "name": "Test Banner", "imp_tracker_url": None, "click_tracker_url": None, "erid": "2Vfnxx",
        "adv_name": None, "adv_inn": None, "adv_ogrn": None, "adv_address": None,
        "fx_markup_percent": markup,
    }


def _req(cur: str | None, bidfloor: float = 0.0, bidfloorcur: str | None = None) -> dict:
    imp = {"id": "1", "banner": {"w": 300, "h": 250}, "bidfloor": bidfloor}
    if bidfloorcur:
        imp["bidfloorcur"] = bidfloorcur
    req = {
        "id": "fx1", "at": 1, "imp": [imp],
        "site": {"domain": "pub.example", "publisher": {"id": "pub-9"}},
        "device": {"ua": "Mozilla/5.0 (Linux; Android 13) Chrome/120", "os": "android",
                   "devicetype": 1, "ifa": "dev-1", "geo": {"country": "RUS"}},
    }
    if cur:
        req["cur"] = [cur]
    return req


class _NoPool:
    """Все чтения идут из прогретого кэша rtb; любой запрос в БД — ошибка теста."""

    async def fetch(self, sql, *_):
        raise AssertionError(f"unexpected DB query: {sql[:60]}")

    async def fetchrow(self, sql, *_):
        raise AssertionError(f"unexpected DB query: {sql[:60]}")


def _bid(monkeypatch, *, partner: dict, candidate: dict, rates: dict, req: dict):
    """process_bid_request на прогретом кэше и записывающем Redis.
    test=1 и verify_token=False: без резерва, без токена — только цена."""
    # Кэш rtb включается только при RTB_CACHE_TTL > 0 — прогреваем его, чтобы
    # ни одно чтение не ушло в БД (_NoPool падает на любом запросе).
    monkeypatch.setattr(rtb, "RTB_CACHE_TTL", 60.0)
    rtb._hot_cache.clear()
    rtb._cache_put("partner:key1", partner)
    rtb._cache_put("candidates", [candidate])
    rtb._cache_put(f"blocks:{candidate['campaign_id']}", [])
    rtb._cache_put(f"domain_lists:{candidate['owner_id']}", [])
    redis = _Redis()

    async def _pool():
        return _NoPool()

    async def _qps_ok(*_):
        return True

    async def _no_blocks(*_):
        return frozenset()

    from ml import quality as ml_quality
    import scoring as _scoring
    monkeypatch.setattr(rtb, "get_pool", _pool)
    monkeypatch.setattr(rtb, "get_redis", lambda: redis)
    monkeypatch.setattr(rtb, "check_qps", _qps_ok)
    monkeypatch.setattr(ml_quality, "get_blocked_domains", _no_blocks)
    monkeypatch.setattr(_scoring, "get_scoring_blocks", _no_blocks)
    monkeypatch.setattr(rtb, "_fire_and_forget", lambda coro: coro.close())
    fx.set_rates_for_tests(rates)
    try:
        req = {**req, "test": 1}
        resp = asyncio.run(rtb.process_bid_request("key1", req, verify_token=False))
    finally:
        fx.set_rates_for_tests(None)
        rtb._hot_cache.clear()
    return resp, redis


def _price(resp) -> float:
    assert resp is not None, "expected a bid"
    bids = resp["seatbid"][0]["bid"]
    assert len(bids) == 1
    return bids[0]["price"]


# --------------------------------------------------------- bid parity ----

def test_usd_partner_prices_in_usd(monkeypatch):
    resp, redis = _bid(monkeypatch, partner=_partner("USD"), candidate=_candidate(),
                       rates={"USD": USD_RATE}, req=_req("USD", 1))
    assert resp["cur"] == "USD"
    assert _price(resp) == pytest.approx(round(120.0 / EFFECTIVE, 4))  # 1.2945
    meta = redis.bidmeta()
    assert meta["cur"] == "USD"
    assert meta["fx_rate"] == pytest.approx(EFFECTIVE)
    # Внутренняя цена — в RUB.
    assert meta["price"] == pytest.approx(120.0)


def test_imp_floor_converted_to_rub(monkeypatch):
    # 1.2 USD × 92.7 = 111.24 ₽ < 120 ₽ — торгуем; 1.3 USD = 120.51 ₽ — no-bid.
    resp, _ = _bid(monkeypatch, partner=_partner("USD"), candidate=_candidate(),
                   rates={"USD": USD_RATE}, req=_req("USD", 1.2))
    assert resp is not None
    resp, _ = _bid(monkeypatch, partner=_partner("USD"), candidate=_candidate(),
                   rates={"USD": USD_RATE}, req=_req("USD", 1.3))
    assert resp is None


def test_partner_floor_in_partner_currency(monkeypatch):
    # 1.5 USD -> 139.05 ₽ > 120 ₽ — no-bid; 1.2 USD -> 111.24 ₽ — торгуем.
    resp, _ = _bid(monkeypatch, partner=_partner("USD", bid_floor=1.5), candidate=_candidate(),
                   rates={"USD": USD_RATE}, req=_req("USD"))
    assert resp is None
    resp, _ = _bid(monkeypatch, partner=_partner("USD", bid_floor=1.2), candidate=_candidate(),
                   rates={"USD": USD_RATE}, req=_req("USD"))
    assert resp is not None


def test_owner_markup_changes_rate(monkeypatch):
    # Наценка 10% -> effective 99 -> 120/99 = 1.2121 USD. JSONB-строка, как в app_settings.
    resp, redis = _bid(monkeypatch, partner=_partner("USD"), candidate=_candidate(markup="10"),
                       rates={"USD": USD_RATE}, req=_req("USD", 1))
    assert _price(resp) == pytest.approx(round(120.0 / 99.0, 4))
    assert redis.bidmeta()["fx_rate"] == pytest.approx(99.0)


def test_rub_fallback_when_ssp_takes_rubles(monkeypatch):
    resp, _ = _bid(monkeypatch, partner=_partner("USD"), candidate=_candidate(),
                   rates={"USD": USD_RATE}, req=_req("RUB", 1))
    assert resp["cur"] == "RUB"
    assert _price(resp) == pytest.approx(120.0)


def test_no_bid_when_currency_unsupported(monkeypatch):
    before = obs_metrics.snapshot()["no_bid"]["currency"]
    resp, _ = _bid(monkeypatch, partner=_partner(None), candidate=_candidate(),
                   rates={"USD": USD_RATE}, req=_req("USD"))
    assert resp is None
    assert obs_metrics.snapshot()["no_bid"]["currency"] == before + 1


def test_no_bid_when_rate_missing(monkeypatch):
    before = obs_metrics.snapshot()["counters"]["rtb_nobid_fx_rate_missing_total"]
    resp, _ = _bid(monkeypatch, partner=_partner("USD"), candidate=_candidate(),
                   rates={}, req=_req("USD"))
    assert resp is None
    # bidfloorcur в валюте без курса — no-bid даже при рублёвом ответе.
    resp, _ = _bid(monkeypatch, partner=_partner(None), candidate=_candidate(),
                   rates={}, req=_req("RUB", 1, "EUR"))
    assert resp is None
    assert obs_metrics.snapshot()["counters"]["rtb_nobid_fx_rate_missing_total"] == before + 2


# ------------------------------------------------------------- win path ---

def test_auction_price_converted_to_rub_by_bid_rate():
    row = {"fx_rate": EFFECTIVE, "cur": "USD"}
    # SSP сообщил клиринг 1.1 USD -> 101.97 ₽.
    assert rtb._to_rub_price("1.1", row) == pytest.approx(101.97)
    # Рублёвый / старый бид без fx_rate — как есть.
    assert rtb._to_rub_price(95.0, {"fx_rate": None}) == pytest.approx(95.0)
    assert rtb._to_rub_price(95.0, {}) == pytest.approx(95.0)
    # Мусор от SSP — не падаем, clamp решит.
    assert rtb._to_rub_price("n/a", row) == "n/a"
