"""Audit F-02 / B14c (DB): backfill миграции 045, /stats/report по всем измерениям,
служебные строки уровня рекламодателя, coverage/confidence, kpi_trustworthy.

Требует DATABASE_URL (skip иначе). Миграция уже применена CI-раннером; здесь
создаём конверсии «до B14c» (без строки атрибуции) и повторно выполняем файл.
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

import conversion_attribution as attribution
import db
from stats_math import (
    NOT_SET_LABEL,
    OUT_OF_WINDOW_LABEL,
    UNATTRIBUTED_LABEL,
)
from tests.test_idor_isolation_db import _app_as, _Tenant

MIG = Path(__file__).resolve().parents[1] / "migrations" / "045_conversion_attributions.sql"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LAUNCH_GATE", "off")
    db._pool = None
    yield
    db._pool = None


async def _bid(conn, camp, li, cr, *, os_name=None, tagid=None, age="0 seconds"):
    bid_id = uuid.uuid4().hex
    await conn.execute(
        f"""INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, price,
                                    os, tagid, device_type, created_at)
            VALUES ('bid', $1, $2, $3, $4, 10, $5, $6, 'mobile', now() - interval '{age}')""",
        bid_id, camp, li, cr, os_name, tagid,
    )
    return bid_id


async def _goal(conn, px):
    return await conn.fetchval(
        "INSERT INTO goals (pixel_id, name, goal_type, match_value) VALUES ($1,'buy','event','buy') RETURNING id", px
    )


async def _conversion(conn, goal, px, *, clid=None, camp=None, li=None, cr=None, os_hint=None):
    """Строка conversions «как писал pixel.py до B14c»: campaign_id только у атрибутированных."""
    return await conn.fetchval(
        """INSERT INTO conversions (goal_id, pixel_id, session_id, clid, campaign_id, line_item_id, creative_id,
                                    device_type)
           VALUES ($1,$2,$3,$4,$5,$6,$7,'mobile') RETURNING id""",
        goal, px, uuid.uuid4().hex, clid, camp, li, cr,
    )


def test_backfill_report_and_kpi_guard(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        a = await _Tenant("attrA").setup(conn)
        try:
            camp, li, cr, px, uid = a.ids["camp"], a.ids["li"], a.ids["cr"], a.ids["px"], a.ids["user"]
            goal = await _goal(conn, px)
            # 3 attributed (2 с os=Android, 1 без os), 1 out_of_window (бид 40 дней), 2 unattributed (без clid)
            b1 = await _bid(conn, camp, li, cr, os_name="Android", tagid="p1")
            b2 = await _bid(conn, camp, li, cr, os_name="Android", tagid="p1")
            b3 = await _bid(conn, camp, li, cr, os_name=None, tagid="p2")
            b_old = await _bid(conn, camp, li, cr, os_name="iOS", age="40 days")
            ids = [
                await _conversion(conn, goal, px, clid=b1, camp=camp, li=li, cr=cr),
                await _conversion(conn, goal, px, clid=b2, camp=camp, li=li, cr=cr),
                await _conversion(conn, goal, px, clid=b3, camp=camp, li=li, cr=cr),
                await _conversion(conn, goal, px, clid=b_old),
                await _conversion(conn, goal, px),
                await _conversion(conn, goal, px),
            ]
            # --- backfill 045 (повторный прогон файла): идемпотентно, статусы по правилам
            await conn.execute(MIG.read_text())
            rows = await conn.fetch(
                "SELECT conversion_id, attribution_status, os, tagid, owner_user_id, bid_at "
                "FROM conversion_attributions WHERE conversion_id = ANY($1::uuid[])", ids)
            by_id = {str(r["conversion_id"]): r for r in rows}
            assert len(by_id) == 6
            assert [by_id[str(i)]["attribution_status"] for i in ids] == \
                ["attributed", "attributed", "attributed", "out_of_window", "unattributed", "unattributed"]
            assert by_id[str(ids[0])]["os"] == "Android" and by_id[str(ids[0])]["tagid"] == "p1"
            assert by_id[str(ids[3])]["os"] is None and by_id[str(ids[3])]["bid_at"] is not None
            assert all(str(r["owner_user_id"]) == uid for r in rows)
            await conn.execute(MIG.read_text())
            assert await conn.fetchval(
                "SELECT count(*) FROM conversion_attributions WHERE conversion_id = ANY($1::uuid[])", ids) == 6

            client = _app_as(a.as_user())
            today = (await conn.fetchval("SELECT current_date")).isoformat()
            base = {"date_from": today, "date_to": today}

            # --- уровень кампании: конверсии по os (раньше было недоступно), без служебных строк
            r = await client.post("/stats/report", json={**base, "groupings": ["os"], "campaign_ids": [camp]})
            assert r.status_code == 200, r.text
            body = r.json()
            rows = {row["os"]: row for row in body["rows"]}
            assert rows["Android"]["conversions"] == 2 and rows["Android"]["kind"] == "data"
            assert rows[NOT_SET_LABEL]["conversions"] == 1 and rows[NOT_SET_LABEL]["cpa"] is None
            assert UNATTRIBUTED_LABEL not in rows and OUT_OF_WINDOW_LABEL not in rows
            assert body["totals"]["conversions"] == 3 and body["totals"]["conversions_attributed"] == 3
            meta = body["meta"]["conversions"]
            assert meta["scope"] == "campaign"
            assert meta == {**meta, "attributed": 3, "out_of_window": 1, "unattributed": 2}
            assert meta["coverage"]["campaign"] == 0.5 and meta["coverage"]["os"] == round(2 / 6, 4)
            assert meta["confidence"] == "low"
            assert body["meta"]["conversions_available"] is True

            # --- уровень рекламодателя: + строки «Вне окна» / «Без атрибуции», totals = все 6
            r2 = await client.post("/stats/report", json={**base, "groupings": ["tagid"]})
            b2j = r2.json()
            rows2 = {row["tagid"]: row for row in b2j["rows"]}
            assert rows2["p1"]["conversions"] == 2 and rows2["p2"]["conversions"] == 1
            assert rows2[OUT_OF_WINDOW_LABEL]["conversions"] == 1 and rows2[OUT_OF_WINDOW_LABEL]["kind"] == "out_of_window"
            assert rows2[UNATTRIBUTED_LABEL]["conversions"] == 2 and rows2[UNATTRIBUTED_LABEL]["cpa"] is None
            assert b2j["totals"]["conversions"] == 6 and b2j["totals"]["conversions_attributed"] == 3
            assert sum(row["conversions"] for row in b2j["rows"]) == b2j["totals"]["conversions"]
            assert b2j["meta"]["conversions"]["scope"] == "advertiser"

            # --- hourly-источник (day) — та же логика служебных строк
            r3 = await client.post("/stats/report", json={**base, "groupings": ["day"]})
            kinds = {row["kind"] for row in r3.json()["rows"]}
            assert {"data", "out_of_window", "unattributed"} <= kinds

            # --- loss_reason: конверсии не определены, служебных строк нет
            r4 = await client.post("/stats/report", json={**base, "groupings": ["loss_reason"]})
            assert r4.json()["meta"]["conversions_available"] is False
            assert all(row["kind"] not in ("unattributed", "out_of_window") for row in r4.json()["rows"])

            # --- guard: покрытие 0.5 < 0.8 → conversion-KPI недоверенные
            assert await attribution.kpi_trustworthy_for_owner(conn, uid, 24) is False
            await conn.execute(
                "UPDATE conversion_attributions SET attribution_status = 'attributed', campaign_id = $2 "
                "WHERE conversion_id = ANY($1::uuid[])", ids[3:], camp)
            assert await attribution.kpi_trustworthy_for_owner(conn, uid, 24) is True

            # --- gauge refresh не падает и отдаёт все измерения
            cov = await attribution.refresh_coverage_gauges(conn)
            assert set(cov) == set(attribution.DIM_COLUMNS) - {"line_item"}
        finally:
            await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", a.ids["camp"])
            await a.teardown(conn)
            await conn.close()

    asyncio.run(scenario())
