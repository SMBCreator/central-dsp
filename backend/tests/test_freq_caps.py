"""Multi-window frequency caps: Lua gate + bump + legacy fallback."""
import asyncio

import fakeredis.aioredis

import cache
from cache import bump_frequency_multi, gate_and_reserve


def _fresh_redis():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache._client = r
    return r


def test_single_window_blocks_at_cap():
    async def run():
        _fresh_redis()
        caps = [(2, 24)]
        # cap 2: first two impressions pass, gate stays open until bumped
        assert await gate_and_reserve("c1", "li1", 0.1, 100, 0, device_id="dev1", caps=caps)
        await bump_frequency_multi("c1", "dev1", caps)
        assert await gate_and_reserve("c1", "li1", 0.1, 100, 0, device_id="dev1", caps=caps)
        await bump_frequency_multi("c1", "dev1", caps)
        # cap reached -> gate closes
        assert not await gate_and_reserve("c1", "li1", 0.1, 100, 0, device_id="dev1", caps=caps)
    asyncio.run(run())


def test_second_window_blocks_independently():
    async def run():
        _fresh_redis()
        # 5/day + 2/week: the weekly window is the binding one
        caps = [(5, 24), (2, 168)]
        for _ in range(2):
            assert await gate_and_reserve("c2", "li1", 0.1, 100, 0, device_id="dev1", caps=caps)
            await bump_frequency_multi("c2", "dev1", caps)
        # daily allows 3 more, but weekly cap of 2 closes the gate
        assert not await gate_and_reserve("c2", "li1", 0.1, 100, 0, device_id="dev1", caps=caps)
    asyncio.run(run())


def test_three_windows_and_ttl_keys():
    async def run():
        r = _fresh_redis()
        caps = [(10, 1), (20, 24), (30, 168)]
        assert await gate_and_reserve("c3", "li1", 0.1, 100, 0, device_id="dev9", caps=caps)
        await bump_frequency_multi("c3", "dev9", caps)
        for hours in (1, 24, 168):
            key = f"freq:c3:{hours}h:dev9"
            assert await r.get(key) == "1"
            ttl = await r.ttl(key)
            assert 0 < ttl <= hours * 3600
    asyncio.run(run())


def test_legacy_freq_cap_fallback():
    async def run():
        _fresh_redis()
        # No caps list -> the legacy 24h pair still gates
        assert await gate_and_reserve("c4", "li1", 0.1, 100, 0, device_id="dev1", freq_cap=1)
        await bump_frequency_multi("c4", "dev1", [(1, 24)])
        assert not await gate_and_reserve("c4", "li1", 0.1, 100, 0, device_id="dev1", freq_cap=1)
    asyncio.run(run())


def test_no_device_id_is_fail_open():
    async def run():
        _fresh_redis()
        caps = [(1, 24)]
        # Without a device id caps cannot apply; budget still reserves
        assert await gate_and_reserve("c5", "li1", 0.1, 100, 0, device_id="", caps=caps)
        assert await gate_and_reserve("c5", "li1", 0.1, 100, 0, device_id="", caps=caps)
    asyncio.run(run())


def test_budget_still_enforced_with_caps():
    async def run():
        _fresh_redis()
        caps = [(100, 24)]
        # budget 0.15, each bid 0.1 -> the second must be rejected by budget
        assert await gate_and_reserve("c6", "li1", 0.1, 0.15, 0, device_id="dev1", caps=caps)
        assert not await gate_and_reserve("c6", "li1", 0.1, 0.15, 0, device_id="dev1", caps=caps)
    asyncio.run(run())


def test_parse_freq_caps_shapes():
    import sys
    sys.path.insert(0, "routers")
    from routers.rtb import _parse_freq_caps

    # JSONB list
    assert _parse_freq_caps({"frequency_caps": '[{"impressions": 3, "period_hours": 24}]',
                             "frequency_cap_impressions": None,
                             "frequency_cap_period_hours": None}) == [(3, 24)]
    # Legacy fallback
    assert _parse_freq_caps({"frequency_caps": None,
                             "frequency_cap_impressions": 5,
                             "frequency_cap_period_hours": 48}) == [(5, 48)]
    # Nothing configured
    assert _parse_freq_caps({"frequency_caps": None,
                             "frequency_cap_impressions": None,
                             "frequency_cap_period_hours": None}) == []
    # Bad JSON is ignored, legacy used
    assert _parse_freq_caps({"frequency_caps": "not-json",
                             "frequency_cap_impressions": 2,
                             "frequency_cap_period_hours": None}) == [(2, 24)]
    # More than 3 windows are clamped
    caps = _parse_freq_caps({"frequency_caps": '[{"impressions":1,"period_hours":1},{"impressions":2,"period_hours":2},{"impressions":3,"period_hours":3},{"impressions":4,"period_hours":4}]',
                             "frequency_cap_impressions": None,
                             "frequency_cap_period_hours": None})
    assert caps == [(1, 1), (2, 2), (3, 3)]
