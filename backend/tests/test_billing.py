"""Integration tests for transactional billing (require DATABASE_URL).

Creates its own throwaway advertiser/campaign/line item, exercises bill_win
under duplicates and concurrency, then removes everything it created.
"""
import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402

from billing_core import bill_win  # noqa: E402
from money import money  # noqa: E402


@pytest.fixture()
def fixture_ids():
    return {}


async def _setup(conn, *, balance="100.00", total_budget="50.00"):
    """Create user -> advertiser -> campaign -> line item; return ids."""
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'billing-test', 'admin') RETURNING id",
        f"billing-test-{uuid.uuid4()}@test.local",
    )
    adv = await conn.fetchval(
        "INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1, 'billing-test', 'active', $2) RETURNING id",
        uid, Decimal(balance),
    )
    camp = await conn.fetchval(
        """INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget)
           VALUES ($1, $2, 'billing-test', 'active', 1000, $3) RETURNING id""",
        adv, uid, Decimal(total_budget),
    )
    li = await conn.fetchval(
        """INSERT INTO line_items (campaign_id, user_id, name, bid_type, bid_price, daily_budget)
           VALUES ($1, $2, 'billing-test', 'cpm', 5, 1000) RETURNING id""",
        camp, uid,
    )
    return {"user": uid, "adv": adv, "camp": camp, "li": li}


async def _teardown(conn, ids):
    # ledger is append-only: deleting the user cascades transactions via FK
    await conn.execute("ALTER TABLE transactions DISABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM transactions WHERE user_id = $1", ids["user"])
    await conn.execute("ALTER TABLE transactions ENABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM line_items WHERE id = $1", ids["li"])
    await conn.execute("DELETE FROM campaigns WHERE id = $1", ids["camp"])
    await conn.execute("DELETE FROM advertisers WHERE id = $1", ids["adv"])
    await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])


def test_single_win_bills_once_and_writes_ledger():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn)
        try:
            bid_id = f"t-{uuid.uuid4()}"
            res = await bill_win(conn, bid_id=bid_id, campaign_id=ids["camp"],
                                 line_item_id=ids["li"], cost=money("0.005"))
            assert res.billed and res.balance_after == Decimal("99.995")
            row = await conn.fetchrow(
                "SELECT amount, balance_after, currency, campaign_id FROM transactions WHERE bid_id = $1", bid_id)
            assert row["amount"] == Decimal("-0.005")
            assert row["balance_after"] == Decimal("99.995")
            assert row["currency"] == "RUB"
            assert row["campaign_id"] == ids["camp"]
            spent = await conn.fetchval("SELECT spent_total FROM campaigns WHERE id = $1", ids["camp"])
            assert spent == Decimal("0.005")
        finally:
            await _teardown(conn, ids)
            await conn.close()
    asyncio.run(run())


def test_duplicate_win_is_noop():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn)
        try:
            bid_id = f"t-{uuid.uuid4()}"
            r1 = await bill_win(conn, bid_id=bid_id, campaign_id=ids["camp"], line_item_id=ids["li"], cost=money("0.01"))
            r2 = await bill_win(conn, bid_id=bid_id, campaign_id=ids["camp"], line_item_id=ids["li"], cost=money("0.01"))
            assert r1.billed and not r2.billed
            n = await conn.fetchval("SELECT count(*) FROM transactions WHERE bid_id = $1", bid_id)
            assert n == 1
            bal = await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", ids["adv"])
            assert bal == Decimal("99.99")  # charged exactly once
        finally:
            await _teardown(conn, ids)
            await conn.close()
    asyncio.run(run())


def test_concurrent_wins_no_lost_updates():
    """20 parallel win notices with distinct bids: balance and ledger must agree."""
    N, COST = 20, money("0.01")

    async def run():
        setup_conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(setup_conn)
        try:
            async def one_win(i):
                c = await asyncpg.connect(DATABASE_URL)
                try:
                    return await bill_win(c, bid_id=f"t-c{i}-{ids['camp']}", campaign_id=ids["camp"],
                                          line_item_id=ids["li"], cost=COST)
                finally:
                    await c.close()

            results = await asyncio.gather(*[one_win(i) for i in range(N)])
            assert all(r.billed for r in results)
            bal = await setup_conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", ids["adv"])
            assert bal == Decimal("100.00") - COST * N
            ledger_sum = await setup_conn.fetchval(
                "SELECT -sum(amount) FROM transactions WHERE user_id = $1 AND type = 'spend'", ids["user"])
            assert ledger_sum == COST * N
            spent = await setup_conn.fetchval("SELECT spent_total FROM campaigns WHERE id = $1", ids["camp"])
            assert spent == COST * N
        finally:
            await _teardown(setup_conn, ids)
            await setup_conn.close()
    asyncio.run(run())


def test_concurrent_duplicate_bid_bills_exactly_once():
    """The same bid_id raced from many connections must bill exactly once."""
    async def run():
        setup_conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(setup_conn)
        try:
            bid_id = f"t-race-{uuid.uuid4()}"

            async def one_win():
                c = await asyncpg.connect(DATABASE_URL)
                try:
                    return await bill_win(c, bid_id=bid_id, campaign_id=ids["camp"],
                                          line_item_id=ids["li"], cost=money("0.02"))
                finally:
                    await c.close()

            results = await asyncio.gather(*[one_win() for _ in range(10)])
            assert sum(1 for r in results if r.billed) == 1
            bal = await setup_conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", ids["adv"])
            assert bal == Decimal("99.98")
        finally:
            await _teardown(setup_conn, ids)
            await setup_conn.close()
    asyncio.run(run())


def test_balance_exhaustion_pauses_campaigns():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn, balance="0.005", total_budget="0")
        try:
            res = await bill_win(conn, bid_id=f"t-{uuid.uuid4()}", campaign_id=ids["camp"],
                                 line_item_id=ids["li"], cost=money("0.005"))
            assert res.billed and res.paused and res.balance_after == Decimal("0")
            status = await conn.fetchval("SELECT status FROM campaigns WHERE id = $1", ids["camp"])
            assert status == "paused"
        finally:
            await _teardown(conn, ids)
            await conn.close()
    asyncio.run(run())


def test_total_budget_exhaustion_pauses_campaign():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn, balance="100.00", total_budget="0.01")
        try:
            res = await bill_win(conn, bid_id=f"t-{uuid.uuid4()}", campaign_id=ids["camp"],
                                 line_item_id=ids["li"], cost=money("0.01"))
            assert res.billed and res.paused
            status = await conn.fetchval("SELECT status FROM campaigns WHERE id = $1", ids["camp"])
            assert status == "paused"
        finally:
            await _teardown(conn, ids)
            await conn.close()
    asyncio.run(run())


def test_ledger_is_append_only():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        ids = await _setup(conn)
        try:
            bid_id = f"t-{uuid.uuid4()}"
            await bill_win(conn, bid_id=bid_id, campaign_id=ids["camp"], line_item_id=ids["li"], cost=money("0.01"))
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute("UPDATE transactions SET amount = 0 WHERE bid_id = $1", bid_id)
            with pytest.raises(asyncpg.RaiseError):
                await conn.execute("DELETE FROM transactions WHERE bid_id = $1", bid_id)
        finally:
            await _teardown(conn, ids)
            await conn.close()
    asyncio.run(run())
