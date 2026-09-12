"""B13g: ошибки ЕРИР после регистрации (docs/audit/ord_spec.md §7).

* /cron/ord-notifications забирает уведомления постранично по курсору
  (gtNotificationId) и пишет их в ord_notifications идемпотентно.
* Объект (контрагент, договор, версия erid, акт) переходит в erir_error с
  текстом ошибки; ord_external_id сохраняется.
* Успешная повторная регистрация объекта закрывает его уведомления
  (resolved_at) и возвращает registered/ok.
* GET /ord/notifications ограничен scope владельца.
* Версия erid в erir_error остаётся в экспорте статистики.
Требует DATABASE_URL, ОРД — mock.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg
import httpx
from fastapi import FastAPI

import db
import obs_metrics
import routers.ord_router as ordr
from auth import current_user
from ord.mock import MockOrdAdapter
from ord.notifications import CURSOR_KEY, load_cursor
from ord.service import register_creative
from routers import cron as cron_router
from tests.test_ord_flow_db import _admin, _Env
from tests.test_ord_invoices_db import _invoice_body, _setup


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(MockOrdAdapter, "notifications", [])
    db._pool = None
    yield
    db._pool = None


def _app(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(ordr.router)
    app.include_router(cron_router.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _note(nid: int, object_type: str, external_id: str, text: str = "ЕРИР: ошибка") -> dict:
    return {"id": nid, "object_id": f"ord-{nid}", "external_id": external_id, "object_type": object_type,
            "text": text, "date": "2026-09-11T10:00:00Z", "updated_at": "2026-09-11T10:00:00Z",
            "url": f"https://ord.example/{nid}"}


async def _clear_cursor(conn):
    await conn.execute("DELETE FROM platform_settings WHERE key = $1", CURSOR_KEY)


def test_cron_marks_objects_and_reregistration_resolves(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        await _clear_cursor(conn)
        try:
            async with _app(_admin(e)) as c:
                r = await c.post("/ord/invoices", json=_invoice_body(
                    e, [{"creative_id": e.ids["cr"], "version": 1, "period": "2025-09"}]))
                assert r.status_code == 201, r.text
                inv_id = r.json()["id"]
                r = await c.post(f"/ord/invoices/{inv_id}/submit")
                assert r.status_code == 200 and r.json()["status"] == "ok", r.text

            MockOrdAdapter.notifications.extend([
                _note(1, "ORGANISATION", e.ids["cp"], "ИНН не найден"),
                _note(2, "CONTRACT", e.ids["ct"], "Нет даты договора"),
                _note(3, "CREATIVE", f"{e.ids['cr']}:v1", "Описание не соответствует ККТУ"),
                _note(4, "INVOICE", inv_id, "Сумма акта не совпадает"),
                _note(5, "PLATFORM", "central-dsp", "Площадка без URL"),
            ])
            async with _app(_admin(e)) as c:
                r = await c.get("/cron/ord-notifications")
                assert r.status_code == 200, r.text
                body = r.json()
                assert (body["fetched"], body["new"], body["cursor_to"]) == (5, 5, 5)
                # повторный запуск: курсор сдвинут, ничего нового
                r = await c.get("/cron/ord-notifications")
                assert (r.json()["fetched"], r.json()["new"]) == (0, 0)
                assert await load_cursor(conn, "mock") == 5

            cp = await conn.fetchrow("SELECT ord_status, ord_error, ord_external_id FROM counterparties WHERE id=$1", e.ids["cp"])
            assert (cp["ord_status"], cp["ord_error"], cp["ord_external_id"]) == ("erir_error", "ИНН не найден", "CP-1")
            ct = await conn.fetchrow("SELECT ord_status, ord_error FROM ord_contracts WHERE id=$1", e.ids["ct"])
            assert (ct["ord_status"], ct["ord_error"]) == ("erir_error", "Нет даты договора")
            cr = await conn.fetchrow("SELECT status, error, erid FROM ord_creatives WHERE creative_id=$1 AND version=1", e.ids["cr"])
            assert (cr["status"], cr["error"]) == ("erir_error", "Описание не соответствует ККТУ") and cr["erid"]
            inv = await conn.fetchrow("SELECT status, error FROM ord_invoices WHERE id=$1", inv_id)
            assert (inv["status"], inv["error"]) == ("erir_error", "Сумма акта не совпадает")
            owners = await conn.fetch(
                "SELECT object_type, owner_id::text AS owner FROM ord_notifications WHERE resolved_at IS NULL ORDER BY notification_id")
            assert [o["object_type"] for o in owners] == ["organisation", "contract", "creative", "invoice", "platform"]
            assert {o["owner"] for o in owners[:4]} == {e.ids["user"]} and owners[4]["owner"] is None
            assert "ord_notifications_unresolved 5" in obs_metrics.render_prometheus()

            # список для владельца и для чужого пользователя
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/notifications")
                assert r.status_code == 200 and r.json()["total"] == 5
            stranger = await _Env().setup(conn, geo=["RU"])
            try:
                async with _app({**_admin(stranger), "role": "advertiser"}) as c:
                    assert (await c.get("/ord/notifications")).json()["total"] == 0
            finally:
                await stranger.teardown(conn)

            # версия в erir_error остаётся в предпросмотре акта (экспорт статистики)
            async with _app(_admin(e)) as c:
                r = await c.get("/ord/invoices/preview", params={
                    "contract_id": e.ids["ct"], "period_start": "2025-10-01", "period_end": "2025-10-31"})
                assert r.status_code == 200 and any(i["version"] == 1 for i in r.json()["items"]), r.text

            # повторная регистрация закрывает уведомления и возвращает registered/ok
            async with _app(_admin(e)) as c:
                assert (await c.post(f"/ord/counterparties/{e.ids['cp']}/register")).status_code == 200
                assert (await c.post(f"/ord/contracts/{e.ids['ct']}/register")).status_code == 200
                r = await c.post(f"/ord/invoices/{inv_id}/submit")
                assert r.status_code == 200 and r.json()["status"] == "ok", r.text
            pool = await db.get_pool()
            creative = dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", e.ids["cr"]))
            res = await register_creative(pool, creative)
            assert res["outcome"] == "ok"

            assert await conn.fetchval("SELECT ord_status FROM counterparties WHERE id=$1", e.ids["cp"]) == "registered"
            assert await conn.fetchval("SELECT ord_status FROM ord_contracts WHERE id=$1", e.ids["ct"]) == "registered"
            assert await conn.fetchval("SELECT status FROM ord_creatives WHERE creative_id=$1 AND version=1", e.ids["cr"]) == "registered"
            assert await conn.fetchval("SELECT status FROM ord_invoices WHERE id=$1", inv_id) == "ok"
            open_types = await conn.fetch("SELECT object_type, resolved_reason FROM ord_notifications WHERE resolved_at IS NULL")
            assert [o["object_type"] for o in open_types] == ["platform"]
            assert await conn.fetchval(
                "SELECT count(*) FROM ord_notifications WHERE resolved_reason = 'reregistered'") == 4
        finally:
            await conn.execute("DELETE FROM ord_notifications")
            await _clear_cursor(conn)
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_cron_paginates_and_is_idempotent(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        await _clear_cursor(conn)
        try:
            MockOrdAdapter.notifications.extend(_note(i, "PLATFORM", "central-dsp") for i in range(1, 8))
            from ord.notifications import sync_notifications

            pool = await db.get_pool()
            res = await sync_notifications(pool, MockOrdAdapter(), page_size=3)
            assert (res["pages"], res["fetched"], res["new"], res["cursor_to"]) == (3, 7, 7, 7)
            # тот же id повторно (ОРД обновил текст) — строка обновляется, не дублируется
            MockOrdAdapter.notifications[-1]["text"] = "обновлено"
            await conn.execute("UPDATE platform_settings SET value = jsonb_set(value, '{last_id}', '6') WHERE key=$1", CURSOR_KEY)
            res = await sync_notifications(pool, MockOrdAdapter(), page_size=3)
            assert (res["fetched"], res["new"]) == (1, 0)
            assert await conn.fetchval("SELECT count(*) FROM ord_notifications") == 7
            assert await conn.fetchval("SELECT text FROM ord_notifications WHERE notification_id = 7") == "обновлено"
        finally:
            await conn.execute("DELETE FROM ord_notifications")
            await _clear_cursor(conn)
            await conn.close()

    asyncio.run(run())
