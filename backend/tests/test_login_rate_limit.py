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


# --- Login brute-force limiter (D5): считает НЕУДАЧИ, по логину и по IP -------


def test_successful_logins_never_block(monkeypatch):
    """Легитимный пользователь входит сколько угодно — лимит только на неудачи."""
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_EMAIL", 3)

    async def run():
        for _ in range(20):
            assert await cache.login_blocked("u@x.ru", "1.1.1.1") == 0
            await cache.clear_login_failures("u@x.ru")

    asyncio.run(run())


def test_email_blocked_after_n_failures_from_any_ip(monkeypatch):
    """Перебор одного логина с разных IP: блок по логину."""
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_EMAIL", 3)
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_IP", 1000)

    async def run():
        for i in range(3):
            assert await cache.login_blocked("victim@x.ru", f"10.0.0.{i}") == 0
            await cache.record_login_failure("victim@x.ru", f"10.0.0.{i}")
        retry = await cache.login_blocked("victim@x.ru", "10.0.0.99")
        assert 0 < retry <= cache.LOGIN_FAIL_WINDOW_SECONDS
        # Другой логин с того же IP не затронут (IP-лимит высокий).
        assert await cache.login_blocked("other@x.ru", "10.0.0.99") == 0

    asyncio.run(run())


def test_ip_blocked_after_n_failures_across_logins(monkeypatch):
    """Перебор многих логинов с одного IP: блок по IP."""
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_EMAIL", 1000)
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_IP", 4)

    async def run():
        for i in range(4):
            await cache.record_login_failure(f"user{i}@x.ru", "203.0.113.5")
        assert await cache.login_blocked("fresh@x.ru", "203.0.113.5") > 0
        assert await cache.login_blocked("fresh@x.ru", "203.0.113.6") == 0

    asyncio.run(run())


def test_success_clears_email_counter_but_not_ip(monkeypatch):
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_EMAIL", 2)
    monkeypatch.setattr(cache, "LOGIN_FAILS_PER_IP", 2)

    async def run():
        await cache.record_login_failure("u@x.ru", "9.9.9.9")
        await cache.record_login_failure("u@x.ru", "9.9.9.9")
        assert await cache.login_blocked("u@x.ru", "9.9.9.9") > 0
        await cache.clear_login_failures("u@x.ru")
        # По логину чисто, но IP в блоке (с него могли перебирать другие аккаунты).
        assert await cache.login_blocked("u@x.ru", "9.9.9.9") > 0
        assert await cache.login_blocked("u@x.ru", "8.8.8.8") == 0

    asyncio.run(run())


def test_window_is_fixed_not_sliding():
    """TTL ставится один раз; повторные неудачи его не продлевают — иначе
    атакующий держал бы жертву заблокированной бесконечно."""

    async def run():
        r = cache.get_redis()
        await cache.record_login_failure("u@x.ru", "1.1.1.1")
        ttl1 = await r.ttl("rl:login:email:u@x.ru")
        await r.expire("rl:login:email:u@x.ru", 5)  # «состарим» ключ
        await cache.record_login_failure("u@x.ru", "1.1.1.1")
        ttl2 = await r.ttl("rl:login:email:u@x.ru")
        assert ttl1 > 5 and 0 < ttl2 <= 5

    asyncio.run(run())


def test_login_limiter_fails_open_when_redis_down(monkeypatch):
    class Broken:
        def pipeline(self, *a, **k):
            raise ConnectionError("redis down")

        async def delete(self, *a):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cache, "_client", Broken())

    async def run():
        assert await cache.login_blocked("u@x.ru", "1.1.1.1") == 0
        await cache.record_login_failure("u@x.ru", "1.1.1.1")  # не бросает
        await cache.clear_login_failures("u@x.ru")

    asyncio.run(run())
