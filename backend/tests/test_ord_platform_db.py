"""B13c (ord_spec §7): организация-ОРС и сводная площадка через API (DATABASE_URL, ОРД — mock).

* POST /ord/counterparties is_self: только admin/superadmin, роль принудительно ors,
  ровно одна «наша» (частичный уникальный индекс + перенос флага).
* POST /ord/platform/register: без зарегистрированной ОРС — 422, после — ok и
  platform_settings.ord_platform; GET /ord/platform показывает недостающее.
* Миграция 049 идемпотентна.
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg
import httpx
from fastapi import FastAPI

import db
import routers.ord_router as ordr
from auth import current_user

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "049_ord_self_org.sql"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_PLATFORM_URL", raising=False)
    db._pool = None
    yield
    db._pool = None


def _run(coro):
    return asyncio.run(coro)


def _app_as(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(ordr.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _user(conn, role: str) -> dict:
    uid = await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','ord-pl',$2) RETURNING id",
        f"ord-pl-{uuid.uuid4()}@test.local", role,
    )
    return {"id": str(uid), "email": "a@x", "name": "A", "role": role, "status": "active", "permissions": {}}


async def _cleanup(conn, users: list[dict], inns: list[str]):
    await conn.execute("DELETE FROM counterparties WHERE inn = ANY($1::text[])", inns)
    await conn.execute("DELETE FROM platform_settings WHERE key = 'ord_platform'")
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [u["id"] for u in users])


def _inn() -> str:
    return "77" + str(uuid.uuid4().int)[:8]


def test_self_org_is_admin_only_and_unique(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        admin, manager = await _user(conn, "admin"), await _user(conn, "manager")
        inn1, inn2 = _inn(), _inn()
        # Снимаем чужой флаг, чтобы тест не зависел от состояния БД, и вернём его после.
        prev_self = await conn.fetchval("SELECT id FROM counterparties WHERE is_self")
        await conn.execute("UPDATE counterparties SET is_self = false WHERE is_self")
        try:
            async with _app_as(manager) as c:
                r = await c.post("/ord/counterparties", json={"name": "ООО М", "inn": inn1, "legal_type": "ul",
                                                            "role": "advertiser", "is_self": True})
                assert r.status_code == 403, r.text
            async with _app_as(admin) as c:
                r = await c.post("/ord/counterparties", json={"name": "ООО Централ", "inn": inn1, "legal_type": "ul",
                                                            "role": "advertiser", "is_self": True,
                                                            "rs_url": "https://central-dsp.ru"})
                assert r.status_code == 201, r.text
                first = r.json()
                assert first["is_self"] is True and first["role"] == "ors"
                # Вторая «наша» — флаг переезжает, уникальность не нарушена.
                r = await c.post("/ord/counterparties", json={"name": "ООО Централ 2", "inn": inn2, "legal_type": "ul",
                                                            "role": "ors", "is_self": True})
                assert r.status_code == 201, r.text
            rows = await conn.fetch("SELECT inn, is_self FROM counterparties WHERE inn = ANY($1::text[])", [inn1, inn2])
            assert {r["inn"]: r["is_self"] for r in rows} == {inn1: False, inn2: True}
        finally:
            await _cleanup(conn, [admin, manager], [inn1, inn2])
            if prev_self:
                await conn.execute("UPDATE counterparties SET is_self = true WHERE id = $1", prev_self)
            await conn.close()

    _run(run())


def test_platform_register_flow(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        admin = await _user(conn, "admin")
        inn = _inn()
        prev_self = await conn.fetchval("SELECT id FROM counterparties WHERE is_self")
        await conn.execute("UPDATE counterparties SET is_self = false WHERE is_self")
        await conn.execute("DELETE FROM platform_settings WHERE key = 'ord_platform'")
        try:
            async with _app_as(admin) as c:
                st = (await c.get("/ord/platform")).json()
                assert "self_org" in st["missing"]
                r = await c.post("/ord/platform/register")
                assert r.status_code == 422 and "self_org_missing" in r.json()["detail"]

                cp = (await c.post("/ord/counterparties", json={
                    "name": "ООО Централ", "inn": inn, "legal_type": "ul", "role": "ors",
                    "is_self": True, "rs_url": "https://central-dsp.ru"})).json()
                r = await c.post("/ord/platform/register")
                assert r.status_code == 422 and "self_org_unregistered" in r.json()["detail"]

                r = await c.post(f"/ord/counterparties/{cp['id']}/register")
                assert r.status_code == 200, r.text
                r = await c.post("/ord/platform/register")
                assert r.status_code == 200, r.text
                out = r.json()
                assert out["status"] == "ok" and out["url"] == "https://central-dsp.ru"

                st = (await c.get("/ord/platform")).json()
                assert st["missing"] == [] and st["registered"]["self_org_id"] == cp["id"]
                assert st["self_org"]["ord_status"] == "registered"
            saved = await conn.fetchval("SELECT value FROM platform_settings WHERE key = 'ord_platform'")
            assert saved
        finally:
            await _cleanup(conn, [admin], [inn])
            if prev_self:
                await conn.execute("UPDATE counterparties SET is_self = true WHERE id = $1", prev_self)
            await conn.close()

    _run(run())


def test_migration_049_is_idempotent(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            sql = MIGRATION.read_text()
            await conn.execute(sql)
            await conn.execute(sql)
            cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'counterparties'")}
            assert {"is_self", "rs_url"} <= cols
            assert await conn.fetchval("SELECT count(*) FROM counterparties WHERE is_self") <= 1
        finally:
            await conn.close()

    _run(run())
