"""Планировщик (audit O-01, C1b) — с БД.

* миграция 053 идемпотентна; ensure_seed сеет DEFAULT_JOBS и не трогает правки;
* fill_next_run считает next_run_at и выключает задачу с битым schedule;
* claim_due: два планировщика получают непересекающиеся наборы (SKIP LOCKED),
  занятая задача не захватывается повторно, протухший lease — захватывается;
* complete: сброс lease, счётчики, next_run_at от момента завершения (misfire —
  один запуск, не «догон»);
* tick_once end-to-end через httpx.MockTransport: backend получил Bearer,
  строка обновлена, метрики выставлены;
* API /scheduler/jobs: admin — 200, viewer — 403; PATCH валидирует cron и
  сбрасывает next_run_at; run — 409 для выключенной/занятой.

Требует DATABASE_URL. Таблица scheduled_jobs очищается до и после каждого
теста (тестовая БД): иначе реальные задачи (`* * * * *`) попадали бы в claim.
"""
import asyncio
import os
import sys
import uuid
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
import obs_metrics
import scheduler
from auth import current_user
from routers.scheduler_router import router as sched_router

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "053_scheduled_jobs.sql"


def _run(coro):
    return asyncio.run(coro)


async def _conn():
    return await asyncpg.connect(DATABASE_URL)


async def _insert(conn, name, *, schedule="* * * * *", enabled=True, timeout=30, next_run_at=None, locked_at=None):
    await conn.execute(
        """INSERT INTO scheduled_jobs (name, path, schedule, enabled, timeout_seconds, next_run_at, locked_at, locked_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        name, f"/cron/{name}", schedule, enabled, timeout, next_run_at, locked_at, "x" if locked_at else None,
    )


def _app_as(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(sched_router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _user(role):
    return {"id": str(uuid.uuid4()), "email": f"{role}@x", "name": role, "role": role, "status": "active", "permissions": {}}


@pytest.fixture
def env():
    async def clear():
        conn = await _conn()
        try:
            await conn.execute("DELETE FROM scheduled_jobs")
        finally:
            await conn.close()

    db._pool = None
    obs_metrics.reset_for_tests()
    _run(clear())
    yield
    _run(clear())
    db._pool = None
    obs_metrics.reset_for_tests()


def test_migration_idempotent_and_seed(env):
    async def run():
        conn = await _conn()
        try:
            await conn.execute(MIGRATION.read_text(encoding="utf-8"))  # повторное применение
            assert await scheduler.ensure_seed(conn) == len(scheduler.DEFAULT_JOBS)
            await conn.execute("DELETE FROM scheduled_jobs WHERE name = 'fx_refresh'")
            assert await scheduler.ensure_seed(conn) == 1
            row = await conn.fetchrow("SELECT enabled, schedule FROM scheduled_jobs WHERE name = 'fx_refresh'")
            assert row["enabled"] is False
            # правка админа переживает повторный seed
            await conn.execute("UPDATE scheduled_jobs SET schedule = '1 2 * * *', enabled = true WHERE name = 'fx_refresh'")
            assert await scheduler.ensure_seed(conn) == 0
            row = await conn.fetchrow("SELECT enabled, schedule FROM scheduled_jobs WHERE name = 'fx_refresh'")
            assert (row["enabled"], row["schedule"]) == (True, "1 2 * * *")
            names = {r["name"] for r in await conn.fetch("SELECT name FROM scheduled_jobs")}
            assert set(obs_metrics.CRON_JOBS) == names
        finally:
            await conn.close()

    _run(run())


def test_fill_next_run_and_bad_schedule(env):
    async def run():
        conn = await _conn()
        try:
            await _insert(conn, "t_ok", schedule="0 6 * * *")
            await _insert(conn, "t_bad", schedule="99 * * * *")
            await _insert(conn, "t_off", schedule="0 6 * * *", enabled=False)
            now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
            assert await scheduler.fill_next_run(conn, now) == 2
            ok = await conn.fetchrow("SELECT next_run_at, enabled FROM scheduled_jobs WHERE name = 't_ok'")
            assert ok["next_run_at"] == datetime(2026, 9, 12, 6, 0, tzinfo=UTC) and ok["enabled"]
            bad = await conn.fetchrow("SELECT next_run_at, enabled, last_error FROM scheduled_jobs WHERE name = 't_bad'")
            assert bad["next_run_at"] is None and bad["enabled"] is False and "bad schedule" in bad["last_error"]
            assert await conn.fetchval("SELECT next_run_at FROM scheduled_jobs WHERE name = 't_off'") is None
            assert await scheduler.fill_next_run(conn, now) == 0  # выключенные не трогаем
        finally:
            await conn.close()

    _run(run())


def test_claim_is_exclusive_and_respects_lease(env):
    async def run():
        a = await _conn()
        b = await _conn()
        try:
            past = datetime.now(UTC) - timedelta(minutes=1)
            await _insert(a, "t_due1", next_run_at=past)
            await _insert(a, "t_due2", next_run_at=past)
            await _insert(a, "t_future", next_run_at=datetime.now(UTC) + timedelta(hours=1))
            await _insert(a, "t_disabled", enabled=False, next_run_at=past)
            await _insert(a, "t_locked", next_run_at=past, locked_at=datetime.now(UTC), timeout=600)
            await _insert(a, "t_stale_lease", next_run_at=past, locked_at=datetime.now(UTC) - timedelta(hours=1), timeout=30)

            # Два планировщика в параллельных транзакциях: LIMIT 1 каждому — разные задачи.
            ta = a.transaction()
            tb = b.transaction()
            await ta.start()
            await tb.start()
            got_a = await scheduler.claim_due(a, "A", limit=1)
            got_b = await scheduler.claim_due(b, "B", limit=1)
            await ta.commit()
            await tb.commit()
            assert len(got_a) == 1 and len(got_b) == 1 and got_a[0].name != got_b[0].name

            rest = {j.name for j in await scheduler.claim_due(a, "A")}
            claimed = {got_a[0].name, got_b[0].name} | rest
            assert claimed == {"t_due1", "t_due2", "t_stale_lease"}, claimed
            assert not await scheduler.claim_due(a, "A")  # всё занято

            rows = {r["name"]: r for r in await a.fetch("SELECT name, locked_by, last_run_at FROM scheduled_jobs")}
            assert rows["t_stale_lease"]["locked_by"] in ("A", "B")
            assert rows["t_locked"]["locked_by"] == "x"
            assert rows["t_future"]["last_run_at"] is None
        finally:
            await a.close()
            await b.close()

    _run(run())


def test_complete_updates_row_and_schedules_next(env):
    async def run():
        conn = await _conn()
        try:
            long_ago = datetime.now(UTC) - timedelta(days=3)
            await _insert(conn, "t_job", schedule="*/5 * * * *", next_run_at=long_ago, locked_at=datetime.now(UTC))
            job = scheduler.Claimed("t_job", "/cron/t_job", "*/5 * * * *", 30)
            finished = datetime(2026, 9, 11, 12, 1, 30, tzinfo=UTC)
            nxt = await scheduler.complete(conn, job, status="error", http_status=500, duration_ms=42, error="boom", finished=finished)
            assert nxt == datetime(2026, 9, 11, 12, 5, tzinfo=UTC)  # от завершения, не «догон» пропущенных суток
            r = await conn.fetchrow("SELECT * FROM scheduled_jobs WHERE name = 't_job'")
            assert r["locked_at"] is None and r["locked_by"] is None
            assert (r["last_status"], r["last_http_status"], r["last_duration_ms"], r["last_error"]) == ("error", 500, 42, "boom")
            assert (r["runs_total"], r["errors_total"]) == (1, 1) and r["next_run_at"] == nxt
            await scheduler.complete(conn, job, status="ok", http_status=200, duration_ms=1, error=None, finished=finished)
            r = await conn.fetchrow("SELECT runs_total, errors_total, last_error FROM scheduled_jobs WHERE name = 't_job'")
            assert (r["runs_total"], r["errors_total"], r["last_error"]) == (2, 1, None)

            # lease-guard: чужой/перехваченный lease не затирает состояние владельца
            await conn.execute("UPDATE scheduled_jobs SET locked_at = now(), locked_by = 'OWNER' WHERE name = 't_job'")
            assert await scheduler.heartbeat(conn, job, "OTHER") is False
            assert await scheduler.heartbeat(conn, job, "OWNER") is True
            assert await scheduler.complete(conn, job, status="ok", http_status=200, duration_ms=1, error=None, worker="OTHER") is None
            r = await conn.fetchrow("SELECT runs_total, locked_by FROM scheduled_jobs WHERE name = 't_job'")
            assert (r["runs_total"], r["locked_by"]) == (2, "OWNER")
            assert await scheduler.complete(conn, job, status="ok", http_status=200, duration_ms=1, error=None, worker="OWNER") is not None
            r = await conn.fetchrow("SELECT runs_total, locked_by FROM scheduled_jobs WHERE name = 't_job'")
            assert (r["runs_total"], r["locked_by"]) == (3, None)
        finally:
            await conn.close()

    _run(run())


def test_tick_once_end_to_end(env):
    async def run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
        calls = []

        def handler(req: httpx.Request):
            calls.append((req.url.path, req.headers.get("authorization")))
            if req.url.path == "/cron/t_fail":
                return httpx.Response(500, text="kaboom")
            return httpx.Response(200, json={"ok": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sched = scheduler.Scheduler(pool, base_url="http://backend:8000", secret="sek", worker="T", client=client)
        try:
            async with pool.acquire() as conn:
                past = datetime.now(UTC) - timedelta(minutes=10)
                await _insert(conn, "t_run", next_run_at=past)
                await _insert(conn, "t_fail", next_run_at=past)
                await _insert(conn, "t_null", schedule="0 0 1 1 *")  # NULL -> заполнится, не запустится
            claimed = await sched.tick_once()
            assert {j.name for j in claimed} == {"t_run", "t_fail"}
            await asyncio.gather(*sched._inflight)
            assert sorted(calls) == [("/cron/t_fail", "Bearer sek"), ("/cron/t_run", "Bearer sek")]
            async with pool.acquire() as conn:
                rows = {r["name"]: r for r in await conn.fetch("SELECT * FROM scheduled_jobs WHERE name LIKE 't\\_%'")}
                total = await conn.fetchval("SELECT count(*) FROM scheduled_jobs")
            assert total == 3 + len(scheduler.DEFAULT_JOBS)  # тик досеял дефолты
            assert rows["t_run"]["last_status"] == "ok" and rows["t_run"]["next_run_at"] > datetime.now(UTC)
            assert rows["t_fail"]["last_status"] == "error" and "kaboom" in rows["t_fail"]["last_error"]
            assert rows["t_null"]["next_run_at"] is not None and rows["t_null"]["last_run_at"] is None
            assert all(r["locked_at"] is None for r in rows.values())
            snap = obs_metrics.render_prometheus()
            assert "scheduler_job_failures_total 1" in snap
            assert "scheduler_last_tick_timestamp_seconds" in snap and "scheduler_jobs_overdue 0" in snap
            # второй тик сразу — дефолтные задачи запланированы в будущее, ничего не просрочено
            assert await sched.tick_once() == []
        finally:
            await client.aclose()
            await pool.close()

    _run(run())


def test_tick_claims_no_more_than_free_slots(env):
    """concurrency=1 и две просроченные: вторая остаётся незахваченной (locked_at NULL)
    до освобождения слота, а не лежит с lease в очереди семафора."""
    async def run():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=3)
        release = asyncio.Event()

        async def handler(req: httpx.Request):
            await release.wait()
            return httpx.Response(200, json={"ok": True})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sched = scheduler.Scheduler(pool, base_url="http://backend:8000", secret="sek", worker="T", client=client, concurrency=1)
        try:
            async with pool.acquire() as conn:
                past = datetime.now(UTC) - timedelta(minutes=10)
                await _insert(conn, "t_a", next_run_at=past - timedelta(minutes=1))
                await _insert(conn, "t_b", next_run_at=past)
            first = await sched.tick_once()
            assert [j.name for j in first] == ["t_a"] and sched.free_slots == 0
            assert await sched.tick_once() == []  # слот занят — ничего не захватываем
            async with pool.acquire() as conn:
                rows = {r["name"]: r for r in await conn.fetch("SELECT name, locked_by FROM scheduled_jobs WHERE name IN ('t_a','t_b')")}
            assert rows["t_a"]["locked_by"] == "T" and rows["t_b"]["locked_by"] is None
            release.set()
            await asyncio.gather(*sched._inflight)
            assert [j.name for j in await sched.tick_once()] == ["t_b"]
            await asyncio.gather(*sched._inflight)
            async with pool.acquire() as conn:
                assert await conn.fetchval("SELECT count(*) FROM scheduled_jobs WHERE name IN ('t_a','t_b') AND last_status = 'ok' AND runs_total = 1") == 2
        finally:
            await client.aclose()
            await pool.close()

    _run(run())


def test_api_roles_patch_and_run(env):
    async def run():
        conn = await _conn()
        try:
            await _insert(conn, "t_api", schedule="0 6 * * *", next_run_at=datetime.now(UTC) + timedelta(hours=1))
            await _insert(conn, "t_off", enabled=False)
            await _insert(conn, "t_busy", next_run_at=datetime.now(UTC) + timedelta(hours=1), locked_at=datetime.now(UTC))
            async with _app_as(_user("viewer")) as c:
                assert (await c.get("/scheduler/jobs")).status_code == 403
                assert (await c.post("/scheduler/jobs/t_api/run")).status_code == 403
            async with _app_as(_user("admin")) as c:
                r = await c.get("/scheduler/jobs")
                assert r.status_code == 200, r.text
                jobs = {j["name"]: j for j in r.json()["jobs"]}
                assert "t_api" in jobs and jobs["t_api"]["next_run_in_seconds"] > 0
                assert set(obs_metrics.CRON_JOBS) <= set(jobs)  # list сеет дефолты

                r = await c.patch("/scheduler/jobs/t_api", json={"schedule": "bogus"})
                assert r.status_code == 422
                r = await c.patch("/scheduler/jobs/t_api", json={"schedule": " 0  7 * * * ", "timeout_seconds": 99})
                assert r.status_code == 200, r.text
                assert r.json()["schedule"] == "0 7 * * *" and r.json()["next_run_at"] is None
                assert r.json()["timeout_seconds"] == 99
                assert (await c.patch("/scheduler/jobs/t_api", json={"timeout_seconds": 1})).status_code == 422
                assert (await c.patch("/scheduler/jobs/nope", json={"enabled": False})).status_code == 404
                assert (await c.patch("/scheduler/jobs/t_api", json={})).status_code == 422

                r = await c.post("/scheduler/jobs/t_off/run")
                assert r.status_code == 409 and "disabled" in r.json()["detail"]
                r = await c.post("/scheduler/jobs/t_busy/run")
                assert r.status_code == 409 and "running" in r.json()["detail"]
                assert (await c.post("/scheduler/jobs/nope/run")).status_code == 404
                r = await c.post("/scheduler/jobs/t_api/run")
                assert r.status_code == 200, r.text
                assert r.json()["next_run_in_seconds"] == 0
            nxt = await conn.fetchval("SELECT next_run_at FROM scheduled_jobs WHERE name = 't_api'")
            assert nxt <= datetime.now(UTC)
        finally:
            await conn.close()

    _run(run())
