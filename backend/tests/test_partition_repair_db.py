"""C5 / O-06: восстановление партиций bid_events на живом PostgreSQL.

Требует DATABASE_URL (skip иначе). Проверяет доказательство из плана:
10k строк в DEFAULT -> repair -> DEFAULT пуст, count совпал; плюс backfill из
legacy-таблицы и возобновление прерванного staged-repair. Тесты синхронные:
scripts/partition_repair.main() сам поднимает event loop.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set")

PREFIX = "c5test"
TODAY = dt.date.today()
PAST_DAY = TODAY - dt.timedelta(days=30)       # прошлый день внутри retention (90)
PAST_DAY2 = TODAY - dt.timedelta(days=31)
LEGACY_DAY = TODAY - dt.timedelta(days=20)
LIVE_DAY = TODAY + dt.timedelta(days=200)      # "живой" день за горизонтом партиций
EXPIRED_DAY = TODAY - dt.timedelta(days=400)   # старше retention


def _run(coro):
    return asyncio.run(coro)


async def _exec(sql: str, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _val(sql: str, *args):
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def _ts(day: dt.date, hours: float = 0.0) -> dt.datetime:
    return dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hours)


def _pname(day: dt.date) -> str:
    return f"bid_events_{day:%Y%m%d}"


async def _cleanup():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for day in (PAST_DAY, PAST_DAY2, LEGACY_DAY, LIVE_DAY, EXPIRED_DAY):
            await conn.execute(f'DROP TABLE IF EXISTS "{_pname(day)}"')
        await conn.execute("DELETE FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")
        await conn.execute("DELETE FROM bid_events WHERE bid_id LIKE $1", PREFIX + "%")
        await conn.execute("DELETE FROM bid_events_legacy WHERE bid_id LIKE $1", PREFIX + "%")
    finally:
        await conn.close()


@pytest.fixture(autouse=True)
def _isolated_schema():
    _run(_cleanup())
    yield
    _run(_cleanup())


async def _insert_default_rows(day: dt.date, n: int, tag: str) -> None:
    """n строк за день без партиции -> роутинг в DEFAULT."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        assert not await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", _pname(day)), "partition must not exist"
        await conn.execute(
            "INSERT INTO bid_events (event_type, bid_id, imp_id, price, created_at) "
            "SELECT 'bid', $1 || '-' || g, 'imp' || g, 1.5, $2::timestamptz + (g * interval '1 second') "
            "FROM generate_series(1, $3) g",
            f"{PREFIX}-{tag}", _ts(day), n,
        )
        got = await conn.fetchval(
            "SELECT count(*) FROM bid_events_default WHERE created_at >= $1 AND created_at < $2", _ts(day), _ts(day, 24)
        )
        assert got == n
    finally:
        await conn.close()


def _script(*argv: str) -> int:
    from scripts import partition_repair

    return partition_repair.main(list(argv))


# --------------------------------------------------------------------------- миграция 057


def test_ensure_partitions_moves_default_rows_atomically():
    _run(_insert_default_rows(LIVE_DAY, 100, "ensure"))
    created = _run(_val("SELECT bid_events_ensure_partitions($1, $1)", LIVE_DAY))
    assert created == 1
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 0
    assert _run(_val(f'SELECT count(*) FROM "{_pname(LIVE_DAY)}"')) == 100
    # индексы родителя (в т.ч. PK) созданы на новой партиции
    n_parent_idx = _run(_val("SELECT count(*) FROM pg_indexes WHERE tablename = 'bid_events'"))
    n_part_idx = _run(_val("SELECT count(*) FROM pg_indexes WHERE tablename = $1", _pname(LIVE_DAY)))
    assert n_part_idx == n_parent_idx
    # вставка через родителя после ATTACH: identity родителя генерирует id, роутинг в новую партицию
    row = _run(_val(
        "INSERT INTO bid_events (event_type, bid_id, created_at) VALUES ('win', $1, $2) RETURNING id",
        f"{PREFIX}-after", _ts(LIVE_DAY, 5),
    ))
    assert row is not None
    assert _run(_val(f'SELECT count(*) FROM "{_pname(LIVE_DAY)}"')) == 101


def test_partition_horizon_days_reflects_last_partition():
    horizon_before = _run(_val("SELECT bid_events_partition_horizon_days()"))
    _run(_val("SELECT bid_events_ensure_partitions($1, $1)", LIVE_DAY))
    assert _run(_val("SELECT bid_events_partition_horizon_days()")) == 200
    assert horizon_before is None or horizon_before < 200


def test_cron_retention_sql_runs_on_live_pg():
    """Строки SQL из /cron/retention выполняются на живом PG с той же привязкой
    параметров, что и в обработчике (мок-тест в test_obs_gauges_cron этого не
    проверяет; без `$1::int` PG отвечает "operator is not unique: date + unknown")."""
    from routers import cron

    async def run():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            created = await conn.fetchval(cron.RETENTION_ENSURE_PARTITIONS_SQL, 7)
            horizon = await conn.fetchval(cron.RETENTION_PARTITION_HORIZON_SQL)
            in_default = await conn.fetchval(cron.RETENTION_DEFAULT_ROWS_SQL)
            return created, horizon, in_default
        finally:
            await conn.close()

    created, horizon, in_default = _run(run())
    assert isinstance(created, int) and created >= 0
    assert horizon is not None and horizon >= 7
    assert isinstance(in_default, int)


def test_ensure_partitions_waits_for_in_flight_insert_into_default():
    """Гонка (057): сессия A держит незакоммиченную вставку за день D в DEFAULT,
    сессия B создаёт партицию D. B обязана дождаться A (LOCK на DEFAULT) и забрать
    обе строки — иначе ATTACH упал бы на проверке DEFAULT."""

    async def run():
        a = await asyncpg.connect(DATABASE_URL)
        b = await asyncpg.connect(DATABASE_URL)
        try:
            tx = a.transaction()
            await tx.start()
            await a.execute(
                "INSERT INTO bid_events (event_type, bid_id, created_at) VALUES ('bid', $1, $2)",
                f"{PREFIX}-race-a", _ts(LIVE_DAY, 1),
            )
            ensure = asyncio.create_task(b.fetchval("SELECT bid_events_ensure_partitions($1, $1)", LIVE_DAY))
            await asyncio.sleep(0.5)
            assert not ensure.done(), "ensure must block while A holds an uncommitted row in DEFAULT"
            await a.execute(
                "INSERT INTO bid_events (event_type, bid_id, created_at) VALUES ('bid', $1, $2)",
                f"{PREFIX}-race-a2", _ts(LIVE_DAY, 2),
            )
            await tx.commit()
            created = await asyncio.wait_for(ensure, timeout=10)
            in_part = await b.fetchval(f'SELECT count(*) FROM "{_pname(LIVE_DAY)}"')
            in_default = await b.fetchval("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")
            return created, in_part, in_default
        finally:
            await a.close()
            await b.close()

    created, in_part, in_default = _run(run())
    assert created == 1
    assert in_part == 2
    assert in_default == 0


# --------------------------------------------------------------------------- repair-default


def test_repair_default_refuses_when_partition_bounds_are_not_utc_midnight():
    """Границы партиций создаёт backend в своей session TimeZone; скрипт работает в
    UTC. При расхождении repair-default обязан остановиться, status — предупредить."""
    misaligned_day = TODAY + dt.timedelta(days=300)
    name = _pname(misaligned_day)
    lo = _ts(misaligned_day, 3)  # 03:00 UTC = полночь UTC+3
    _run(_exec(
        f'CREATE TABLE "{name}" PARTITION OF bid_events FOR VALUES FROM ($1::text::timestamptz) TO ($2::text::timestamptz)'
        .replace("$1::text::timestamptz", f"'{lo.isoformat()}'").replace("$2::text::timestamptz", f"'{(lo + dt.timedelta(days=1)).isoformat()}'")
    ))
    try:
        _run(_insert_default_rows(PAST_DAY, 3, "tz"))
        assert _script("status") == 2
        assert _script("repair-default") == 1
        assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 3
    finally:
        _run(_exec(f'DROP TABLE IF EXISTS "{name}"'))
    assert _script("repair-default") == 0




def test_repair_default_10k_rows_default_empty_and_counts_match():
    _run(_insert_default_rows(PAST_DAY, 6000, "past"))
    _run(_insert_default_rows(LIVE_DAY, 4000, "live"))
    _run(_insert_default_rows(EXPIRED_DAY, 50, "expired"))
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 10050
    total_before = _run(_val("SELECT count(*) FROM bid_events WHERE bid_id LIKE $1", PREFIX + "%"))

    # status видит проблему
    assert _script("status") == 2
    # dry-run ничего не двигает
    assert _script("repair-default", "--dry-run") == 2
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 10050

    # батчи по 1000: прошлый день — staged путь (6 батчей + ATTACH), живой — атомарный ensure
    rc = _script("repair-default", "--batch", "1000")
    assert rc == 2  # expired-строки оставлены (без --purge-expired)
    assert _run(_val(f'SELECT count(*) FROM "{_pname(PAST_DAY)}"')) == 6000
    assert _run(_val(f'SELECT count(*) FROM "{_pname(LIVE_DAY)}"')) == 4000
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 50
    # партиции подключены к родителю
    for day in (PAST_DAY, LIVE_DAY):
        assert _run(_val(
            "SELECT EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid WHERE c.relname = $1)",
            _pname(day),
        ))
    # ни одна строка не потеряна и не задублирована
    assert _run(_val("SELECT count(*) FROM bid_events WHERE bid_id LIKE $1", PREFIX + "%")) == total_before
    # партиция для expired-дня не создавалась
    assert not _run(_val("SELECT to_regclass($1) IS NOT NULL", _pname(EXPIRED_DAY)))

    # явное удаление просроченных
    assert _script("repair-default", "--purge-expired") == 0
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 0
    assert _run(_val("SELECT count(*) FROM bid_events WHERE bid_id LIKE $1", PREFIX + "%")) == total_before - 50
    assert _script("status") == 0


def test_repair_default_resumes_interrupted_staged_run():
    """Скрипт умер после создания standalone-таблицы и части батчей: таблица
    существует, не подключена, часть строк в ней, часть в DEFAULT."""
    _run(_insert_default_rows(PAST_DAY2, 15, "resume"))
    _run(_exec(f'CREATE TABLE "{_pname(PAST_DAY2)}" (LIKE bid_events INCLUDING DEFAULTS INCLUDING CONSTRAINTS)'))
    _run(_exec(
        f'WITH m AS (DELETE FROM bid_events_default WHERE bid_id LIKE $1 AND created_at < $2 RETURNING *) '
        f'INSERT INTO "{_pname(PAST_DAY2)}" SELECT * FROM m',
        f"{PREFIX}-resume%", _ts(PAST_DAY2, 0) + dt.timedelta(seconds=10),
    ))
    assert _run(_val(f'SELECT count(*) FROM "{_pname(PAST_DAY2)}"')) == 9
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 6
    assert _script("status") == 2  # staged table reported

    assert _script("repair-default") == 0
    assert _run(_val(
        "SELECT EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid WHERE c.relname = $1)",
        _pname(PAST_DAY2),
    ))
    assert _run(_val(
        "SELECT count(*) FROM bid_events WHERE created_at >= $1 AND created_at < $2", _ts(PAST_DAY2), _ts(PAST_DAY2, 24)
    )) == 15
    assert _run(_val("SELECT count(*) FROM bid_events_default WHERE bid_id LIKE $1", PREFIX + "%")) == 0


# --------------------------------------------------------------------------- legacy


def test_backfill_legacy_copies_only_missing_rows_inside_retention():
    async def seed():
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            # 300 строк в legacy за день внутри окна, 100 из них уже есть в bid_events
            await conn.execute(
                "INSERT INTO bid_events_legacy (event_type, bid_id, imp_id, price, created_at) "
                "SELECT 'bid', $1 || '-' || g, 'imp' || g, 2.0, $2::timestamptz + (g * interval '1 second') FROM generate_series(1, 300) g",
                f"{PREFIX}-leg", _ts(LEGACY_DAY),
            )
            await conn.fetchval("SELECT bid_events_ensure_partitions($1, $1)", LEGACY_DAY)
            await conn.execute(
                "INSERT INTO bid_events (event_type, bid_id, imp_id, price, created_at) "
                "SELECT event_type, bid_id, imp_id, price, created_at FROM bid_events_legacy "
                "WHERE bid_id LIKE $1 ORDER BY created_at LIMIT 100",
                f"{PREFIX}-leg-%",
            )
            # строки старше retention в legacy — игнорируются
            await conn.execute(
                "INSERT INTO bid_events_legacy (event_type, bid_id, created_at) VALUES ('bid', $1, $2)",
                f"{PREFIX}-leg-old", _ts(EXPIRED_DAY),
            )
        finally:
            await conn.close()

    _run(seed())
    assert _script("verify-legacy") == 2
    assert _script("backfill-legacy", "--dry-run") == 2
    assert _run(_val("SELECT count(*) FROM bid_events WHERE bid_id LIKE $1", f"{PREFIX}-leg-%")) == 100

    assert _script("backfill-legacy") == 0
    assert _run(_val("SELECT count(*) FROM bid_events WHERE bid_id LIKE $1", f"{PREFIX}-leg-%")) == 300
    # повторный запуск — ничего не дублирует
    assert _script("backfill-legacy") == 0
    assert _run(_val("SELECT count(*) FROM bid_events WHERE bid_id LIKE $1", f"{PREFIX}-leg-%")) == 300
    assert _script("verify-legacy") == 0
    assert _run(_val("SELECT count(*) FROM bid_events WHERE bid_id = $1", f"{PREFIX}-leg-old")) == 0
