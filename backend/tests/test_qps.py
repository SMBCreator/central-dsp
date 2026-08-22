"""Tests for the shared per-partner QPS limiter (cache.check_qps).

Uses fakeredis so no external Redis is needed. Follows the repo convention
of driving coroutines with asyncio.run (no pytest-asyncio dependency).
"""
import asyncio
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(
        cache, "_client", fakeredis.aioredis.FakeRedis(decode_responses=True)
    )


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Pin the limiter's 1-second bucket.

    check_qps keys its counter by int(time.time()); if a test's calls
    straddle a real second boundary the window splits in two and more
    requests pass than the limit (flaked in CI: 17 == 10). Freezing the
    clock makes every test deterministic regardless of scheduling.
    """
    import time as _time_mod

    monkeypatch.setattr(_time_mod, "time", lambda: 1_700_000_000.0)


def test_under_limit_allows():
    async def run():
        return [await cache.check_qps("p1", 10) for _ in range(5)]

    assert asyncio.run(run()) == [True] * 5


def test_over_limit_blocks():
    async def run():
        return [await cache.check_qps("p2", 3) for _ in range(6)]

    assert asyncio.run(run()) == [True, True, True, False, False, False]


def test_zero_or_negative_limit_means_unlimited():
    async def run():
        for _ in range(50):
            assert await cache.check_qps("p3", 0) is True
            assert await cache.check_qps("p3", -1) is True

    asyncio.run(run())


def test_partners_are_isolated():
    async def run():
        for _ in range(3):
            await cache.check_qps("busy", 3)
        assert await cache.check_qps("busy", 3) is False
        assert await cache.check_qps("quiet", 3) is True

    asyncio.run(run())


def test_concurrent_requests_counted_atomically():
    async def run():
        results = await asyncio.gather(
            *[cache.check_qps("burst", 10) for _ in range(25)]
        )
        return sum(results)

    assert asyncio.run(run()) == 10
