"""Справочник «домен → тематика» через БД (требует DATABASE_URL): миграция,
CRUD, импорт CSV, права, фид для биддера, перенос обученных записей."""

import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
import domain_categories as dc  # noqa: E402
import routers.bidder_feed as feed  # noqa: E402
import routers.domain_categories as dc_router  # noqa: E402
from auth import current_user  # noqa: E402

FEED_TOKEN = "domcat-feed-token"
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "062_domain_categories.sql"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RTB_API_TOKEN", FEED_TOKEN)
    db._pool = None
    yield
    db._pool = None


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def _client(role: str):
    app = FastAPI()
    app.include_router(dc_router.router)
    app.include_router(feed.router)
    app.dependency_overrides[current_user] = lambda: {"id": 1, "role": role}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


class FakeRedis:
    """Минимум async-Redis для flush_learned: exists/rename/hscan/delete."""

    def __init__(self, data: dict[str, dict[str, str]]):
        self.data = data

    async def exists(self, key):
        return int(bool(self.data.get(key)))

    async def rename(self, src, dst):
        self.data[dst] = self.data.pop(src)

    async def hscan(self, key, cursor, count=1000):
        return 0, dict(self.data.get(key, {}))

    async def delete(self, key):
        self.data.pop(key, None)


def test_migration_is_idempotent(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            sql = MIGRATION.read_text(encoding="utf-8")
            await conn.execute(sql)
            await conn.execute(sql)
            assert await conn.fetchval("SELECT to_regclass('domain_categories')") is not None
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_crud_import_and_permissions(env):
    t = _tag()
    d1, d2 = f"crud-{t}.example", f"imp-{t}.example"

    async def scenario():
        async with _client("client") as c:
            assert (await c.get("/domain-categories")).status_code == 403
            assert (await c.put("/domain-categories", json={"domain": d1, "iab_codes": ["IAB1"]})).status_code == 403
            assert (await c.post("/domain-categories/import", json={"csv": f"{d2};IAB1"})).status_code == 403
            assert (await c.delete(f"/domain-categories/{d1}")).status_code == 403

        async with _client("admin") as c:
            r = await c.put("/domain-categories", json={"domain": f"WWW.{d1.upper()}", "iab_codes": ["iab1", "IAB2-3"]})
            assert r.status_code == 200 and r.json()["domain"] == d1 and r.json()["source"] == "manual"
            assert r.json()["iab_codes"] == ["IAB1", "IAB2-3"]

            assert (await c.put("/domain-categories", json={"domain": "no_dots", "iab_codes": ["IAB1"]})).status_code == 422
            assert (await c.put("/domain-categories", json={"domain": d1, "iab_codes": ["bogus"]})).status_code == 422

            found = (await c.get("/domain-categories", params={"q": t})).json()
            assert found["total"] == 1 and found["items"][0]["domain"] == d1

            bad_csv = f"{d2};IAB1\n{d2};IAB2\nbad_domain;IAB1\n"
            pre = (await c.post("/domain-categories/import", json={"csv": bad_csv})).json()
            assert pre["valid"] == 1 and len(pre["errors"]) == 2 and pre["applied"] is False
            refused = await c.post("/domain-categories/import", json={"csv": bad_csv, "apply": True})
            assert refused.status_code == 422
            assert (await c.get("/domain-categories", params={"q": d2})).json()["total"] == 0

            ok = (await c.post("/domain-categories/import", json={"csv": f"{d2};IAB1,IAB3\n{d1};IAB9", "apply": True})).json()
            assert ok["applied"] is True and ok["created"] == 1 and ok["updated"] == 1
            row = (await c.get("/domain-categories", params={"q": d1})).json()["items"][0]
            assert row["iab_codes"] == ["IAB9"]

            assert (await c.delete(f"/domain-categories/{d1}")).status_code == 200
            assert (await c.delete(f"/domain-categories/{d1}")).status_code == 404
            assert (await c.delete(f"/domain-categories/{d2}")).status_code == 200

    asyncio.run(scenario())


def test_feed_threshold_auth_and_learn_flush(env):
    t = _tag()
    manual, hot, cold = f"m-{t}.example", f"hot-{t}.example", f"cold-{t}.example"

    async def scenario():
        pool = await db.get_pool()
        await dc.upsert_manual(pool, manual, ["IAB7"])
        redis = FakeRedis({dc.LEARN_KEY: {
            hot: "5", f"{hot}|IAB1": "4", f"{hot}|IAB2": "1",
            cold: "1", f"{cold}|IAB3": "1",
            manual: "9", f"{manual}|IAB1": "9",  # ручная запись обучением не перезаписывается
            "junk_domain|IAB1": "3",
        }})
        res = await dc.flush_learned(pool, redis)
        assert res["domains"] == 2 and dc.LEARN_KEY not in redis.data and dc.LEARN_PROC_KEY not in redis.data

        # повторный запуск при пустом хэше — no-op
        assert (await dc.flush_learned(pool, redis))["skipped"] == "empty"

        # второй прогон: hits суммируются, коды сливаются
        redis.data[dc.LEARN_KEY] = {hot: "2", f"{hot}|IAB2": "2"}
        await dc.flush_learned(pool, redis)

        async with _client("admin") as c:
            assert (await c.get("/rtb-feed/domain-categories")).status_code == 401
            r = await c.get("/rtb-feed/domain-categories", headers={"Authorization": f"Bearer {FEED_TOKEN}"})
            assert r.status_code == 200
            domains = r.json()["domains"]
            assert domains[manual] == ["IAB7"]
            assert domains[hot] == ["IAB2", "IAB1"]  # IAB2: 2 в свежей пачке — выше IAB1 из прошлой
            assert cold not in domains  # hits=1 < порога

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT hits, source FROM domain_categories WHERE domain=$1", hot)
            assert row["hits"] == 7 and row["source"] == "learned"
            assert (await conn.fetchval("SELECT source FROM domain_categories WHERE domain=$1", manual)) == "manual"
            await conn.execute("DELETE FROM domain_categories WHERE domain = ANY($1)", [manual, hot, cold])

    asyncio.run(scenario())


def test_unreachable_redis_does_not_break_feed(env):
    async def scenario():
        async with _client("admin") as c:
            r = await c.get("/rtb-feed/domain-categories", headers={"Authorization": f"Bearer {FEED_TOKEN}"})
            assert r.status_code == 200 and "domains" in r.json()

    asyncio.run(scenario())
