"""Frequency cap lifecycle (audit R-06): bid → in-flight → win reservation →
imp confirm; gate formula confirmed + active_reservations < cap.

Mirrors bidder-go internal/redisx/freqcap_test.go."""
import asyncio
import time

import fakeredis.aioredis

import cache
from cache import (
    confirm_imp,
    freq_inflight_key,
    freq_key,
    gate_and_reserve,
    get_frequency,
    get_frequency_inflight,
    release_bid,
    settle_win,
)


def _fresh_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache._client = r
    return r


def _res(bid_id, caps, *, camp="c1", dev="dev1", count_on_win=False):
    return {
        "bid_id": bid_id, "campaign_id": camp, "line_item_id": "li1", "advertiser_id": "",
        "reserve": 0.1, "business_date": cache.business_date_str(),
        "has_total": False, "has_li": False,
        "device_id": dev, "caps": caps, "count_on_win": count_on_win,
    }


async def _bid(bid_id, caps, *, camp="c1", dev="dev1", **kw):
    return await gate_and_reserve(camp, "li1", 0.1, 100, 0, device_id=dev, caps=caps, bid_id=bid_id, **kw)


def test_inflight_bids_count_against_cap():
    """N concurrent auctions of one device cannot all pass at cap-1."""
    async def run():
        _fresh_redis()
        caps = [(2, 24)]
        assert await _bid("b1", caps)
        assert await _bid("b2", caps)
        # Nothing shown yet, but two bids are in flight: cap 2 is full.
        assert not await _bid("b3", caps)
        assert await get_frequency_inflight("c1", "dev1") == 2
        assert await get_frequency("c1", "dev1", 24) == 0
    asyncio.run(run())


def test_loss_frees_the_slot():
    async def run():
        _fresh_redis()
        caps = [(1, 24)]
        assert await _bid("b1", caps)
        assert not await _bid("b2", caps)
        assert await release_bid(_res("b1", caps), "loss")
        assert await _bid("b2", caps)
    asyncio.run(run())


def test_win_is_a_reservation_not_an_impression():
    async def run():
        r = _fresh_redis()
        caps = [(1, 24)]
        assert await _bid("b1", caps)
        assert await settle_win(_res("b1", caps), 0.05) == 1
        # Counter untouched, the member became a win reservation.
        assert await get_frequency("c1", "dev1", 24) == 0
        members = await r.zrange(freq_inflight_key("c1", "dev1"), 0, -1, withscores=True)
        assert [m for m, _ in members] == ["w:b1"]
        deadline = members[0][1]
        assert time.time() + cache.WIN_IMP_TTL - 5 < deadline <= time.time() + cache.WIN_IMP_TTL + 1
        # Still holds the cap.
        assert not await _bid("b2", caps)
    asyncio.run(run())


def test_imp_confirms_and_counter_gets_window_ttl():
    async def run():
        r = _fresh_redis()
        caps = [(2, 1), (5, 24), (9, 168)]
        assert await _bid("b1", caps)
        assert await settle_win(_res("b1", caps), 0.05) == 1
        assert await confirm_imp(_res("b1", caps))
        assert await get_frequency_inflight("c1", "dev1") == 0
        for hours in (1, 24, 168):
            key = freq_key("c1", hours, "dev1")
            assert await r.get(key) == "1"
            assert 0 < await r.ttl(key) <= hours * 3600
        # cap 2 on the 1h window: one confirmed + one in flight = full.
        assert await _bid("b2", caps)
        assert not await _bid("b3", caps)
    asyncio.run(run())


def test_win_reservation_expires_without_imp_and_is_counted():
    async def run():
        r = _fresh_redis()
        caps = [(1, 24)]
        assert await _bid("b1", caps)
        assert await settle_win(_res("b1", caps), 0.05) == 1
        assert not await _bid("b2", caps)
        # Simulate the win→imp TTL passing: move the deadline into the past.
        await r.zadd(freq_inflight_key("c1", "dev1"), {"w:b1": time.time() - 1})
        import obs_metrics
        before = obs_metrics.snapshot()["counters"].get("rtb_freq_win_without_imp_total", 0)
        assert await _bid("b2", caps)  # prune freed the slot
        assert await get_frequency("c1", "dev1", 24) == 0  # never confirmed → not an impression
        assert obs_metrics.snapshot()["counters"]["rtb_freq_win_without_imp_total"] == before + 1
    asyncio.run(run())


def test_late_imp_after_expired_reservation_still_counts():
    """The impression happened even if the pixel came after the TTL."""
    async def run():
        r = _fresh_redis()
        caps = [(3, 24)]
        assert await _bid("b1", caps)
        assert await settle_win(_res("b1", caps), 0.05) == 1
        await r.zremrangebyscore(freq_inflight_key("c1", "dev1"), "-inf", "+inf")
        assert await confirm_imp(_res("b1", caps))
        assert await get_frequency("c1", "dev1", 24) == 1
    asyncio.run(run())


def test_duplicate_win_does_not_double_reserve():
    async def run():
        r = _fresh_redis()
        caps = [(5, 24)]
        assert await _bid("b1", caps)
        assert await settle_win(_res("b1", caps), 0.05) == 1
        assert await settle_win(_res("b1", caps), 0.05) == 0
        assert await r.zcard(freq_inflight_key("c1", "dev1")) == 1
    asyncio.run(run())


def test_partner_without_pixel_counts_on_win():
    async def run():
        r = _fresh_redis()
        caps = [(2, 24)]
        res = _res("b1", caps, count_on_win=True)
        assert await _bid("b1", caps)
        assert await settle_win(res, 0.05) == 1
        assert await get_frequency("c1", "dev1", 24) == 1
        assert await r.zcard(freq_inflight_key("c1", "dev1")) == 0
        # A later pixel from such a partner must not double count.
        assert not await confirm_imp(res)
        assert await get_frequency("c1", "dev1", 24) == 1
    asyncio.run(run())


def test_legacy_freq_cap_fallback():
    async def run():
        _fresh_redis()
        assert await gate_and_reserve("c4", "li1", 0.1, 100, 0, device_id="dev1", freq_cap=1, bid_id="b1")
        assert not await gate_and_reserve("c4", "li1", 0.1, 100, 0, device_id="dev1", freq_cap=1, bid_id="b2")
    asyncio.run(run())


def test_no_device_id_is_fail_open():
    async def run():
        _fresh_redis()
        caps = [(1, 24)]
        assert await _bid("b1", caps, dev="")
        assert await _bid("b2", caps, dev="")
        # And confirm_imp without a device never creates counters.
        assert not await confirm_imp(_res("b1", caps, dev=""))
    asyncio.run(run())


def test_no_caps_means_no_freq_keys():
    async def run():
        r = _fresh_redis()
        assert await gate_and_reserve("c6", "li1", 0.1, 100, 0, device_id="dev1", bid_id="b1")
        assert not await r.exists(freq_inflight_key("c6", "dev1"))
        assert await settle_win(_res("b1", []), 0.05) == 1
        assert not await confirm_imp(_res("b1", []))
        assert not await r.keys("freq:*")
    asyncio.run(run())


def test_budget_still_enforced_with_caps():
    async def run():
        _fresh_redis()
        caps = [(100, 24)]
        assert await gate_and_reserve("c7", "li1", 60, 100, 0, device_id="dev1", caps=caps, bid_id="b1")
        assert not await gate_and_reserve("c7", "li1", 60, 100, 0, device_id="dev1", caps=caps, bid_id="b2")
    asyncio.run(run())
