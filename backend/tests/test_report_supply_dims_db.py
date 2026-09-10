"""Отчёт по инвентарю SSP: разрезы tagid / publisher / bundle / loss_reason и
метрики losses / avg_bidfloor / avg_min_to_win из bid_events (требует DATABASE_URL).

Эти разрезы — единственный способ вести переговоры с SSP предметно:
«на плейсменте X мы проигрываем по цене (102) со средним min_to_win 41 ₽ при
нашей ставке 30 ₽». Тест фиксирует, что цифры считаются из тех полей, которые
Go пишет в rtb:events (см. docs/CONTRACTS.md), тестовый трафик исключён, а
чужие кампании не видны.
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

import db  # noqa: E402
from routers.stats import LOSS_REASON_LABELS  # noqa: E402
from tests.test_idor_isolation_db import _app_as, _Tenant  # noqa: E402
from stats_math import NOT_SET_LABEL


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("LAUNCH_GATE", "off")
    db._pool = None
    yield
    db._pool = None


async def _insert_event(conn, camp, li, cr, *, etype, price=None, bidfloor=None,
                        tagid=None, publisher=None, bundle=None, loss_reason=None,
                        min_to_win=None, is_test=False):
    await conn.execute(
        """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, creative_id, price, bidfloor,
                                   tagid, publisher_id, bundle, loss_reason, min_to_win, is_test, created_at)
           VALUES ($1, gen_random_uuid()::text, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now())""",
        etype, camp, li, cr, price, bidfloor, tagid, publisher, bundle, loss_reason, min_to_win, is_test,
    )


def test_report_by_tagid_and_loss_reason(env):
    async def scenario():
        conn = await asyncpg.connect(DATABASE_URL)
        a = await _Tenant("supA").setup(conn)
        b = await _Tenant("supB").setup(conn)
        try:
            camp, li, cr = a.ids["camp"], a.ids["li"], a.ids["cr"]
            # плейсмент p1: 3 бида по 30 ₽ при floor 20, два проигрыша по цене (102) с min_to_win 40/42
            for _ in range(3):
                await _insert_event(conn, camp, li, cr, etype="bid", price=30, bidfloor=20,
                                    tagid="p1", publisher="pub-1", bundle="com.app.one")
            for mtw in (40, 42):
                await _insert_event(conn, camp, li, cr, etype="loss", tagid="p1", publisher="pub-1",
                                    bundle="com.app.one", loss_reason="102", min_to_win=mtw)
            # плейсмент p2: 1 бид, проигрыш ниже floor (100)
            await _insert_event(conn, camp, li, cr, etype="bid", price=10, bidfloor=15, tagid="p2", publisher="pub-1")
            await _insert_event(conn, camp, li, cr, etype="loss", tagid="p2", publisher="pub-1", loss_reason="100")
            # тестовый трафик — не должен попасть в отчёт
            await _insert_event(conn, camp, li, cr, etype="bid", price=999, bidfloor=1, tagid="p1", is_test=True)
            # чужой тенант на том же tagid — не должен быть виден A
            await _insert_event(conn, b.ids["camp"], b.ids["li"], b.ids["cr"], etype="bid", price=500, tagid="p1")

            client = _app_as(a.as_user())
            today = (await conn.fetchval("SELECT current_date")).isoformat()
            body = {"date_from": today, "date_to": today, "groupings": ["tagid"], "campaign_ids": [camp]}
            r = await client.post("/stats/report", json=body)
            assert r.status_code == 200, r.text
            rows = {row["tagid"]: row for row in r.json()["rows"]}
            assert set(rows) == {"p1", "p2"}

            p1 = rows["p1"]
            assert p1["bids"] == 3 and p1["losses"] == 2
            assert p1["avg_bid"] == 30.0 and p1["avg_bidfloor"] == 20.0
            assert p1["avg_min_to_win"] == 41.0
            assert p1["loss_rate"] == round(2 / 3 * 100, 2)
            assert rows["p2"]["losses"] == 1 and rows["p2"]["avg_min_to_win"] is None

            totals = r.json()["totals"]
            assert totals["bids"] == 4 and totals["losses"] == 3

            # loss_reason → человекочитаемая подпись + исходный код в *_id
            r2 = await client.post("/stats/report", json={**body, "groupings": ["loss_reason"]})
            lr = {row["loss_reason_id"]: row for row in r2.json()["rows"] if row.get("loss_reason_id")}
            assert lr["102"]["loss_reason"] == LOSS_REASON_LABELS["102"] and lr["102"]["losses"] == 2
            assert lr["100"]["loss_reason"] == LOSS_REASON_LABELS["100"] and lr["100"]["losses"] == 1

            # publisher × bundle — два измерения за один запрос
            r3 = await client.post("/stats/report", json={**body, "groupings": ["publisher", "bundle"]})
            combos = {(row["publisher"], row["bundle"]) for row in r3.json()["rows"]}
            assert ("pub-1", "com.app.one") in combos and ("pub-1", NOT_SET_LABEL) in combos  # F-02: NULL → «Не указано»

            # источник stats_hourly (группировка day) не знает supply-метрик → null, а не 0
            r4 = await client.post("/stats/report", json={**body, "groupings": ["day"]})
            day = r4.json()["rows"][0]
            # hourly-источник проигрыши не измеряет → None, не ноль (F-02)
            assert day["losses"] is None and day["avg_bidfloor"] is None and day["avg_min_to_win"] is None

            # чужой тенант не видит наши плейсменты
            rb = await _app_as(b.as_user()).post("/stats/report", json={**body, "campaign_ids": [camp]})
            assert rb.json()["rows"] == []
        finally:
            await conn.execute("DELETE FROM bid_events WHERE campaign_id = ANY($1::uuid[])", [a.ids["camp"], b.ids["camp"]])
            await a.teardown(conn)
            await b.teardown(conn)
            await conn.close()

    asyncio.run(scenario())
