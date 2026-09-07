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
    """Минимальный asyncpg-conn: журнал SQL + эмуляция inbox (ON CONFLICT DO NOTHING
    RETURNING) поверх множества seen, чтобы дедуп проверялся без Postgres."""

    def __init__(self, log, seen: set, fail_after_inbox: bool = False):
        self.log = log
        self.seen = seen
        self.fail_after_inbox = fail_after_inbox
        self.cursor = None

    def transaction(self):
        return _Tx()

    async def fetch(self, sql, *args):
        assert "rtb_events_inbox" in sql
        fresh = [sid for sid in args[0] if sid not in self.seen]
        self.seen.update(fresh)
        self.log.append(("INSERT INTO rtb_events_inbox", fresh))
        if self.fail_after_inbox:
            self.seen.difference_update(fresh)   # транзакция откатилась
            raise RuntimeError("pg failed mid-transaction")
        return [{"stream_id": s} for s in fresh]

    async def execute(self, sql, *args):
        self.log.append((sql.strip().split("\n")[0], list(args)))
        if "rtb_events_cursor" in sql:
            self.cursor = args[1]

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
    def __init__(self, fail_after_inbox: bool = False):
        self.log = []
        self.seen: set[str] = set()
        self._conn = _Conn(self.log, self.seen, fail_after_inbox)

    def acquire(self):
        return _Acquire(self._conn)

    async def fetchval(self, *a, **k):
        return self._conn.cursor

    async def execute(self, *a, **k):
        return None

    # --- helpers для assert-ов ---
    def inserted_rows(self):
        return [r for sql, args in self.log if "INSERT INTO bid_events" in sql for r in args]

    def bids_bumped(self):
        return sum(b[-1] for sql, args in self.log if "INSERT INTO stats_hourly" in sql for b in args)


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


def test_normalize_pctr_decision_log():
    # Go-биддер шлёт pctr только когда модель повлияла на цену (R-03, Часть B);
    # отсутствие/мусор/значение вне (0,1) -> NULL, а не падение батча.
    assert ec.normalize(_bid_fields())["pctr"] is None
    assert ec.normalize(_bid_fields(pctr="0.036032"))["pctr"] == 0.036032
    assert ec.normalize(_bid_fields(pctr=""))["pctr"] is None
    assert ec.normalize(_bid_fields(pctr="nan?"))["pctr"] is None
    assert ec.normalize(_bid_fields(pctr="1.5"))["pctr"] is None
    assert "pctr" in ec._INSERT_COLS and ec._INSERT_SQL.count("$") == len(ec._INSERT_COLS)


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
        assert len(pool.inserted_rows()) == 2
        assert pool.bids_bumped() == 1
        # курсор записан в той же транзакции, что и данные
        assert pool._conn.cursor == last

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


# ---------------------------------------------------------------------------
# Audit E-03: сценарии из отчёта (PG down->up, смена consumer, replay после
# потери ACK, poison event, bootstrap с «0»).
# ---------------------------------------------------------------------------

def test_e03_pg_down_then_up_replays_pending_before_new():
    """PG down при «>» -> событие в PEL; после восстановления чтение «0»
    записывает его, и только потом «>» отдаёт новые. Раньше: new_read=0, pending=1."""
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)
        await r.xadd(ec.STREAM, _bid_fields(bid_id="a"))

        class BrokenPool(FakePool):
            def acquire(self):
                raise RuntimeError("pg down")

        with pytest.raises(RuntimeError):
            await ec.process_once(r, BrokenPool(), from_id=">")
        await r.xadd(ec.STREAM, _bid_fields(bid_id="b"))   # новое пришло, пока PG лежал

        pool = FakePool()
        n_pending, _ = await ec.process_once(r, pool, from_id="0")   # как run_forever после ошибки
        assert n_pending == 1
        n_new, _ = await ec.process_once(r, pool, from_id=">")
        assert n_new == 1
        assert sorted(row[1] for row in pool.inserted_rows()) == ["a", "b"]
        assert pool.bids_bumped() == 2
        assert (await r.xpending(ec.STREAM, ec.GROUP))["pending"] == 0

    asyncio.run(run())


def test_e03_replay_after_lost_ack_does_not_double_count():
    """Crash после COMMIT до XACK: батч перечитывается, inbox отбрасывает
    уже вставленные stream id -> bids не задваиваются. Раньше: counted_bids=2."""
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)
        await r.xadd(ec.STREAM, _bid_fields(bid_id="a"))
        pool = FakePool()

        class NoAckRedis:
            """Redis, у которого XACK «не дошёл» (процесс умер после COMMIT)."""
            def __init__(self, inner):
                self.inner = inner

            def __getattr__(self, name):
                return getattr(self.inner, name)

            async def xack(self, *a, **k):
                raise ConnectionError("died before ack")

        with pytest.raises(ConnectionError):
            await ec.process_once(NoAckRedis(r), pool, from_id=">")
        assert pool.bids_bumped() == 1          # COMMIT прошёл
        assert (await r.xpending(ec.STREAM, ec.GROUP))["pending"] == 1

        # Рестарт: перечитываем pending «0».
        n, _ = await ec.process_once(r, pool, from_id="0")
        assert n == 1                            # событие обработано и подтверждено
        assert len(pool.inserted_rows()) == 1    # но НЕ вставлено второй раз
        assert pool.bids_bumped() == 1           # и bids не задвоились
        assert (await r.xpending(ec.STREAM, ec.GROUP))["pending"] == 0

    asyncio.run(run())


def test_e03_new_hostname_claims_stale_pending(monkeypatch):
    """Контейнер перезапущен с другим HOSTNAME: pending старого consumer-а
    забирается через XAUTOCLAIM. Раньше: pending_read=0 навсегда."""
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)
        await r.xadd(ec.STREAM, _bid_fields(bid_id="a"))
        await r.xadd(ec.STREAM, _bid_fields(bid_id="b"))

        # Старый consumer прочитал и умер, не подтвердив.
        monkeypatch.setattr(ec, "CONSUMER", "backend-old")
        class BrokenPool(FakePool):
            def acquire(self):
                raise RuntimeError("pg down")
        with pytest.raises(RuntimeError):
            await ec.process_once(r, BrokenPool(), from_id=">")

        # Новый consumer: свой pending пуст, «>» пуст — без claim события зависли бы.
        monkeypatch.setattr(ec, "CONSUMER", "backend-new")
        monkeypatch.setattr(ec, "CLAIM_IDLE_MS", 0)
        pool = FakePool()
        assert (await ec.process_once(r, pool, from_id="0"))[0] == 0
        assert (await ec.process_once(r, pool, from_id=">"))[0] == 0

        claimed = await ec.claim_stale(r, pool)
        assert claimed == 2
        assert sorted(row[1] for row in pool.inserted_rows()) == ["a", "b"]
        assert (await r.xpending(ec.STREAM, ec.GROUP))["pending"] == 0

    asyncio.run(run())


def test_e03_poison_event_quarantined_after_max_deliveries(monkeypatch):
    """Событие, на котором запись падает MAX_DELIVERIES раз, уходит в dead-stream
    и подтверждается; поток не блокируется."""
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)
        poison_id = await r.xadd(ec.STREAM, _bid_fields(bid_id="poison"))
        monkeypatch.setattr(ec, "MAX_DELIVERIES", 3)

        broken = FakePool(fail_after_inbox=True)
        # доставка 1 через «>», затем 2 перечитывания «0» с ошибкой -> times_delivered=3
        with pytest.raises(RuntimeError):
            await ec.process_once(r, broken, from_id=">")
        with pytest.raises(RuntimeError):
            await ec.process_once(r, broken, from_id="0")
        # Третье чтение: доставок уже >= MAX -> карантин ВМЕСТО попытки записи.
        n, _ = await ec.process_once(r, broken, from_id="0")
        assert n == 0
        assert (await r.xpending(ec.STREAM, ec.GROUP))["pending"] == 0
        dead = await r.xrange(ec.DEAD_STREAM)
        assert len(dead) == 1 and dead[0][1]["_src_id"] == poison_id
        assert dead[0][1]["bid_id"] == "poison"

        # Здоровое событие после этого идёт как обычно.
        await r.xadd(ec.STREAM, _bid_fields(bid_id="ok"))
        good = FakePool()
        assert (await ec.process_once(r, good, from_id=">"))[0] == 1
        assert [row[1] for row in good.inserted_rows()] == ["ok"]

    asyncio.run(run())


def test_e03_bootstrap_reads_existing_stream_not_only_new():
    """Первая группа стартует с «0»: то, что Go уже сложил в стрим до старта
    backend-а, не теряется. Раньше старт был с «$»."""
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.xadd(ec.STREAM, _bid_fields(bid_id="before-start"))
        pool = FakePool()
        await ec._ensure_group(r, pool)
        await ec._ensure_group(r, pool)            # BUSYGROUP -> идемпотентно
        n, _ = await ec.process_once(r, pool, from_id=">")
        assert n == 1 and [row[1] for row in pool.inserted_rows()] == ["before-start"]

    asyncio.run(run())


def test_e03_group_recreated_from_db_cursor():
    """Redis пересоздан (группа потеряна), но курсор в БД есть: группа стартует
    с него и не перечитывает то, что уже в БД."""
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        id1 = await r.xadd(ec.STREAM, _bid_fields(bid_id="old"))
        await r.xadd(ec.STREAM, _bid_fields(bid_id="new"))
        pool = FakePool()
        pool._conn.cursor = id1                    # «БД помнит, что old записан»
        await ec._ensure_group(r, pool)
        n, _ = await ec.process_once(r, pool, from_id=">")
        assert n == 1 and [row[1] for row in pool.inserted_rows()] == ["new"]

    asyncio.run(run())


def test_e03_refresh_gauges_sets_length_pending_lag():
    async def run():
        r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        await r.xgroup_create(ec.STREAM, ec.GROUP, id="0", mkstream=True)
        for i in range(3):
            await r.xadd(ec.STREAM, _bid_fields(bid_id=str(i)))
        await r.xreadgroup(ec.GROUP, ec.CONSUMER, {ec.STREAM: ">"}, count=1)

        seen = {}
        class Obs:
            def set_gauge(self, name, value):
                seen[name] = value
        await ec.refresh_gauges(r, Obs())
        assert seen["rtb_events_stream_length"] == 3
        assert seen["rtb_events_pending"] == 1
        assert seen["rtb_events_lag"] == 2

    asyncio.run(run())
