"""B13 (audit L-02/L-03): версии erid, заморозка юр. полей, выгрузка по версиям.

* Смена содержимого одобренного креатива закрывает текущую версию (erid остаётся
  в истории) и регистрирует новую с другим erid.
* Смена контрагента/договора/ККТУ у креатива с erid -> 409 ord_locked.
* Месячная выгрузка делит показы между версиями по часу действия и пишет
  построчный результат (ord_export_rows); статусы ok/partial/failed/empty;
  повтор no-op, force -> stats_correction; advisory-lock -> 409.
Требует DATABASE_URL, ОРД — mock. Спека: docs/audit/ord_spec.md.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg
import httpx
from fastapi import FastAPI

import db
import ord as ord_pkg
import routers.creatives as crs
import routers.ord_router as ordr
from auth import current_user
from ord.service import register_creative
from routers import cron as cron_router
from tests.test_ord_flow_db import _admin, _Env


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("ORD_OZON_API_KEY", raising=False)
    monkeypatch.delenv("ORD_FEED_GATE", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    db._pool = None
    yield
    db._pool = None


def _app(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(crs.router)
    app.include_router(ordr.router)
    app.include_router(cron_router.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _setup(conn):
    e = await _Env().setup(conn, geo=["RU"])
    ct = await e.add_contract(conn)
    cr = await e.add_creative(conn, status="approved", contract_id=ct)
    e.ids["ct"] = ct
    e.ids["cr"] = cr
    return e


async def _versions(conn, cr):
    return [dict(r) for r in await conn.fetch(
        "SELECT version, erid, status, registered_at, superseded_at FROM ord_creatives WHERE creative_id = $1 ORDER BY version",
        cr,
    )]


# ---------------------------------------------------------------------------
def test_content_change_keeps_old_erid_in_history_and_issues_new(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            pool = await db.get_pool()
            cr_row = dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", e.ids["cr"]))
            res = await register_creative(pool, cr_row)
            assert res["outcome"] == "ok" and res["version"] == 1
            erid_v1 = res["erid"]
            locked = await conn.fetchval("SELECT ord_locked_at FROM creatives WHERE id = $1", e.ids["cr"])
            assert locked is not None

            # повторная регистрация без изменений — идемпотентна
            again = await register_creative(pool, cr_row)
            assert again["erid"] == erid_v1 and again["version"] == 1

            # админ меняет картинку: старая версия закрывается, новая получает новый erid
            async with _app(_admin(e)) as c:
                r = await c.patch(f"/creatives/{e.ids['cr']}", json={"asset_url": "http://a/i2.png"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ord"]["status"] == "ok"
            assert body["erid"] and body["erid"] != erid_v1

            vs = await _versions(conn, e.ids["cr"])
            assert [v["version"] for v in vs] == [1, 2]
            assert vs[0]["erid"] == erid_v1 and vs[0]["status"] == "superseded" and vs[0]["superseded_at"]
            assert vs[1]["erid"] == body["erid"] and vs[1]["status"] == "registered" and vs[1]["superseded_at"] is None

            async with _app(_admin(e)) as c:
                r = await c.get(f"/ord/creatives/{e.ids['cr']}/versions")
            assert r.status_code == 200
            assert [(v["version"], v["is_current"]) for v in r.json()] == [(1, False), (2, True)]
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_legal_fields_locked_after_erid(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            pool = await db.get_pool()
            cr_row = dict(await conn.fetchrow("SELECT * FROM creatives WHERE id = $1", e.ids["cr"]))
            assert (await register_creative(pool, cr_row))["outcome"] == "ok"
            other_cp = await conn.fetchval(
                """INSERT INTO counterparties (user_id, name, inn, legal_type, role, ord_status, ord_external_id)
                   VALUES ($1,'ООО Другой','7700000003','ul','advertiser','registered','CP-3') RETURNING id""",
                e.ids["user"],
            )
            async with _app(_admin(e)) as c:
                r = await c.patch(f"/creatives/{e.ids['cr']}", json={"counterparty_id": str(other_cp)})
                assert r.status_code == 409, r.text
                assert r.json()["detail"]["code"] == "ord_locked"
                assert r.json()["detail"]["fields"] == ["counterparty_id"]

                r = await c.patch(f"/creatives/{e.ids['cr']}", json={"kktu_code": "30.20"})
                assert r.status_code == 409 and r.json()["detail"]["fields"] == ["kktu_code"]

                # то же значение — не изменение; имя менять можно
                r = await c.patch(f"/creatives/{e.ids['cr']}", json={"kktu_code": "30.10", "name": "banner-2"})
                assert r.status_code == 200, r.text
            vs = await _versions(conn, e.ids["cr"])
            assert len(vs) == 1 and vs[0]["status"] == "registered"
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_legal_fields_editable_before_erid(env):
    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            async with _app(_admin(e)) as c:
                r = await c.patch(f"/creatives/{e.ids['cr']}", json={"kktu_code": "30.20"})
            assert r.status_code == 200, r.text
        finally:
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


# ---------------------------------------------------------------------------
async def _add_stats(conn, e, ts_hour, imps, spend="1.0"):
    await conn.execute(
        """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, bids, wins,
                                     impressions, clicks, spend)
           VALUES ($1, $2, $3, $4, $5, $5, $5, 1, $6::numeric)""",
        ts_hour, e.ids["camp"], e.ids["li"], e.ids["cr"], imps, spend,
    )


def _period_and_hours(months_back: int = 1):
    """Период — прошлый месяц (чужие строки за него не мешают: проверяем только
    свои creative_id). Часы: середина месяца."""
    now = datetime.now(UTC)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(months_back):
        first = (first - timedelta(days=1)).replace(day=1)
    mid = first.replace(day=15, hour=12)
    return mid.strftime("%Y-%m"), mid


def test_export_splits_impressions_between_erid_versions(env):
    period, h0 = _period_and_hours()

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            cr = e.ids["cr"]
            # v1 действует с h0-5h, закрыта в h0+0:20; v2 зарегистрирована в h0+0:40
            await conn.execute(
                """INSERT INTO ord_creatives (creative_id, version, erid, ord_provider, status, registered_at, superseded_at)
                   VALUES ($1, 1, 'ERID-V1', 'mock', 'superseded', $2, $3),
                          ($1, 2, 'ERID-V2', 'mock', 'registered', $4, NULL)""",
                cr, h0 - timedelta(hours=5), h0 + timedelta(minutes=20), h0 + timedelta(minutes=40),
            )
            await _add_stats(conn, e, h0 - timedelta(hours=2), 100)   # -> v1
            await _add_stats(conn, e, h0, 30)                          # час смены -> v2 (действует на конец часа)
            await _add_stats(conn, e, h0 + timedelta(hours=3), 200)    # -> v2
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)

            async with _app(_admin(e)) as c:
                r = await c.get("/cron/ord-export", params={"period": period})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["status"] == "ok", body
                rows = await conn.fetch(
                    "SELECT version, erid, impressions, status, external_statistic_id FROM ord_export_rows "
                    "WHERE log_id = $1::uuid AND creative_id = $2::uuid ORDER BY version",
                    body["log_id"], cr,
                )
                got = [(r["version"], r["erid"], r["impressions"], r["status"]) for r in rows]
                assert got == [(1, "ERID-V1", 100, "accepted"), (2, "ERID-V2", 230, "accepted")]
                assert rows[0]["external_statistic_id"] == f"{cr}:v1:{period}"
                assert not any(x["creative_id"] == cr for x in body["skipped_without_erid"])

                # повтор за период — no-op
                r2 = await c.get("/cron/ord-export", params={"period": period})
                assert r2.json().get("skipped") == "already exported"

                # force — коррекция с тем же externalStatisticId
                r3 = await c.get("/cron/ord-export", params={"period": period, "force": "1"})
                assert r3.status_code == 200, r3.text
                assert r3.json()["kind"] == "stats_correction" and r3.json()["status"] == "ok"
                kinds = await conn.fetch(
                    "SELECT kind, status FROM ord_export_log WHERE period = $1 ORDER BY started_at", period
                )
                assert [(k["kind"], k["status"]) for k in kinds] == [("stats", "ok"), ("stats_correction", "ok")]

                r = await c.get(f"/ord/exports/{body['log_id']}/rows")
                assert r.status_code == 200
                assert {x["erid"] for x in r.json() if x["creative_id"] == cr} == {"ERID-V1", "ERID-V2"}
        finally:
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_export_partial_and_failed_statuses(env, monkeypatch):
    period, h0 = _period_and_hours()

    class _Rejecting:
        provider = "fake"

        def __init__(self, reject_all: bool):
            self.reject_all = reject_all

        async def register_platform(self, data):
            return {"external_id": data["external_id"]}

        async def register_counterparty(self, data):
            return {"external_id": data["external_id"]}

        async def submit_stats(self, period, rows):
            if self.reject_all:
                return {"accepted": [], "rejected": [{"id": r["external_statistic_id"], "error": "boom"} for r in rows]}
            first, *rest = rows
            return {"accepted": [r["external_statistic_id"] for r in rest],
                    "rejected": [{"id": first["external_statistic_id"], "error": "creative not found"}]}

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e1 = await _setup(conn)
        e2 = await _setup(conn)
        try:
            for e in (e1, e2):
                await conn.execute(
                    """INSERT INTO ord_creatives (creative_id, version, erid, ord_provider, status, registered_at)
                       VALUES ($1, 1, $2, 'mock', 'registered', $3)""",
                    e.ids["cr"], f"ERID-{e.ids['cr'][:8]}", h0 - timedelta(days=3),
                )
                await _add_stats(conn, e, h0, 50)
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)

            adapter = _Rejecting(reject_all=False)

            async def _fake_adapter():
                return adapter

            monkeypatch.setattr(ord_pkg, "get_ord_adapter", _fake_adapter)
            async with _app(_admin(e1)) as c:
                r = await c.get("/cron/ord-export", params={"period": period})
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["status"] == "partial"
                assert body["rows_rejected"] == 1 and body["rows_accepted"] == body["rows"] - 1
                log = await conn.fetchrow("SELECT status, rows_accepted, rows_rejected FROM ord_export_log WHERE id = $1::uuid", body["log_id"])
                assert (log["status"], log["rows_rejected"]) == ("partial", 1)
                rej = await conn.fetchrow(
                    "SELECT error FROM ord_export_rows WHERE log_id = $1::uuid AND status = 'rejected'", body["log_id"]
                )
                assert rej["error"] == "creative not found"

                # partial не считается выгруженным: следующий запуск повторяет отправку
                adapter.reject_all = True
                r = await c.get("/cron/ord-export", params={"period": period})
                assert r.status_code == 200, r.text
                assert r.json()["status"] == "failed" and r.json()["rows_accepted"] == 0
        finally:
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            await e1.teardown(conn)
            await e2.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_export_impressions_without_any_erid_is_failed_not_ok(env):
    # Далёкий период: в нём не должно быть чужих строк с erid, иначе статус был бы partial/ok.
    period, h0 = _period_and_hours(months_back=37)

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        e = await _setup(conn)
        try:
            await _add_stats(conn, e, h0, 70)
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            async with _app(_admin(e)) as c:
                r = await c.get("/cron/ord-export", params={"period": period})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "failed"
            assert e.ids["cr"] in {x["creative_id"] for x in r.json()["skipped_without_erid"]}
            log = await conn.fetchval("SELECT status FROM ord_export_log WHERE period = $1", period)
            assert log == "failed"
        finally:
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            await e.teardown(conn)
            await conn.close()

    asyncio.run(run())


def test_export_advisory_lock_rejects_concurrent_run(env):
    period, _ = _period_and_hours()

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.fetchval("SELECT pg_advisory_lock(hashtext($1))", f"ord_export:{period}")
            async with _app({"id": "x", "role": "admin", "email": "a@x", "name": "A", "status": "active",
                             "permissions": {}}) as c:
                r = await c.get("/cron/ord-export", params={"period": period})
            assert r.status_code == 409, r.text
        finally:
            await conn.fetchval("SELECT pg_advisory_unlock(hashtext($1))", f"ord_export:{period}")
            await conn.close()

    asyncio.run(run())
