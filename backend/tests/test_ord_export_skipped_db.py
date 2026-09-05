"""Чеклист 6.6 «Отчёт ОРД»: креативы с показами, но без erid, не пропадают молча.

`GET /cron/ord-export` отправляет в ОРД только строки с зарегистрированным erid.
До 2026-09-05 всё остальное просто отфильтровывалось в WHERE — реклама без
маркировки (ФЗ-347) была невидима. Теперь такие креативы возвращаются в
`skipped_without_erid`, пишутся в ord_export_log.request и в gauge
`ord_export_skipped_without_erid`. Требует DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
import obs_metrics  # noqa: E402
from routers import cron as cron_router  # noqa: E402
from tests.test_idor_isolation_db import _Tenant  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ORD_PROVIDER", raising=False)
    db._pool = None
    yield
    db._pool = None


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(cron_router.router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_ord_export_reports_creatives_without_erid(env):
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        # _Tenant.setup кладёт в stats_hourly 500 показов на свой креатив за текущий час.
        no_erid = await _Tenant("ordA").setup(conn)
        with_erid = await _Tenant("ordB").setup(conn)
        await conn.execute(
            """INSERT INTO ord_creatives (creative_id, erid, ord_provider, ord_external_id, status)
               VALUES ($1, 'ERID-TEST-B', 'mock', 'ext-b', 'registered')""",
            with_erid.ids["cr"],
        )
        # Идемпотентность по периоду: снять след прошлых прогонов за этот месяц.
        await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
        try:
            async with _client() as c:
                r = await c.get("/cron/ord-export", params={"period": period})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["status"] == "ok"
            assert body["rows"] >= 1

            skipped = {x["creative_id"] for x in body["skipped_without_erid"]}
            assert no_erid.ids["cr"] in skipped
            assert with_erid.ids["cr"] not in skipped
            mine = next(x for x in body["skipped_without_erid"] if x["creative_id"] == no_erid.ids["cr"])
            assert mine["impressions"] == 500
            assert mine["campaign_id"] == no_erid.ids["camp"]

            logged = await conn.fetchrow(
                "SELECT status, request FROM ord_export_log WHERE period = $1 AND kind = 'stats'", period
            )
            assert logged["status"] == "ok"
            import json

            req = json.loads(logged["request"]) if isinstance(logged["request"], str) else logged["request"]
            assert any(x["creative_id"] == no_erid.ids["cr"] for x in req["skipped_without_erid"])
            assert any(x["erid"] == "ERID-TEST-B" for x in req["stats"])

            rendered = obs_metrics.render_prometheus()
            assert "ord_export_skipped_without_erid" in rendered
        finally:
            await conn.execute("DELETE FROM ord_export_log WHERE period = $1", period)
            await no_erid.teardown(conn)
            await with_erid.teardown(conn)
            await conn.close()

    asyncio.run(run())
