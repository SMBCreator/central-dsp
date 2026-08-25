"""Retargeting audience tests: segment matching + include/exclude gate.

Follows the repo convention: fakeredis + asyncio.run (no pytest-asyncio).
"""
import asyncio
import sys
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache  # noqa: E402
from audiences import _segment_matches, check_audiences  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(
        cache, "_client", fakeredis.aioredis.FakeRedis(decode_responses=True)
    )


def _seg(rule_type, rule_value=None):
    return {"rule_type": rule_type, "rule_value": rule_value}


# ---------- segment matching (pure logic) ----------

def test_all_visitors_matches_pageview():
    assert _segment_matches(_seg("all_visitors"), "pageview", None, "https://a.ru/")


def test_all_visitors_ignores_heartbeat():
    assert not _segment_matches(_seg("all_visitors"), "heartbeat", None, None)


def test_url_contains_hit():
    assert _segment_matches(
        _seg("url_contains", "/checkout"), "pageview", None,
        "https://shop.ru/checkout?step=1",
    )


def test_url_contains_miss():
    assert not _segment_matches(
        _seg("url_contains", "/checkout"), "pageview", None, "https://shop.ru/catalog",
    )


def test_event_hit():
    assert _segment_matches(_seg("event", "purchase"), "event", "purchase", None)


def test_event_name_mismatch():
    assert not _segment_matches(_seg("event", "purchase"), "event", "add_to_cart", None)


def test_goal_never_matches_raw_events():
    # goal segments are filled by the conversion path, not raw pixel events
    assert not _segment_matches(_seg("goal", "g1"), "event", "g1", None)


# ---------- hot-path gate (Redis sets) ----------

def test_no_lists_passes():
    assert asyncio.run(check_audiences("dev1", [], [])) is True


def test_unknown_device_fails_include():
    assert asyncio.run(check_audiences("", ["seg1"], [])) is False


def test_unknown_device_passes_exclude_only():
    assert asyncio.run(check_audiences("", [], ["seg1"])) is True


def test_include_member_passes():
    async def run():
        await cache.get_redis().sadd("aud:seg-inc", "dev-a")
        return await check_audiences("dev-a", ["seg-inc"], [])
    assert asyncio.run(run()) is True


def test_include_nonmember_blocked():
    async def run():
        await cache.get_redis().sadd("aud:seg-inc2", "dev-a")
        return await check_audiences("dev-b", ["seg-inc2"], [])
    assert asyncio.run(run()) is False


def test_exclude_member_blocked():
    async def run():
        await cache.get_redis().sadd("aud:seg-exc", "dev-x")
        return await check_audiences("dev-x", [], ["seg-exc"])
    assert asyncio.run(run()) is False


def test_exclude_wins_over_include():
    async def run():
        await cache.get_redis().sadd("aud:both", "dev-z")
        return await check_audiences("dev-z", ["both"], ["both"])
    assert asyncio.run(run()) is False


def test_include_or_semantics():
    """Membership in ANY include segment is enough."""
    async def run():
        await cache.get_redis().sadd("aud:seg-a", "dev-m")
        return await check_audiences("dev-m", ["seg-a", "seg-b"], [])
    assert asyncio.run(run()) is True
