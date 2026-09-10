"""Retargeting audience tests: segment matching + include/exclude gate.

Follows the repo convention: fakeredis + asyncio.run (no pytest-asyncio).
"""
import asyncio
import sys
import time
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache  # noqa: E402
from audiences import _segment_matches, check_audiences, redis_add_member  # noqa: E402


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr(
        cache, "_client", fakeredis.aioredis.FakeRedis(decode_responses=True)
    )


_FUTURE = int(time.time()) + 24 * 3600   # live member: expires_at in the future
_PAST = int(time.time()) - 60             # expired member, pruner not yet run


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
        await cache.get_redis().zadd("audz:seg-inc", {"dev-a": _FUTURE})
        return await check_audiences("dev-a", ["seg-inc"], [])
    assert asyncio.run(run()) is True


def test_include_nonmember_blocked():
    async def run():
        await cache.get_redis().zadd("audz:seg-inc2", {"dev-a": _FUTURE})
        return await check_audiences("dev-b", ["seg-inc2"], [])
    assert asyncio.run(run()) is False


def test_exclude_member_blocked():
    async def run():
        await cache.get_redis().zadd("audz:seg-exc", {"dev-x": _FUTURE})
        return await check_audiences("dev-x", [], ["seg-exc"])
    assert asyncio.run(run()) is False


def test_exclude_wins_over_include():
    async def run():
        await cache.get_redis().zadd("audz:both", {"dev-z": _FUTURE})
        return await check_audiences("dev-z", ["both"], ["both"])
    assert asyncio.run(run()) is False


def test_include_or_semantics():
    """Membership in ANY include segment is enough."""
    async def run():
        await cache.get_redis().zadd("audz:seg-a", {"dev-m": _FUTURE})
        return await check_audiences("dev-m", ["seg-a", "seg-b"], [])
    assert asyncio.run(run()) is True


# ---------- per-member expiry (audit R-06) ----------

def test_expired_include_member_is_not_a_member():
    async def run():
        await cache.get_redis().zadd("audz:seg-exp", {"dev-old": _PAST, "dev-new": _FUTURE})
        return (
            await check_audiences("dev-old", ["seg-exp"], []),
            await check_audiences("dev-new", ["seg-exp"], []),
        )
    assert asyncio.run(run()) == (False, True)


def test_expired_exclude_member_does_not_block():
    async def run():
        await cache.get_redis().zadd("audz:seg-exc-exp", {"dev-old": _PAST})
        return await check_audiences("dev-old", [], ["seg-exc-exp"])
    assert asyncio.run(run()) is True


def test_redis_add_member_sets_score_and_prunes_expired():
    from datetime import datetime, timedelta, timezone

    async def run():
        r = cache.get_redis()
        await r.zadd("audz:seg-w", {"dev-stale": _PAST})
        exp = datetime.now(timezone.utc) + timedelta(days=30)
        await redis_add_member("seg-w", "dev-fresh", exp, 30)
        members = await r.zrange("audz:seg-w", 0, -1, withscores=True)
        ttl = await r.ttl("audz:seg-w")
        return members, ttl
    members, ttl = asyncio.run(run())
    assert [m for m, _ in members] == ["dev-fresh"]          # stale member pruned
    assert abs(members[0][1] - (time.time() + 30 * 86400)) < 5  # score = expires_at
    assert 30 * 86400 < ttl <= 31 * 86400                    # key TTL = ttl + grace


def test_new_member_does_not_prolong_old_one():
    """The whole point of R-06: a SET with one EXPIRE prolonged everybody."""
    from datetime import datetime, timedelta, timezone

    async def run():
        r = cache.get_redis()
        soon = int(time.time()) + 5
        await r.zadd("audz:seg-p", {"dev-old": soon})
        await redis_add_member("seg-p", "dev-new", datetime.now(timezone.utc) + timedelta(days=30), 30)
        return await r.zscore("audz:seg-p", "dev-old")
    assert asyncio.run(run()) == pytest.approx(time.time() + 5, abs=3)


# ---------- Postgres <-> Redis sync (cron /cron/audience-sync) ----------

class _FakePool:
    """Minimal asyncpg-like pool: one active segment, members with expiry."""

    def __init__(self, members):
        self.members = members  # list of (device_id, expires_at datetime)
        self.deleted = 0

    async def fetchval(self, sql, *args):
        from datetime import datetime, timezone
        assert "DELETE FROM audience_members" in sql
        before = len(self.members)
        self.members = [m for m in self.members if m[1] > datetime.now(timezone.utc)]
        self.deleted = before - len(self.members)
        return self.deleted

    async def fetch(self, sql, *args):
        if "FROM audience_segments s" in sql:
            return [{"id": "seg-sync", "ttl_days": 30, "live": len(self.members)}]
        assert "SELECT device_id, expires_at" in sql
        limit, offset = args[1], args[2]
        return [{"device_id": d, "expires_at": e} for d, e in self.members[offset:offset + limit]]


def test_sync_prunes_expired_and_reseeds_empty_key():
    from datetime import datetime, timedelta, timezone
    from audiences import sync_audiences

    now = datetime.now(timezone.utc)
    pool = _FakePool([("dev-live", now + timedelta(days=10)), ("dev-dead", now - timedelta(days=1))])

    async def run():
        r = cache.get_redis()
        # Redis holds only a stale member: key becomes empty after prune → reseed.
        await r.zadd("audz:seg-sync", {"dev-stale": _PAST})
        res = await sync_audiences(pool)
        members = await r.zrange("audz:seg-sync", 0, -1)
        return res, members
    res, members = asyncio.run(run())
    assert res["pg_deleted"] == 1 and res["redis_pruned"] == 1
    assert res["reseeded_segments"] == 1 and res["reseeded_members"] == 1
    assert members == ["dev-live"]


def test_sync_leaves_populated_key_alone():
    from datetime import datetime, timedelta, timezone
    from audiences import sync_audiences

    now = datetime.now(timezone.utc)
    pool = _FakePool([("dev-a", now + timedelta(days=10)), ("dev-b", now + timedelta(days=10))])

    async def run():
        r = cache.get_redis()
        await r.zadd("audz:seg-sync", {"dev-a": _FUTURE})   # partial but non-empty: not touched
        res = await sync_audiences(pool)
        return res, await r.zrange("audz:seg-sync", 0, -1)
    res, members = asyncio.run(run())
    assert res["reseeded_segments"] == 0 and members == ["dev-a"]
