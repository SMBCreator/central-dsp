"""Audit E-01 / R-02: единая семантика расхода и наследование измерений.

Договор (stats_math.spend_expr):
  spend = sum(clear_price по win) / 1000 + sum(clear_price по click).
  Оплаченное событие одно на бид: win для CPM, первый click для CPC (R-03).
  impression/viewable/video clear_price не несут (в _log_event принудительно
  NULL); у CPM-бида click его тоже не получает — click() передаёт clear_price
  только когда сам списал деньги.

Наследование (_event_dims): win/impression/click получают bundle/publisher_id/
tagid/geo_region/geo_city/domain от бида — иначе в группировках «по площадкам»
они попадали в «—».

Требует DATABASE_URL (pytest.skip иначе).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from routers.rtb import _event_dims, _log_event
from stats_math import spend_expr

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _Pool:
    """Минимальный адаптер asyncpg.Connection -> интерфейс pool.execute/fetch."""

    def __init__(self, conn: asyncpg.Connection):
        self._c = conn

    async def execute(self, *a):
        return await self._c.execute(*a)

    async def fetchrow(self, *a):
        return await self._c.fetchrow(*a)

    async def fetch(self, *a):
        return await self._c.fetch(*a)


BID_META = {
    "bid_id": None, "imp_id": "1",
    "campaign_id": None, "line_item_id": None, "creative_id": None, "supply_partner_id": None,
    "price": 120.0,
    "device_type": "mobile", "geo_country": "RU", "geo_region": "MOW", "geo_city": "Moscow",
    "ad_type": "banner",
    "domain": "Example.RU", "bundle": "com.example.app", "publisher_id": "pub-77", "tagid": "slot-1",
    "os": "Android", "browser": "Chrome",
    "is_test": 0,
}


def test_event_dims_normalises_go_bidmeta():
    dims = _event_dims(BID_META)
    assert dims["bundle"] == "com.example.app"
    assert dims["publisher_id"] == "pub-77"
    assert dims["tagid"] == "slot-1"
    assert dims["geo_region"] == "MOW"
    assert dims["geo_city"] == "Moscow"
    assert dims["domain"] == "example.ru"  # lower-case как в bid-строке
    # Пустая строка из Go-меты -> NULL, а не '' (иначе отдельная группа в отчёте).
    assert _event_dims({**BID_META, "tagid": ""})["tagid"] is None
    assert _event_dims(None)["bundle"] is None


def test_spend_expr_counts_only_paid_events():
    sql = spend_expr("e")
    assert "e.event_type = 'win'" in sql and "/ 1000.0" in sql
    assert "e.event_type = 'click'" in sql  # CPC: цена клика, без /1000
    assert "'impression'" not in sql


def test_win_chain_inherits_dims_and_spend_is_win_only():
    async def main():
        conn = await asyncpg.connect(DATABASE_URL)
        pool = _Pool(conn)
        bid_id = f"t-{uuid.uuid4().hex[:12]}"
        cid = uuid.uuid4()
        meta = {**BID_META, "bid_id": bid_id, "campaign_id": str(cid)}
        try:
            dims = _event_dims(meta)
            await _log_event(pool, "bid", bid_id=bid_id, price=120.0, **dims)
            await _log_event(pool, "win", bid_id=bid_id, price=120.0, clear_price=95.0, **dims)
            # impression приносит clear_price — по договору он должен обнулиться.
            await _log_event(pool, "impression", bid_id=bid_id, clear_price=95.0, **dims)
            # CPM-клик: click() не списывал -> clear_price не передаёт (None).
            await _log_event(pool, "click", bid_id=bid_id, clear_price=None, **dims)

            rows = await conn.fetch(
                "SELECT event_type, clear_price, bundle, publisher_id, tagid, geo_region, geo_city, domain "
                "FROM bid_events WHERE bid_id = $1 ORDER BY created_at, event_type",
                bid_id,
            )
            by_type = {r["event_type"]: r for r in rows}
            assert set(by_type) == {"bid", "win", "impression", "click"}
            for et in ("win", "impression", "click"):
                r = by_type[et]
                assert r["bundle"] == "com.example.app", et
                assert r["publisher_id"] == "pub-77", et
                assert r["tagid"] == "slot-1", et
                assert r["geo_region"] == "MOW", et
                assert r["geo_city"] == "Moscow", et
                assert r["domain"] == "example.ru", et
            assert float(by_type["win"]["clear_price"]) == 95.0
            assert by_type["impression"]["clear_price"] is None
            assert by_type["click"]["clear_price"] is None

            spend = await conn.fetchval(
                f"SELECT {spend_expr('e')} FROM bid_events e WHERE e.bid_id = $1", bid_id
            )
            # 95 CPM / 1000 = 0.095 — ровно один раз, а не x2/x3 с imp/click.
            assert abs(float(spend) - 0.095) < 1e-9

            # Группировка «по tagid» видит все четыре события в одной строке, без «—».
            grp = await conn.fetch(
                "SELECT tagid, count(*) AS n FROM bid_events WHERE bid_id = $1 GROUP BY tagid", bid_id
            )
            assert len(grp) == 1 and grp[0]["tagid"] == "slot-1" and grp[0]["n"] == 4
        finally:
            await conn.execute("DELETE FROM bid_events WHERE bid_id = $1", bid_id)
            await conn.close()

    _run(main())
