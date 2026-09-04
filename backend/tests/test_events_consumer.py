"""Tests for events_consumer (Go bidder Redis Stream -> bid_events/stats_hourly).

Redis — fakeredis; Postgres — минимальный fake pool, который записывает SQL и
параметры. Проверяем: нормализацию полей стрима, агрегацию bids по часу,
at-least-once (XACK только после записи), пропуск невалидных событий.
Конвенция репо: asyncio.run, без pytest-asyncio.
"""
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import fakeredis.aioredis
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import events_consumer as ec  # noqa: E402


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Conn:
    def __init__(self, log):
        self.log = log

    def transaction(self):
        return _Tx()

    async def executemany(self, sql, args):
        self.log.append((sql.strip().split("\n")[0], list(args)))


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *a):
        return False


class FakePool:
    def __init__(self):
        self.log = []
        self._conn = _Conn(self.log)

    def acquire(self):
        return _Acquire(self._conn)

    async def fetchval(self, *a, **k):
        return None

    async def execute(self, *a, **k):
        return None


def _bid_fields(**over):
    base = {
        "type": "bid", "bid_id": "b1", "imp_id": "1",
        "campaign_id": "11111111-1111-1111-1111-111111111111",
        "line_item_id": "", "creative_id": "22222222-2222-2222-2222-222222222222",
        "supply_partner_id": "33333333-3333-3333-3333-333333333333",
        "price": "45.5000", "bidfloor": "30.0000", "device_type": "mobile",
        "geo_country": "RU", "geo_region": "MOW", "geo_city": "Moscow",
        "ad_type": "banner", "domain": "Example.COM", "bundle": "", "publisher_id": "pub-7",
        "tagid": "slot-1", "os": "android", "browser": "chrome", "is_test": "0",
        "ts": "1757000000",
    }
    base.update(over)
    return base


def test_normalize_bid_maps_types():
    row = ec.normalize(_bid_fields())
    assert row["event_type"] == "bid"
    assert row["price"] == Decimal("45.5000")
    assert row["bidfloor"] == Decimal("30.0000")
    assert row["domain"] == "example.com"           # lower + strip
    assert row["line_item_id"] is None              # "" -> NULL for UUID column
    assert row["bundle"] is None
    assert row["tagid"] == "slot-1"
    assert row["is_test"] is False
    assert row["created_at"] == datetime.fromtimestamp(1757000000, tz=timezone.utc)


def test_normalize_loss_keeps_reason_and_mtw():
    row = ec.normalize(_bid_fields(type="loss", loss_reason="102", min_to_win="52.1"))
    assert row["event_type"] == "loss"
    assert row["loss_reason"] == "102"
    assert row["min_to_win"] == Decimal("52.1")


def test_normalize_rejects_unknown_or_empty():
    assert ec.normalize({"type": "win", "bid_id": "x"}) is None
    assert ec.normalize(_bid_fields(bid_id="")) is None
    assert ec.normalize({}) is None


def test_aggregate_bids_by_hour_and_key_skips_test_and_loss():
    rows = [
        ec.normalize(_bid_fields(bid_id="a", ts="1757000000")),
        ec.normalize(_bid_fields(bid_id="b", ts="1757000100")),       # same hour
        ec.normalize(_bid_fields(bid_id="c", ts="1757003700")),       # next hour
        ec.normalize(_bid_fields(bid_id="d", is_test="1")),           # test: excluded
        ec.normalize(_bid_fields(bid_id="e", type="loss")),           # loss: excluded
    ]
    agg = ec.aggregate_bids(rows)
    counts = sorted(n for *_, n in agg)
    assert counts == [1, 2]
    hours = {k[0] for k in agg}
    assert all(h.minute == 0 and h.second == 0 for h in hours)


def test_process_once_persists_then_acks():
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        pool = FakePool()
        await r.xadd(ec.STREAM, _bid_fields(bid_id="a"))
        await r.xadd(ec.STREAM, _bid_fields(bid_id="b", type="loss", loss_reason="1"))
        await r.xadd(ec.STREAM, {"type": "garbage"})   # invalid -> skipped but acked
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)

        n, last = await ec.process_once(r, pool, from_id=">")
        assert n == 3 and last is not None

        # bid_events insert got 2 valid rows; stats_hourly bump got 1 key (only the bid)
        inserts = [args for sql, args in pool.log if "INSERT INTO bid_events" in sql]
        bumps = [args for sql, args in pool.log if "INSERT INTO stats_hourly" in sql]
        assert len(inserts) == 1 and len(inserts[0]) == 2
        assert len(bumps) == 1 and len(bumps[0]) == 1 and bumps[0][0][-1] == 1

        # everything acked -> no pending
        pending = await r.xpending(ec.STREAM, ec.GROUP)
        assert pending["pending"] == 0

        # nothing more to read
        n2, _ = await ec.process_once(r, pool, from_id=">")
        assert n2 == 0

    asyncio.run(run())


def test_process_once_does_not_ack_on_db_failure():
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)

        class BrokenPool(FakePool):
            def acquire(self):
                raise RuntimeError("pg down")

        await r.xadd(ec.STREAM, _bid_fields(bid_id="a"))
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)
        with pytest.raises(RuntimeError):
            await ec.process_once(r, BrokenPool(), from_id=">")
        pending = await r.xpending(ec.STREAM, ec.GROUP)
        assert pending["pending"] == 1   # остаётся в PEL -> будет перечитано с id "0"

    asyncio.run(run())
