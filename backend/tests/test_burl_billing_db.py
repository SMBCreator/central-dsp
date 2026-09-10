"""Audit R-07 (DB): миграция 046 идемпотентна, default billing_event для новых
партнёров = burl (существующие остаются на win), billable_imps доходит до
/stats/report из обоих источников (hourly и events), late хранится.

Требует DATABASE_URL (skip иначе). Миграция уже применена CI-раннером;
здесь файл выполняется повторно.
"""
from __future__ import annotations

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

import db
from tests.test_idor_isolation_db import _app_as, _Tenant

MIG = Path(__file__).resolve().parents[1] / "migrations" / "046_billing_event_burl.sql"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LAUNCH_GATE", "off")
    db._pool = None
    yield
    db._pool = None


def test_migration_046_defaults_report_and_late_flag(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        a = await _Tenant("burlA").setup(conn)
        sp = None
        try:
            camp, li, cr, uid = a.ids["camp"], a.ids["li"], a.ids["cr"], a.ids["user"]
            # --- миграция повторно: идемпотентна, default burl, CHECK на месте
            await conn.execute(MIG.read_text())
            await conn.execute(MIG.read_text())
            sp = await conn.fetchval(
                "INSERT INTO supply_partners (user_id, name, endpoint_key) VALUES ($1, 'burl-ssp', $2) RETURNING id",
                uid, uuid.uuid4().hex,
            )
            assert await conn.fetchval("SELECT billing_event FROM supply_partners WHERE id = $1", sp) == "burl"
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("UPDATE supply_partners SET billing_event = 'nurl' WHERE id = $1", sp)
            await conn.execute("UPDATE supply_partners SET billing_event = 'win' WHERE id = $1", sp)
            assert await conn.fetchval("SELECT billing_event FROM supply_partners WHERE id = $1", sp) == "win"

            # --- hourly: 5 wins, 2 impressions, 3 billable, spend 0.3 -> ecpm по billable
            await conn.execute(
                """INSERT INTO stats_hourly (ts_hour, campaign_id, line_item_id, creative_id, supply_partner_id,
                                             bids, wins, impressions, billable_imps, spend)
                   VALUES (date_trunc('hour', now()), $1, $2, $3, $4, 10, 5, 2, 3, 0.3)""",
                camp, li, cr, sp,
            )
            # --- events: win без денег (режим burl), billable с деньгами, один late, win_expired
            bids = [uuid.uuid4().hex for _ in range(3)]
            for b in bids:
                await conn.execute(
                    "INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, supply_partner_id, price) "
                    "VALUES ('win', $1, $2, $3, $4, $5, 100)", b, camp, li, cr, sp,
                )
            await conn.execute(
                "INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, supply_partner_id, price, clear_price) "
                "VALUES ('billable', $1, $2, $3, $4, $5, 100, 80)", bids[0], camp, li, cr, sp,
            )
            await conn.execute(
                "INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, supply_partner_id, price, clear_price, late) "
                "VALUES ('billable', $1, $2, $3, $4, $5, 100, 70, true)", bids[1], camp, li, cr, sp,
            )
            await conn.execute(
                "INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, supply_partner_id, price) "
                "VALUES ('win_expired', $1, $2, $3, $4, $5, 100)", bids[2], camp, li, cr, sp,
            )
            assert await conn.fetchval(
                "SELECT count(*) FROM bid_events WHERE bid_id = ANY($1::text[]) AND late", bids) == 1

            client = _app_as(a.as_user())
            today = (await conn.fetchval("SELECT current_date")).isoformat()
            base = {"date_from": today, "date_to": today, "campaign_ids": [camp]}

            r = await client.post("/stats/report", json={**base, "groupings": ["campaign"]})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["meta"]["source"] == "hourly"
            t = body["totals"]
            # tenant setup уже мог положить свою строку stats_hourly — сравниваем приращения
            assert t["billable_imps"] >= 3 and t["wins"] >= 5
            assert t["billable_imps"] != t["wins"] and t["billable_imps"] != t["impressions"]
            assert t["ecpm"] == pytest.approx(t["spend"] / t["billable_imps"] * 1000, rel=1e-3)

            # ad_type — events-only измерение: отчёт идёт из bid_events (spend_expr/billable_expr)
            r = await client.post("/stats/report", json={**base, "groupings": ["supply_partner", "ad_type"]})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["meta"]["source"] == "events"
            row = next(x for x in body["rows"] if x.get("supply_partner_id") == str(sp))
            # 3 win-события без денег, 2 billable с деньгами: wins=3, billable=2, spend=(80+70)/1000
            assert row["wins"] == 3 and row["billable_imps"] == 2
            assert row["spend"] == pytest.approx(0.15, rel=1e-3)
            assert row["ecpm"] == pytest.approx(75.0, rel=1e-3)
        finally:
            await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", a.ids["camp"])
            if sp:
                await conn.execute("DELETE FROM stats_hourly WHERE supply_partner_id = $1", sp)
                await conn.execute("DELETE FROM supply_partners WHERE id = $1", sp)
            await a.teardown(conn)
            await conn.close()

    asyncio.run(scenario())
