"""Audit E-02: идемпотентность трекинг-событий через bid_event_once в одной
транзакции с bid_events и stats_hourly.

Гарантии:
- повтор пикселя (imp/click/view) не задваивает ни события, ни stats_hourly;
- если запись события упала, заявка откатывается и следующий вызов
  проходит целиком (Redis-ключ не ставится до коммита);
- параллельные вызовы одного пикселя дают ровно одну запись.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from routers import rtb as rtb_module

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")


class _FailingRedis:
    """Redis недоступен: fast-path должен молча уступить БД."""

    async def exists(self, *_):
        raise ConnectionError("redis down")

    async def set(self, *_, **__):
        raise ConnectionError("redis down")


def _row(camp, li, cr, sp):
    return {
        "imp_id": "1", "campaign_id": camp, "line_item_id": li, "creative_id": cr,
        "supply_partner_id": sp, "device_type": "desktop", "geo_country": "RU",
        "ad_type": "banner", "domain": "site.ru", "os": "linux", "browser": "chrome",
        "bundle": None, "publisher_id": None, "tagid": None, "geo_region": None, "geo_city": None,
        "price": 100.0, "is_test": False,
    }


# bid_events / stats_hourly / domain_stats_hourly / bid_event_once не имеют FK —
# достаточно случайных UUID, сущности кампании не нужны.
async def _cleanup(conn, camp, bids):
    await conn.execute("DELETE FROM bid_event_once WHERE bid_id = ANY($1::text[])", bids)
    await conn.execute("DELETE FROM bid_events WHERE campaign_id = $1", camp)
    await conn.execute("DELETE FROM stats_hourly WHERE campaign_id = $1", camp)
    await conn.execute("DELETE FROM domain_stats_hourly WHERE campaign_id = $1", camp)


async def _counts(conn, camp, bid):
    ev = await conn.fetchval(
        "SELECT count(*) FROM bid_events WHERE campaign_id = $1 AND bid_id = $2 AND event_type = 'impression'",
        camp, bid,
    )
    imps = await conn.fetchval("SELECT coalesce(sum(impressions), 0) FROM stats_hourly WHERE campaign_id = $1", camp)
    once = await conn.fetchval("SELECT count(*) FROM bid_event_once WHERE bid_id = $1 AND kind = 'imp'", bid)
    return int(ev), int(imps), int(once)


def test_impression_repeat_and_rollback(monkeypatch):
    async def main():
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
        bid = f"once-{uuid.uuid4().hex}"
        bid2 = f"once-{uuid.uuid4().hex}"
        camp, li, cr, sp = (uuid.uuid4() for _ in range(4))
        row = _row(camp, li, cr, sp)

        async def fake_find_bid(_pool, _bid):
            return row

        async def fake_get_pool():
            return pool

        monkeypatch.setattr(rtb_module, "_find_bid", fake_find_bid)
        monkeypatch.setattr(rtb_module, "get_pool", fake_get_pool)
        monkeypatch.setattr(rtb_module, "get_redis", lambda: _FailingRedis())
        monkeypatch.setattr(rtb_module, "verify_event_token", lambda t: t)

        try:
            # 1) Первый вызов падает внутри транзакции (после заявки) -> всё откатилось.
            real_bump = rtb_module._bump_stats
            calls = {"n": 0}

            async def flaky_bump(db, *a, **kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("simulated stats failure")
                return await real_bump(db, *a, **kw)

            monkeypatch.setattr(rtb_module, "_bump_stats", flaky_bump)
            with pytest.raises(RuntimeError):
                await rtb_module.impression(bid)
            async with pool.acquire() as conn:
                assert await _counts(conn, camp, bid) == (0, 0, 0), "падение должно откатить и заявку"

            # 2) Повтор проходит целиком.
            await rtb_module.impression(bid)
            async with pool.acquire() as conn:
                assert await _counts(conn, camp, bid) == (1, 1, 1)

            # 3) Дубль пикселя — ноль новых записей.
            await rtb_module.impression(bid)
            await rtb_module.impression(bid)
            async with pool.acquire() as conn:
                assert await _counts(conn, camp, bid) == (1, 1, 1)

            # 4) Параллельный шторм по другому биду — ровно одна запись.
            await asyncio.gather(*(rtb_module.impression(bid2) for _ in range(8)))
            async with pool.acquire() as conn:
                ev2, imps_total, once2 = await _counts(conn, camp, bid2)
                assert (ev2, once2) == (1, 1)
                assert imps_total == 2, "stats_hourly: по одному показу на каждый бид"
        finally:
            async with pool.acquire() as conn:
                await _cleanup(conn, camp, [bid, bid2])
            await pool.close()

    asyncio.run(main())
