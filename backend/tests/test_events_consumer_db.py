"""Audit E-03 на живом Postgres: rtb_events_inbox делает persist_batch идемпотентным.

- повтор того же батча (crash между COMMIT и XACK) не вставляет строк и не
  двигает stats_hourly.bids;
- частично новый батч записывает только новые stream id;
- ошибка внутри транзакции откатывает и inbox — следующий повтор проходит целиком;
- курсор пишется атомарно с данными.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

import events_consumer as ec

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


def _fields(camp, bid_id, ts="1757000000"):
    return {
        "type": "bid", "bid_id": bid_id, "imp_id": "1",
        "campaign_id": str(camp), "line_item_id": "", "creative_id": "",
        "supply_partner_id": "", "price": "10", "device_type": "mobile",
        "geo_country": "RU", "ad_type": "banner", "domain": "x.ru", "is_test": "0", "ts": ts,
    }


async def _state(conn, camp, sids):
    rows = await conn.fetchval("SELECT count(*) FROM bid_events WHERE campaign_id = $1", camp)
    bids = await conn.fetchval("SELECT coalesce(sum(bids), 0) FROM stats_hourly WHERE campaign_id = $1", camp)
    inbox = await conn.fetchval("SELECT count(*) FROM rtb_events_inbox WHERE stream_id = ANY($1::text[])", sids)
    return int(rows), int(bids), int(inbox)


def test_inbox_makes_batch_idempotent():
    async def main():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
        camp = uuid.uuid4()
        tag = uuid.uuid4().hex[:8]
        sids = [f"{tag}-{i}" for i in range(4)]
        items = [(sids[i], ec.normalize(_fields(camp, f"b{i}"))) for i in range(3)]
        try:
            async with pool.acquire() as conn:
                saved_cursor = await conn.fetchval(
                    "SELECT last_id FROM rtb_events_cursor WHERE stream = $1", ec.STREAM
                )

            # 1) первый проход
            assert await ec.persist_batch(pool, items, last_id=sids[2]) == 3
            async with pool.acquire() as conn:
                assert await _state(conn, camp, sids) == (3, 3, 3)
                assert await conn.fetchval(
                    "SELECT last_id FROM rtb_events_cursor WHERE stream = $1", ec.STREAM
                ) == sids[2]

            # 2) полный повтор (потерянный XACK) — ничего нового
            assert await ec.persist_batch(pool, items, last_id=sids[2]) == 0
            async with pool.acquire() as conn:
                assert await _state(conn, camp, sids) == (3, 3, 3)

            # 3) частично новый батч — только новое
            items2 = items[1:] + [(sids[3], ec.normalize(_fields(camp, "b3")))]
            assert await ec.persist_batch(pool, items2, last_id=sids[3]) == 1
            async with pool.acquire() as conn:
                assert await _state(conn, camp, sids) == (4, 4, 4)

            # 4) ошибка после inbox-вставки откатывает и inbox
            bad_sid = f"{tag}-bad"
            bad_row = ec.normalize(_fields(camp, "bad"))
            bad_row["campaign_id"] = "not-a-uuid"      # INSERT в bid_events упадёт
            with pytest.raises(asyncpg.PostgresError):
                await ec.persist_batch(pool, [(bad_sid, bad_row)], last_id=bad_sid)
            async with pool.acquire() as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM rtb_events_inbox WHERE stream_id = $1", bad_sid
                ) == 0
                # курсор не сдвинулся на упавший батч
                assert await conn.fetchval(
                    "SELECT last_id FROM rtb_events_cursor WHERE stream = $1", ec.STREAM
                ) == sids[3]
            # починенный повтор проходит целиком
            bad_row["campaign_id"] = str(camp)
            assert await ec.persist_batch(pool, [(bad_sid, bad_row)], last_id=bad_sid) == 1
            async with pool.acquire() as conn:
                assert await _state(conn, camp, sids + [bad_sid]) == (5, 5, 5)
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM rtb_events_inbox WHERE stream_id LIKE $1", f"{tag}-%")
                await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", camp)
                await conn.execute("DELETE FROM stats_hourly WHERE campaign_id = $1", camp)
                if saved_cursor is None:
                    await conn.execute("DELETE FROM rtb_events_cursor WHERE stream = $1", ec.STREAM)
                else:
                    await conn.execute(
                        "UPDATE rtb_events_cursor SET last_id = $2 WHERE stream = $1", ec.STREAM, saved_cursor
                    )
            await pool.close()

    asyncio.run(main())
