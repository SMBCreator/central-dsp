"""ТЗ trackers/macros, фаза 3 (7.1, V3-V9): PATCH /creatives/{id} с пикселями.

* админ: статус approved не меняется, в audit_log старое и новое значение;
* менеджер: approved -> pending и уведомление модераторам; pending не меняется;
* пустая строка очищает поле (NULL); то же значение — no-op без повторной модерации;
* неизвестный макрос — 422 с подсказкой, ничего не сохраняется;
* viewer — 403.
Требует DATABASE_URL; сущности создаются и удаляются тестом.
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncpg  # noqa: E402

from test_creatives_batch_db import DATABASE_URL, _banner, _client, _run, _Tenant, env, pytestmark  # noqa: E402,F401

IMP = "https://px.test/i?cb=${CDSP_CACHEBUSTER}&c=${CDSP_COUNTRY}"
CLICK = "https://px.test/c"


async def _creative(c, t: _Tenant, **over) -> dict:
    r = await c.post("/creatives", json=_banner(t, **over))
    assert r.status_code == 201, r.text
    return r.json()


async def _set_status(conn, cid: str, status: str, erid: str | None = None) -> None:
    await conn.execute("UPDATE creatives SET status = $2, erid = $3 WHERE id = $1", cid, status, erid)


async def _admin_for_notifications(conn) -> str:
    return str(await conn.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1,'x','mod','admin') RETURNING id",
        f"mod-{uuid.uuid4()}@test.local",
    ))


async def _notif_count(conn, uid: str) -> int:
    return await conn.fetchval("SELECT count(*) FROM notifications WHERE user_id = $1 AND type = 'moderation_pending'", uid)


def test_admin_changes_pixel_of_approved_keeps_status_and_audits(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t, imp_tracker_url="https://old.test/i")
                await _set_status(conn, cr["id"], "approved", "erid-1")
                r = await c.patch(f"/creatives/{cr['id']}", json={"imp_tracker_url": IMP, "click_tracker_url": CLICK})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["status"] == "approved" and body["erid"] == "erid-1"
                assert body["imp_tracker_url"] == IMP and body["click_tracker_url"] == CLICK
            row = await conn.fetchrow(
                "SELECT payload FROM audit_log WHERE entity = 'creatives' AND entity_id = $1 AND path LIKE '%/pixels'", cr["id"]
            )
            p = json.loads(row["payload"])
            assert p["pixels"]["imp_tracker_url"] == {"old": "https://old.test/i", "new": IMP}
            assert p["pixels"]["click_tracker_url"] == {"old": None, "new": CLICK}
            assert p["remoderation"] is False
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_manager_changes_pixel_of_approved_goes_pending_and_notifies(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn, role="manager")
        mod = await _admin_for_notifications(conn)
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t)
                await _set_status(conn, cr["id"], "approved", "erid-2")
                before = await _notif_count(conn, mod)
                r = await c.patch(f"/creatives/{cr['id']}", json={"click_tracker_url": CLICK})
                assert r.status_code == 200, r.text
                assert r.json()["status"] == "pending"
                assert r.json()["erid"] == "erid-2"
                assert await _notif_count(conn, mod) == before + 1
                again = await c.patch(f"/creatives/{cr['id']}", json={"click_tracker_url": CLICK})
                assert again.status_code == 200 and again.json()["status"] == "pending"
                assert await _notif_count(conn, mod) == before + 1
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", mod)
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_manager_changes_pixel_of_pending_keeps_status_and_no_notification(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn, role="manager")
        mod = await _admin_for_notifications(conn)
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t)
                await _set_status(conn, cr["id"], "pending")
                before = await _notif_count(conn, mod)
                r = await c.patch(f"/creatives/{cr['id']}", json={"imp_tracker_url": IMP})
                assert r.status_code == 200 and r.json()["status"] == "pending"
                assert r.json()["imp_tracker_url"] == IMP
                assert await _notif_count(conn, mod) == before
        finally:
            await conn.execute("DELETE FROM users WHERE id = $1", mod)
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_same_value_is_noop_and_empty_string_clears(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn, role="manager")
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t, imp_tracker_url=IMP)
                await _set_status(conn, cr["id"], "approved")
                same = await c.patch(f"/creatives/{cr['id']}", json={"imp_tracker_url": f"  {IMP}  "})
                assert same.status_code == 200 and same.json()["status"] == "approved"
                cleared = await c.patch(f"/creatives/{cr['id']}", json={"imp_tracker_url": ""})
                assert cleared.status_code == 200, cleared.text
                assert cleared.json()["imp_tracker_url"] is None
            assert await conn.fetchval("SELECT imp_tracker_url FROM creatives WHERE id = $1", cr["id"]) is None
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_unknown_macro_is_rejected_and_nothing_saved(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t, imp_tracker_url=CLICK)
                r = await c.patch(
                    f"/creatives/{cr['id']}",
                    json={"imp_tracker_url": "https://px.test/i?c=${CDSP_COUNTY}", "click_tracker_url": CLICK},
                )
                assert r.status_code == 422, r.text
                assert "COUNTRY" in r.text
            row = await conn.fetchrow("SELECT imp_tracker_url, click_tracker_url FROM creatives WHERE id = $1", cr["id"])
            assert row["imp_tracker_url"] == CLICK and row["click_tracker_url"] is None
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_viewer_cannot_patch_pixels(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t)
            async with _client(t.user("viewer")) as c:
                r = await c.patch(f"/creatives/{cr['id']}", json={"imp_tracker_url": IMP})
                assert r.status_code == 403
            assert await conn.fetchval("SELECT imp_tracker_url FROM creatives WHERE id = $1", cr["id"]) is None
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())


def test_moderation_queue_returns_pixels(env):
    async def scenario():
        import routers.moderation as mod_r
        from fastapi import FastAPI
        import httpx
        from auth import current_user

        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant().setup(conn)
        try:
            async with _client(t.user()) as c:
                cr = await _creative(c, t, imp_tracker_url=IMP, click_tracker_url=CLICK)
            await _set_status(conn, cr["id"], "pending")
            app = FastAPI()
            app.include_router(mod_r.router)
            app.dependency_overrides[current_user] = lambda: t.user()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as m:
                r = await m.get("/moderation/queue?status=pending")
            assert r.status_code == 200, r.text
            mine = [x for x in r.json() if x["id"] == cr["id"]]
            assert mine and mine[0]["imp_tracker_url"] == IMP and mine[0]["click_tracker_url"] == CLICK
        finally:
            await t.teardown(conn)
            await conn.close()

    _run(scenario())
