"""Audit M-04: one daily-budget boundary — midnight Europe/Moscow.

* business_day.py agrees with Go redisx.BusinessDate on the exact instants
  20:59/21:00/23:59 UTC and 00:00 UTC (the old double-reset points).
* Redis spend keys are dated; the cron reset only removes previous days.
* bill_win restarts spent_today on the first win of a new business day and
  API serialization hides yesterday's counter (DB tests need DATABASE_URL).
"""
import asyncio
import os
import re
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import business_day as bd  # noqa: E402
import cache  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


@pytest.mark.parametrize("utc,expected", [
    ("2026-09-06T20:59:59Z", "2026-09-06"),
    ("2026-09-06T21:00:00Z", "2026-09-07"),  # 00:00 MSK — new business day
    ("2026-09-06T23:59:59Z", "2026-09-07"),
    ("2026-09-07T00:00:00Z", "2026-09-07"),  # UTC midnight changes nothing
    ("2026-09-07T20:59:59Z", "2026-09-07"),
])
def test_business_date_moscow_midnight_only(utc, expected):
    assert bd.business_date_str(_utc(utc)) == expected


def test_ttl_covers_whole_business_day_plus_grace():
    at_midnight = _utc("2026-09-06T21:00:00Z")
    assert bd.seconds_until_business_midnight(at_midnight) == 24 * 3600 + bd.KEY_GRACE_SECONDS
    before = _utc("2026-09-06T20:59:59Z")
    assert bd.seconds_until_business_midnight(before) == 60 + bd.KEY_GRACE_SECONDS


def test_go_and_python_share_grace_and_timezone():
    """Go mirrors business_day.py; both constants must stay in lockstep."""
    go_src = (REPO / "bidder-go" / "internal" / "redisx" / "redisx.go").read_text(encoding="utf-8")
    m = re.search(r"const keyGraceSeconds = (\d+)", go_src)
    assert m and int(m.group(1)) == bd.KEY_GRACE_SECONDS
    assert '"Europe/Moscow"' in go_src and bd.BUSINESS_TZ_NAME == "Europe/Moscow"
    assert '"spend:" + scope + ":" + entityID + ":" + day' in go_src
    assert bd.spend_key("camp", "X", "2026-01-02") == "spend:camp:X:2026-01-02"


def test_spend_key_date_parsing():
    assert bd.spend_key_date("spend:camp:abc:2026-09-06") == "2026-09-06"
    assert bd.spend_key_date("spend:camp:abc") is None  # legacy undated key
    assert bd.spend_key_date("spend:li:abc:garbage") is None


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(cache, "_client", r)
    return r


def test_gate_uses_dated_key_and_stale_reset_is_idempotent(fake_redis):
    async def run():
        today = bd.business_date_str()
        ok = await cache.gate_and_reserve("camp-A", "li-A", 10, 10, 0)
        assert ok
        assert await fake_redis.exists(f"spend:camp:camp-A:{today}")
        ttl = await fake_redis.ttl(f"spend:camp:camp-A:{today}")
        assert ttl > bd.KEY_GRACE_SECONDS
        assert not await cache.gate_and_reserve("camp-A", "li-A", 10, 10, 0)

        # Yesterday's and legacy undated keys are stale; today's is not.
        await fake_redis.set("spend:camp:camp-A:2020-01-01", "5")
        await fake_redis.set("spend:camp:legacy", "5")
        deleted = await cache.clear_stale_daily_spend()
        assert deleted == 2
        assert await fake_redis.exists(f"spend:camp:camp-A:{today}")
        # Running the reset again (late / duplicate cron) changes nothing.
        assert await cache.clear_stale_daily_spend() == 0
        assert not await cache.gate_and_reserve("camp-A", "li-A", 10, 10, 0), "reset must not reopen today's budget"

    asyncio.run(run())


def test_release_budget_targets_the_bids_day(fake_redis):
    async def run():
        await fake_redis.set("spend:camp:camp-B:2026-09-06", "4")
        await fake_redis.set("spend:camp:camp-B:2026-09-07", "6")
        await cache.release_budget("camp", "camp-B", 4, day="2026-09-06")
        assert float(await fake_redis.get("spend:camp:camp-B:2026-09-06")) == 0
        assert float(await fake_redis.get("spend:camp:camp-B:2026-09-07")) == 6
        # refund for a day whose key is gone must not create a negative counter
        await cache.release_budget("camp", "camp-B", 4, day="2026-09-01")
        assert not await fake_redis.exists("spend:camp:camp-B:2026-09-01")

    asyncio.run(run())


def test_normalize_spent_today_hides_previous_day():
    today = bd.business_date()
    row = {"spent_today": Decimal("12.5"), "spent_today_date": today}
    assert bd.normalize_spent_today(dict(row))["spent_today"] == Decimal("12.5")
    stale = {"spent_today": Decimal("12.5"), "spent_today_date": date(2020, 1, 1)}
    out = bd.normalize_spent_today(stale)
    assert out["spent_today"] == 0 and "spent_today_date" not in out
    legacy = {"spent_today": Decimal("3"), "spent_today_date": None}
    assert bd.normalize_spent_today(legacy)["spent_today"] == Decimal("3")


# --------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

if DATABASE_URL:
    import asyncpg  # noqa: E402

    from billing_core import bill_win  # noqa: E402
    from money import money  # noqa: E402


async def _setup(conn):
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'bd-test', 'admin') RETURNING id",
        f"bd-test-{uuid.uuid4()}@test.local",
    )
    adv = await conn.fetchval(
        "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1, 'bd-test', 'active', 1000) RETURNING id", uid,
    )
    camp = await conn.fetchval(
        """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget,
                                  spent_today, spent_today_date)
           VALUES ($1, $2, 'bd-test', 'active', 100, 0, 77, DATE '2020-01-01') RETURNING id""",
        adv, uid,
    )
    li = await conn.fetchval(
        """INSERT INTO line_items (campaign_id, user_id, name, bid_type, bid_price, daily_budget,
                                   spent_today, spent_today_date)
           VALUES ($1, $2, 'bd-test', 'cpm', 5, 100, 33, DATE '2020-01-01') RETURNING id""",
        camp, uid,
    )
    return {"user": uid, "adv": adv, "camp": camp, "li": li}


async def _teardown(conn, ids):
    await conn.execute("ALTER TABLE transactions DISABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM transactions WHERE user_id = $1", ids["user"])
    await conn.execute("ALTER TABLE transactions ENABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM line_items WHERE id = $1", ids["li"])
    await conn.execute("DELETE FROM campaigns WHERE id = $1", ids["camp"])
    await conn.execute("DELETE FROM advertisers WHERE id = $1", ids["adv"])
    await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])


@db
def test_bill_win_restarts_spent_today_on_new_business_day():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn)
        try:
            # Yesterday's 77 must NOT be added to: the first win today restarts the counter.
            await bill_win(conn, bid_id=f"bd-{uuid.uuid4()}", campaign_id=ids["camp"],
                           line_item_id=ids["li"], cost=money("2.5"))
            row = await conn.fetchrow("SELECT spent_today, spent_today_date, spent_total FROM campaigns WHERE id = $1", ids["camp"])
            assert row["spent_today"] == Decimal("2.5")
            assert row["spent_today_date"] == bd.business_date()
            assert row["spent_total"] == Decimal("2.5")
            li = await conn.fetchrow("SELECT spent_today, spent_today_date FROM line_items WHERE id = $1", ids["li"])
            assert li["spent_today"] == Decimal("2.5") and li["spent_today_date"] == bd.business_date()

            # Second win the same day accumulates.
            await bill_win(conn, bid_id=f"bd-{uuid.uuid4()}", campaign_id=ids["camp"],
                           line_item_id=ids["li"], cost=money("1"))
            assert await conn.fetchval("SELECT spent_today FROM campaigns WHERE id = $1", ids["camp"]) == Decimal("3.5")
        finally:
            await _teardown(conn, ids)
            await conn.close()

    asyncio.run(run())


@db
def test_cron_reset_only_touches_previous_days():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn)
        try:
            today = bd.business_date()
            await conn.execute("UPDATE campaigns SET spent_today = 9, spent_today_date = $2 WHERE id = $1", ids["camp"], today)
            # same statement the cron endpoint runs
            await conn.execute(
                "UPDATE campaigns SET spent_today = 0, spent_today_date = NULL "
                "WHERE spent_today > 0 AND (spent_today_date IS NULL OR spent_today_date < $1)", today)
            await conn.execute(
                "UPDATE line_items SET spent_today = 0, spent_today_date = NULL "
                "WHERE spent_today > 0 AND (spent_today_date IS NULL OR spent_today_date < $1)", today)
            assert await conn.fetchval("SELECT spent_today FROM campaigns WHERE id = $1", ids["camp"]) == Decimal("9")
            assert await conn.fetchval("SELECT spent_today FROM line_items WHERE id = $1", ids["li"]) == Decimal("0")
        finally:
            await _teardown(conn, ids)
            await conn.close()

    asyncio.run(run())
