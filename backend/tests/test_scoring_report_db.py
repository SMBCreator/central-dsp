"""Отчёт скоринга и ручные блоки площадок на реальном Postgres (требует DATABASE_URL).

Регрессии 2026-09-05:
- `GET /scoring/report` без профиля падал 500: `($2 || ' days')::interval`
  заставлял asyncpg типизировать window_days как text, а мы передавали int.
  Юнит-тесты с фейковым пулом этого не видят — только живая БД.
- Кнопка блокировки в UI не передавала campaign_id, хотя блок-лист — на кампанию.
  Здесь фиксируем контракт: POST {campaign_id, domain} → строка становится
  blocked/manual; DELETE /blocks/{campaign_id}/{domain} снимает блок.
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

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import db  # noqa: E402
import scoring  # noqa: E402
from auth import current_user  # noqa: E402
from routers import scoring as scoring_router  # noqa: E402
from tests.test_idor_isolation_db import _Tenant  # noqa: E402


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LAUNCH_GATE", "off")
    db._pool = None
    scoring.reset_cache_for_tests()
    scoring_router._report_cache.clear()
    yield
    db._pool = None
    scoring.reset_cache_for_tests()
    scoring_router._report_cache.clear()


def _client(user_row: dict) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(scoring_router.router)

    async def _fake_user():
        return user_row

    app.dependency_overrides[current_user] = _fake_user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def _impressions(conn, camp, li, cr, domain: str, n: int):
    for _ in range(n):
        await conn.execute(
            """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, domain,
                                       clear_price, is_test, created_at)
               VALUES ('impression', gen_random_uuid()::text, $1, $2, $3, $4, 1000, false, now())""",
            camp, li, cr, domain,
        )


def test_report_without_profile_and_manual_block_roundtrip(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        t = await _Tenant("scoreA").setup(conn)
        camp = t.ids["camp"]
        try:
            await _impressions(conn, camp, t.ids["li"], t.ids["cr"], "Bad-Site.example", 3)
            client = _client(t.as_user())

            # 1) Профиль не выбран → дефолтный, window_days=7 (int). Раньше здесь был 500.
            r = await client.get("/scoring/report", params={"dimension": "domain"})
            assert r.status_code == 200, r.text
            rows = {row["domain"]: row for row in r.json()["rows"]}
            assert "bad-site.example" in rows and rows["bad-site.example"]["blocked"] is False

            # SSP-разрез использует тот же паттерн интервала — тоже не должен падать.
            r_ssp = await client.get("/scoring/report", params={"dimension": "ssp"})
            assert r_ssp.status_code == 200, r_ssp.text

            # 2) Ручной блок требует campaign_id (блок-лист — на кампанию).
            r = await client.post("/scoring/blocks", json={"domain": "bad-site.example"})
            assert r.status_code == 422

            r = await client.post("/scoring/blocks", json={"campaign_id": camp, "domain": "bad-site.example"})
            assert r.status_code == 200, r.text
            assert r.json()["source"] == "manual"

            r = await client.get("/scoring/report", params={"dimension": "domain", "campaign_id": camp})
            assert r.status_code == 200, r.text
            row = next(x for x in r.json()["rows"] if x["domain"] == "bad-site.example")
            assert row["blocked"] is True and row["block_source"] == "manual"
            assert r.json()["total_blocked"] == 1

            # 3) Снятие блока — путём, который зовёт UI: /blocks/{campaign_id}/{domain}.
            r = await client.delete(f"/scoring/blocks/{camp}/bad-site.example")
            assert r.status_code == 200, r.text

            r = await client.get("/scoring/report", params={"dimension": "domain", "campaign_id": camp})
            row = next(x for x in r.json()["rows"] if x["domain"] == "bad-site.example")
            assert row["blocked"] is False and r.json()["total_blocked"] == 0
        finally:
            await conn.execute("DELETE FROM scoring_blocklist WHERE campaign_id = $1", camp)
            await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", camp)
            await t.teardown(conn)
            await conn.close()

    asyncio.run(scenario())
