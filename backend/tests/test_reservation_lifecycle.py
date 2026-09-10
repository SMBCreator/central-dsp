"""Reservation lifecycle (audit M-05/M-06): баланс рекламодателя и остаток
total как реальные лимиты на bid-time, per-bid терминальные состояния.

Инвариант, ради которого всё сделано: сумма невыполненных резервов + уже
списанное никогда не превышает баланс рекламодателя, даже если дневные
бюджеты кампаний в сумме больше баланса и win-уведомления приходят поздно.
"""
import asyncio

import fakeredis.aioredis
import pytest

import cache
from cache import (
    PENDING_BIDS_KEY,
    avail_adv_key,
    avail_camp_key,
    bid_state_key,
    gate_and_reserve,
    release_bid,
    res_adv_key,
    reservation_from_bidmeta,
    settle_win,
    spend_key,
)


def _fresh_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache._client = r
    return r


async def _f(r, key) -> float:
    v = await r.get(key)
    return float(v) if v is not None else 0.0


def _res(bid_id, camp="c1", li="li1", adv="adv1", reserve=1.0, has_total=False, has_li=True):
    return {
        "bid_id": bid_id, "campaign_id": camp, "line_item_id": li, "advertiser_id": adv,
        "reserve": reserve, "business_date": cache.business_date_str(),
        "has_total": has_total, "has_li": has_li,
    }


async def _bid(bid_id, *, cost=1.0, camp="c1", li="li1", adv="adv1", camp_budget=1000.0, li_budget=1000.0,
               adv_balance=5.0, total_remaining=0.0, has_total=False):
    return await gate_and_reserve(
        camp, li, cost, camp_budget, li_budget, bid_id=bid_id, advertiser_id=adv,
        adv_balance=adv_balance, total_remaining=total_remaining, has_total=has_total,
    )


def test_small_balance_caps_commitments_despite_big_daily_budgets():
    """Баланс 5 ₽, дневной бюджет 1000 ₽ — проходят ровно 5 бидов по 1 ₽,
    шестой режется гейтом: обязательства ≤ balance."""
    async def run():
        r = _fresh_redis()
        passed = [await _bid(f"b{i}") for i in range(7)]
        assert passed == [True] * 5 + [False, False]
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(0.0)
        assert await _f(r, res_adv_key("adv1")) == pytest.approx(5.0)
        # daily-счётчик после отката шестого бида не сдвинулся
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(5.0)
        assert await r.zcard(PENDING_BIDS_KEY) == 5
    asyncio.run(run())


def test_loss_refunds_and_reopens_gate_once():
    async def run():
        r = _fresh_redis()
        for i in range(5):
            assert await _bid(f"b{i}")
        assert not await _bid("blocked")
        # loss одного бида освобождает ровно 1 ₽
        assert await release_bid(_res("b0"), "loss") is True
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(1.0)
        assert await _f(r, res_adv_key("adv1")) == pytest.approx(4.0)
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(4.0)
        assert await r.get(bid_state_key("b0")) == "loss"
        assert await r.zscore(PENDING_BIDS_KEY, "b0") is None
        # повторный lurl — ничего не двигает
        assert await release_bid(_res("b0"), "loss") is False
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(1.0)
        # освободившийся рубль можно снова зарезервировать — но только один раз
        assert await _bid("b5")
        assert not await _bid("b6")
    asyncio.run(run())


def test_win_converts_reserve_to_clearing_and_refunds_difference():
    async def run():
        r = _fresh_redis()
        assert await _bid("b0", cost=1.0)
        code = await settle_win(_res("b0", reserve=1.0), 0.6)
        assert code == 1
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(0.6)
        assert await _f(r, spend_key("li", "li1")) == pytest.approx(0.6)
        # balance 5 - clearing 0.6: резерв 1.0 снят на биде, разница 0.4 вернулась
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.4)
        assert await _f(r, res_adv_key("adv1")) == pytest.approx(0.0)
        assert await r.get(bid_state_key("b0")) == "win"
        # дубликат win — ноль движения
        assert await settle_win(_res("b0", reserve=1.0), 0.6) == 0
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.4)
        # loss после win — резерв не возвращается второй раз
        assert await release_bid(_res("b0"), "loss") is False
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.4)
    asyncio.run(run())


def test_late_win_after_expire_recharges_clearing():
    """Sweeper вернул резерв (expired), потом пришёл win: clearing досписывается,
    а не «возвращается разница» — иначе рекламодатель получил бы деньги за показ."""
    async def run():
        r = _fresh_redis()
        assert await _bid("b0", cost=1.0)
        assert await release_bid(_res("b0"), "expired") is True
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(5.0)
        code = await settle_win(_res("b0", reserve=1.0), 0.7)
        assert code == 2
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.3)
        assert await _f(r, spend_key("camp", "c1")) == pytest.approx(0.7)
        assert await r.get(bid_state_key("b0")) == "win"
    asyncio.run(run())


def test_win_from_pg_fallback_assumes_reservation_gone():
    """bidmeta истёк (>1h), бид найден в Postgres: bidstate нет, но резерв точно
    снят sweeper'ом (90 с) — assume_released досписывает clearing."""
    async def run():
        r = _fresh_redis()
        await r.set(avail_adv_key("adv1"), "5")
        code = await settle_win(_res("old"), 0.5, assume_released=True)
        assert code == 2
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(4.5)
    asyncio.run(run())


def test_total_budget_remaining_is_enforced_and_refunded():
    async def run():
        r = _fresh_redis()
        kw = dict(adv_balance=100.0, total_remaining=2.5, has_total=True)
        assert await _bid("b0", **kw)
        assert await _bid("b1", **kw)
        assert not await _bid("b2", **kw)          # остаток total 0.5 < 1
        assert await _f(r, avail_camp_key("c1")) == pytest.approx(0.5)
        assert await _f(r, avail_adv_key("adv1")) == pytest.approx(98.0)   # откат не оставил следов
        assert await release_bid(_res("b0", has_total=True), "loss")
        assert await _f(r, avail_camp_key("c1")) == pytest.approx(1.5)
        assert await _bid("b2", **kw)
    asyncio.run(run())


def test_seed_only_when_key_missing():
    """Снимок фида сеет avail:* один раз; дальше лимит держит Redis, даже если
    фид (обновляется раз в N секунд) ещё показывает старый баланс."""
    async def run():
        r = _fresh_redis()
        assert await _bid("b0", adv_balance=1.0)
        assert not await _bid("b1", adv_balance=1.0)
        # фид всё ещё говорит «1.0», но ключ уже 0 — сида нет, бид режется
        assert not await _bid("b2", adv_balance=1.0)
        # депозит двигает доступный остаток напрямую
        await cache.adjust_available_balance("adv1", 1.0)
        assert await _bid("b3", adv_balance=1.0)
    asyncio.run(run())


def test_reservation_from_bidmeta_legacy_and_test_rows():
    row = {"bid_id": "b", "campaign_id": "c", "line_item_id": "li", "price": 2000.0, "is_test": 0}
    res = reservation_from_bidmeta(row)
    assert res["reserve"] == pytest.approx(2.0) and res["advertiser_id"] == "" and res["has_li"] is False
    assert reservation_from_bidmeta({**row, "is_test": 1}) is None
    full = {**row, "advertiser_id": "a", "reserve": 1.5, "has_total": 1, "has_li": 1, "business_date": "2026-01-01"}
    res = reservation_from_bidmeta(full)
    assert res == {
        "bid_id": "b", "campaign_id": "c", "line_item_id": "li", "advertiser_id": "a",
        "reserve": 1.5, "business_date": "2026-01-01", "has_total": True, "has_li": True,
        "device_id": "", "caps": [], "count_on_win": False,
    }
    # R-06: fdev/fcaps/fwin carry the frequency-cap context of the bid.
    res = reservation_from_bidmeta({**full, "fdev": "dev9", "fcaps": [[3, 24], [0, 1], [5, 168]], "fwin": 1})
    assert res["device_id"] == "dev9" and res["caps"] == [(3, 24), (5, 168)] and res["count_on_win"] is True
