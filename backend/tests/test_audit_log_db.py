"""Журнал действий: только админы, фильтры, курсор, CSV; middleware пишет
мутации и не пишет чтения/RTB. Нужен DATABASE_URL (как test_idor_isolation_db).

Стиль — sync-тесты + asyncio.run + сброс db._pool на каждый тест: пул asyncpg
привязан к event loop, а loop у каждого asyncio.run свой."""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

import db
from auth import current_user
from routers import audit_router

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

class Seed:
    """Уникальный TAG на каждый тест: таблица append-only, строки прошлых
    прогонов остаются в БД, поэтому фильтруем по своему TAG."""

    def __init__(self) -> None:
        self.tag = uuid.uuid4().hex[:8]
        self.mail = f"@{self.tag}.audit.local"
        self.rows = [
            (f"admin{self.mail}", "admin", "POST", "/campaigns", 201, "campaigns", None, "10.0.0.1"),
            (f"admin{self.mail}", "admin", "PATCH", f"/campaigns/c-{self.tag}", 200, "campaigns", f"c-{self.tag}", "10.0.0.1"),
            (f"buyer{self.mail}", "manager", "DELETE", f"/creatives/cr-{self.tag}", 403, "creatives", f"cr-{self.tag}", "10.0.0.2"),
            (f"buyer{self.mail}", "manager", "POST", "/users", 500, "users", None, "10.0.0.2"),
        ]
        self.ids: list[int] = []


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def env():
    """audit_log append-only (триггер trg_audit_append_only запрещает
    UPDATE/DELETE), поэтому без cleanup."""
    db._pool = None
    seed = Seed()
    _run(_seed(seed))
    yield seed
    db._pool = None


async def _seed(seed: Seed) -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for email, role, method, path, code, entity, eid, ip in seed.rows:
            seed.ids.append(await conn.fetchval(
                """INSERT INTO audit_log (user_email, user_role, method, path, status_code, entity, entity_id, ip)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
                email, role, method, path, code, entity, eid, ip,
            ))
    finally:
        await conn.close()


def _app_as(role: str) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(audit_router.router)

    async def _fake_user():
        return {"id": str(uuid.uuid4()), "email": f"{role}@x.local", "role": role, "permissions": {}}

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.parametrize("role", ["manager", "advertiser", "analyst", "finance", "viewer", "agency", "client"])
def test_non_admins_get_403(env, role):
    async def go():
        async with _app_as(role) as c:
            assert (await c.get("/audit/log")).status_code == 403
            assert (await c.get("/audit/log.csv")).status_code == 403
            assert (await c.get("/audit/entities")).status_code == 403

    _run(go())


def test_admin_sees_newest_first_with_cursor(env):
    ids = sorted(env.ids, reverse=True)
    MAIL = env.mail

    async def go():
        async with _app_as("admin") as c:
            r = await c.get("/audit/log", params={"limit": 2, "user_email": MAIL})
            assert r.status_code == 200, r.text
            page = r.json()
            assert [x["id"] for x in page["items"]] == ids[:2]
            assert page["next_before_id"] == page["items"][-1]["id"]

            r2 = await c.get("/audit/log", params={"limit": 2, "user_email": MAIL, "before_id": page["next_before_id"]})
            page2 = r2.json()
            assert [x["id"] for x in page2["items"]] == ids[2:4]
            assert page2["next_before_id"] is None  # ровно 4 строки — конец

    _run(go())


def test_filters(env):
    MAIL, TAG = env.mail, env.tag

    async def go():
        async with _app_as("superadmin") as c:
            by_entity = (await c.get("/audit/log", params={"entity": "campaigns", "user_email": MAIL})).json()["items"]
            assert {x["entity"] for x in by_entity} == {"campaigns"} and len(by_entity) == 2

            by_id = (await c.get("/audit/log", params={"entity_id": f"cr-{TAG}"})).json()["items"]
            assert len(by_id) == 1 and by_id[0]["method"] == "DELETE" and by_id[0]["status_code"] == 403

            errors = (await c.get("/audit/log", params={"status_min": 400, "user_email": MAIL})).json()["items"]
            assert {x["status_code"] for x in errors} == {403, 500}

            lower = (await c.get("/audit/log", params={"method": "patch", "user_email": MAIL})).json()["items"]
            assert len(lower) == 1 and lower[0]["path"] == f"/campaigns/c-{TAG}"

            assert (await c.get("/audit/log", params={"method": "GET"})).status_code == 422
            assert (await c.get("/audit/log", params={"limit": 999})).status_code == 422

    _run(go())


def test_csv_has_bom_and_all_rows(env):
    MAIL, TAG = env.mail, env.tag

    async def go():
        async with _app_as("admin") as c:
            r = await c.get("/audit/log.csv", params={"user_email": MAIL})
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/csv")
            assert 'filename="audit_log.csv"' in r.headers["content-disposition"]
            text = r.text
            assert text.startswith("\ufeffid;Время (UTC);Пользователь")
            lines = [ln for ln in text.splitlines() if ln.strip()]
            assert len(lines) == 1 + 4
            assert f"/campaigns/c-{TAG}" in text

    _run(go())


def test_entities_list_is_static_labels(env):
    async def go():
        async with _app_as("admin") as c:
            items = (await c.get("/audit/entities")).json()
            keys = {i["key"] for i in items}
            assert {"campaigns", "creatives", "users", "settings"} <= keys
            assert all(i["label"] for i in items)

    _run(go())


def test_middleware_writes_mutations_and_skips_reads_and_rtb(env):
    """Реальный middleware поверх мини-приложения: POST пишется, GET и /rtb/* —
    нет; без сессии user_* = NULL; IP — первый адрес из X-Forwarded-For."""
    from audit import AuditMiddleware

    TAG = env.tag
    app = FastAPI()
    app.add_middleware(AuditMiddleware)
    path = f"/widgets-{TAG}"

    @app.post(path)
    async def create_widget():
        return {"ok": True}

    @app.get(path)
    async def list_widgets():
        return []

    @app.post("/rtb/bid")
    async def bid():
        return {}

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post(path, headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"})).status_code == 200
            assert (await c.get(path)).status_code == 200
            assert (await c.post("/rtb/bid")).status_code == 200
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            rows = await conn.fetch(
                "SELECT method, path, status_code, entity, ip, user_email FROM audit_log WHERE path LIKE $1",
                f"/widgets-{TAG}%",
            )
            assert len(rows) == 1
            r = rows[0]
            assert r["method"] == "POST" and r["path"] == path and r["entity"] == f"widgets-{TAG}"
            assert r["ip"] == "203.0.113.9"
            assert r["user_email"] is None
            # /rtb/* никогда не попадает в журнал — проверяем по свежим строкам
            recent = await conn.fetch("SELECT path FROM audit_log ORDER BY id DESC LIMIT 20")
            assert all(x["path"] != "/rtb/bid" for x in recent)
        finally:
            await conn.close()

    _run(go())
