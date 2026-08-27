"""Tests for the login brute-force limiter (cache.rate_limit, GAP-002).

Uses fakeredis, follows the repo convention of asyncio.run (no
pytest-asyncio dependency).
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


def test_under_limit_allows():
    async def run():
        results = [await cache.rate_limit("login:ip", "1.2.3.4", 5, 300) for _ in range(5)]
        assert all(results)

    asyncio.run(run())


def test_over_limit_blocks():
    async def run():
        for _ in range(5):
            await cache.rate_limit("login:ip", "1.2.3.4", 5, 300)
        assert await cache.rate_limit("login:ip", "1.2.3.4", 5, 300) is False

    asyncio.run(run())


def test_keys_are_isolated():
    async def run():
        for _ in range(6):
            await cache.rate_limit("login:ip", "1.2.3.4", 5, 300)
        # A different IP is unaffected.
        assert await cache.rate_limit("login:ip", "5.6.7.8", 5, 300) is True
        # A different scope with the same key is unaffected.
        assert await cache.rate_limit("login:email", "1.2.3.4", 5, 300) is True

    asyncio.run(run())


def test_zero_limit_means_unlimited():
    async def run():
        assert await cache.rate_limit("login:ip", "1.2.3.4", 0, 300) is True

    asyncio.run(run())


def test_fails_open_on_redis_error(monkeypatch):
    class Boom:
        def pipeline(self, *a, **kw):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "_client", Boom())

    async def run():
        assert await cache.rate_limit("login:ip", "1.2.3.4", 5, 300) is True

    asyncio.run(run())


def test_window_ttl_is_set():
    async def run():
        await cache.rate_limit("login:email", "user@example.com", 8, 300)
        ttl = await cache.get_redis().ttl("rl:login:email:user@example.com")
        assert 0 < ttl <= 300

    asyncio.run(run())
