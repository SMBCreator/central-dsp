"""Audit M-01/M-02: balance only via /billing, atomically and idempotently.

Two layers:

* HTTP-level without Postgres (fake pool): advertiser CRUD rejects `balance`
  for every non-finance role; a failed ledger INSERT rolls the balance back.
* Integration with DATABASE_URL: deposit/adjust are one transaction, replay of
  the same Idempotency-Key produces one ledger row, a concurrent race on the
  same key credits exactly once, and sum(ledger) == balance.
"""
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routers.advertisers as adv_router  # noqa: E402
import routers.billing as billing_router  # noqa: E402
from auth import current_user  # noqa: E402

ADV_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"


# --------------------------------------------------------------------------
# HTTP level, no database
# --------------------------------------------------------------------------
class _RecordingPool:
    """Records every SQL statement; never touches a database."""

    def __init__(self):
        self.sql: list[str] = []

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return {"id": ADV_ID, "user_id": USER_ID, "name": "x", "domain": None, "category": None,
                "balance": Decimal("0"), "currency": "RUB", "status": "active"}

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return []

    async def execute(self, sql, *args):
        self.sql.append(sql)
        return "UPDATE 1"


def _app_with_user(router_module, role: str, pool):
    app = FastAPI()
    app.include_router(router_module.router)

    async def _me():
        return {"id": USER_ID, "role": role, "status": "active", "email": "u@x.ru"}

    app.dependency_overrides[current_user] = _me
    return app, pool


@pytest.fixture
def adv_client(monkeypatch):
    def make(role: str):
        pool = _RecordingPool()

        async def _pool():
            return pool

        monkeypatch.setattr(adv_router, "get_pool", _pool)
        app, _ = _app_with_user(adv_router, role, pool)
        return TestClient(app), pool

    return make


# finance has no campaign-write right at all (403); everyone else who can
# create/edit advertisers gets a validation error (422). Either way: no SQL.
ROLES = [("manager", 422), ("advertiser", 422), ("agency", 422), ("admin", 422),
         ("superadmin", 422), ("finance", 403), ("viewer", 403), ("analyst", 403)]


@pytest.mark.parametrize("role,expected", ROLES)
def test_create_advertiser_with_balance_is_rejected(adv_client, role, expected):
    client, pool = adv_client(role)
    r = client.post("/advertisers", json={"name": "ООО Ромашка", "balance": 999999})
    assert r.status_code == expected, r.text
    assert not any("INSERT" in s for s in pool.sql)


@pytest.mark.parametrize("role,expected", ROLES)
def test_patch_advertiser_balance_is_rejected(adv_client, role, expected):
    client, pool = adv_client(role)
    r = client.patch(f"/advertisers/{ADV_ID}", json={"balance": 999999})
    assert r.status_code == expected, r.text
    assert not any("balance =" in s for s in pool.sql)


def test_create_advertiser_never_writes_balance(adv_client):
    client, pool = adv_client("manager")
    r = client.post("/advertisers", json={"name": "ООО Ромашка"})
    assert r.status_code == 201, r.text
    inserts = [s for s in pool.sql if "INSERT INTO advertisers" in s]
    assert inserts and "balance" not in inserts[0]


class _FailingLedgerConn:
    """Fake asyncpg connection: UPDATE succeeds, ledger INSERT raises."""

    def __init__(self, state):
        self.state = state

    @asynccontextmanager
    async def transaction(self):
        snapshot = dict(self.state)
        try:
            yield
        except Exception:
            self.state.clear()
            self.state.update(snapshot)
            raise

    async def fetchrow(self, sql, *args):
        if "FOR UPDATE" in sql:
            return {"id": ADV_ID, "balance": self.state["balance"]}
        if "INSERT INTO transactions" in sql:
            raise RuntimeError("ledger down")
        if "idempotency_key = $1" in sql:
            return None
        raise AssertionError(sql)

    async def execute(self, sql, *args):
        if "UPDATE advertisers SET balance" in sql:
            self.state["balance"] = args[1]
            return "UPDATE 1"
        raise AssertionError(sql)


class _FailingLedgerPool:
    def __init__(self):
        self.state = {"balance": Decimal("100.00")}

    @asynccontextmanager
    async def acquire(self):
        yield _FailingLedgerConn(self.state)


def test_deposit_rolls_back_balance_when_ledger_insert_fails(monkeypatch):
    pool = _FailingLedgerPool()

    async def _pool():
        return pool

    monkeypatch.setattr(billing_router, "get_pool", _pool)
    app, _ = _app_with_user(billing_router, "finance", pool)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/billing/deposit", json={"advertiser_id": ADV_ID, "amount": 50})
    assert r.status_code == 500
    assert pool.state["balance"] == Decimal("100.00")


def test_deposit_rejects_unknown_fields_and_zero(monkeypatch):
    pool = _FailingLedgerPool()

    async def _pool():
        return pool

    monkeypatch.setattr(billing_router, "get_pool", _pool)
    app, _ = _app_with_user(billing_router, "finance", pool)
    client = TestClient(app)
    assert client.post("/billing/deposit", json={"advertiser_id": ADV_ID, "amount": 0}).status_code == 422
    assert client.post("/billing/deposit", json={"advertiser_id": ADV_ID, "amount": 1.005}).status_code == 422
    assert client.post("/billing/adjust", json={"advertiser_id": ADV_ID, "amount": 0, "description": "x"}).status_code == 422


# --------------------------------------------------------------------------
# Integration with Postgres
# --------------------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")
db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

if DATABASE_URL:
    import asyncpg  # noqa: E402

    import db as dbmod  # noqa: E402


async def _setup(conn):
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'money-test', 'finance') RETURNING id",
        f"money-test-{uuid.uuid4()}@test.local",
    )
    adv = await conn.fetchval(
        "INSERT INTO advertisers (user_id, name, status) VALUES ($1, 'money-test', 'active') RETURNING id", uid,
    )
    return {"user": uid, "adv": adv}


async def _teardown(conn, ids):
    await conn.execute("ALTER TABLE transactions DISABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM transactions WHERE user_id = $1", ids["user"])
    await conn.execute("ALTER TABLE transactions ENABLE TRIGGER trg_transactions_append_only")
    await conn.execute("DELETE FROM advertisers WHERE id = $1", ids["adv"])
    await conn.execute("DELETE FROM users WHERE id = $1", ids["user"])


def _finance_app(pool, uid):
    async def _pool():
        return pool

    billing_router.get_pool = _pool  # type: ignore[assignment]
    app = FastAPI()
    app.include_router(billing_router.router)

    async def _me():
        return {"id": str(uid), "role": "finance", "status": "active", "email": "f@x.ru"}

    app.dependency_overrides[current_user] = _me
    return app


@db
def test_deposit_and_adjust_are_ledgered_and_replayable():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        ids = await _setup(conn)
        try:
            # The asyncpg pool is bound to this loop, so drive the ASGI app in
            # the same loop instead of the threaded TestClient.
            app = _finance_app(pool, ids["user"])
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
            adv = str(ids["adv"])
            key = f"dep-{uuid.uuid4()}"

            r1 = await client.post("/billing/deposit", json={"advertiser_id": adv, "amount": "150.10"},
                                   headers={"Idempotency-Key": key})
            assert r1.status_code == 201, r1.text
            assert r1.json()["balance"] == 150.10 and r1.json()["replayed"] is False

            r2 = await client.post("/billing/deposit", json={"advertiser_id": adv, "amount": "150.10"},
                                   headers={"Idempotency-Key": key})
            assert r2.status_code == 201, r2.text
            assert r2.json()["replayed"] is True
            assert r2.json()["transaction_id"] == r1.json()["transaction_id"]

            # same key, different amount -> conflict, nothing credited
            r3 = await client.post("/billing/deposit", json={"advertiser_id": adv, "amount": "1.00"},
                                   headers={"Idempotency-Key": key})
            assert r3.status_code == 409

            r4 = await client.post("/billing/adjust", json={"advertiser_id": adv, "amount": "-50.10",
                                                            "description": "возврат",
                                                            "idempotency_key": f"adj-{key}"})
            assert r4.status_code == 201, r4.text
            assert r4.json()["balance"] == 100.0

            # debit below zero is refused and leaves no ledger trace
            r5 = await client.post("/billing/adjust", json={"advertiser_id": adv, "amount": "-500",
                                                            "description": "слишком много"})
            assert r5.status_code == 409
            await client.aclose()

            bal = await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", ids["adv"])
            ledger = await conn.fetchval("SELECT sum(amount) FROM transactions WHERE advertiser_id = $1", ids["adv"])
            n = await conn.fetchval("SELECT count(*) FROM transactions WHERE advertiser_id = $1", ids["adv"])
            assert bal == Decimal("100.00")
            assert ledger == Decimal("100.00")
            assert n == 2
        finally:
            await _teardown(conn, ids)
            await pool.close()
            await conn.close()

    asyncio.run(run())


@db
def test_concurrent_deposits_same_key_credit_once():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        ids = await _setup(conn)
        try:
            async def _pool():
                return pool

            billing_router.get_pool = _pool  # type: ignore[assignment]
            user = {"id": str(ids["user"]), "role": "finance", "status": "active"}
            key = f"race-{uuid.uuid4()}"

            async def one():
                return await billing_router._apply_money_operation(
                    user=user, advertiser_id=str(ids["adv"]), amount=Decimal("10"), op_type="deposit",
                    description="race", idempotency_key=key, allow_negative_result=False,
                )

            results = await asyncio.gather(*[one() for _ in range(8)])
            assert sum(1 for r in results if not r["replayed"]) == 1
            assert len({r["transaction_id"] for r in results}) == 1
            bal = await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", ids["adv"])
            assert bal == Decimal("10.00")
        finally:
            await _teardown(conn, ids)
            await pool.close()
            await conn.close()

    asyncio.run(run())


@db
def test_concurrent_distinct_deposits_no_lost_update():
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
        ids = await _setup(conn)
        try:
            async def _pool():
                return pool

            billing_router.get_pool = _pool  # type: ignore[assignment]
            user = {"id": str(ids["user"]), "role": "finance", "status": "active"}

            async def one(i):
                return await billing_router._apply_money_operation(
                    user=user, advertiser_id=str(ids["adv"]), amount=Decimal("1.5"), op_type="deposit",
                    description=f"d{i}", idempotency_key=None, allow_negative_result=False,
                )

            await asyncio.gather(*[one(i) for i in range(20)])
            bal = await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1", ids["adv"])
            ledger = await conn.fetchval("SELECT sum(amount) FROM transactions WHERE advertiser_id = $1", ids["adv"])
            assert bal == Decimal("30.00") == ledger
        finally:
            await _teardown(conn, ids)
            await pool.close()
            await conn.close()

    asyncio.run(run())
