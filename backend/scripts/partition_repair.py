"""bid_events partitions: inspect, create ahead, drain DEFAULT, backfill legacy (audit O-06 / C5).

`bid_events` is RANGE-partitioned by day (migration 033). Two things go wrong in
practice and this script is the operator tool for both:

* Rows in `bid_events_default`: an event arrived for a day that had no partition
  (cron lagged, VPS was down). They are not lost, but retention never drops them
  and, before migration 057, they made `CREATE PARTITION` for that day fail.
* Rows only in `bid_events_legacy`: migration 033 copies with INSERT ... SELECT
  and then renames. Anything committed between the copy snapshot and the rename
  exists only in the legacy table. Same for a manual online upgrade where the
  copy ran in batches under live traffic.

Commands (DATABASE_URL required; run inside the backend container). The script
pins its own session to UTC and, before moving anything, checks that the existing
day partitions have UTC-midnight bounds — bounds created by the backend depend on
the server TimeZone, and a mismatch would silently misfile rows:

    python scripts/partition_repair.py status
        Partition inventory, DEFAULT contents per day, legacy table, staged
        (created but not attached) tables. Exit 2 if anything needs attention.

    python scripts/partition_repair.py ensure [--ahead N]
        Create daily partitions from today to today+N (default 60). Rows already
        sitting in DEFAULT for those days are moved atomically (migration 057).

    python scripts/partition_repair.py repair-default [--batch N] [--purge-expired] [--dry-run]
        Move every row out of DEFAULT into its daily partition.
        - past days: create the partition as a standalone table, move rows in
          batches of N (default 20000, each its own transaction), then ATTACH;
          verifies per-day count before == after.
        - today/future days: atomic create+move+attach in one transaction (live
          writers keep inserting into that range).
        - days older than BID_EVENTS_RETENTION_DAYS: retention has already
          dropped the partition; rows are deleted only with --purge-expired,
          otherwise reported and the run exits 2.

    python scripts/partition_repair.py backfill-legacy [--dry-run]
        Copy rows that exist in bid_events_legacy but not in bid_events
        (anti-join on event_type, bid_id, imp_id, created_at), one day per
        transaction, only for days inside the retention window. Columns are the
        intersection of both tables (legacy predates 034/046 columns).

    python scripts/partition_repair.py verify-legacy
        Count legacy rows inside the retention window with no counterpart in
        bid_events. Exit 0 when zero, 2 otherwise.

Exit codes: 0 ok, 1 error (incl. partition bounds not aligned to UTC midnight),
2 work remaining / verification failed, 3 another repair run holds the advisory
lock, 4 bid_events is not partitioned.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402

EXIT_OK, EXIT_ERROR, EXIT_ATTENTION, EXIT_LOCKED, EXIT_NOT_PARTITIONED = 0, 1, 2, 3, 4

# Distinct from migrate.py's lock: a repair must not block a deploy, but two
# repairs (or repair + manual ensure via this script) must not race on ATTACH.
ADVISORY_LOCK_KEY = 0x5041_5254_3035_3700  # "PART0570"

PARENT = "bid_events"
DEFAULT = "bid_events_default"
LEGACY = "bid_events_legacy"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _pname(day: dt.date) -> str:
    return f"bid_events_{day:%Y%m%d}"


def _retention_days() -> int:
    return int(os.environ.get("BID_EVENTS_RETENTION_DAYS", "90"))


async def _is_partitioned(conn: asyncpg.Connection) -> bool:
    return bool(
        await conn.fetchval(
            """SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
               WHERE n.nspname = 'public' AND c.relname = $1 AND c.relkind = 'p')""",
            PARENT,
        )
    )


async def _table_exists(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = $1 AND c.relkind IN ('r', 'p'))",
            name,
        )
    )


async def _is_attached(conn: asyncpg.Connection, name: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = $1 AND c.relname = $2)",
            PARENT,
            name,
        )
    )


async def _columns(conn: asyncpg.Connection, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT a.attname FROM pg_attribute a WHERE a.attrelid = ('public.' || quote_ident($1))::regclass "
        "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum",
        table,
    )
    return [r["attname"] for r in rows]


def _qcols(cols: list[str], prefix: str = "") -> str:
    return ", ".join(f'{prefix}"{c}"' for c in cols)


async def _day_partitions(conn: asyncpg.Connection) -> list[dt.date]:
    rows = await conn.fetch(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = $1 AND c.relname ~ '^bid_events_[0-9]{8}$' "
        "ORDER BY c.relname",
        PARENT,
    )
    return [dt.datetime.strptime(r["relname"][11:], "%Y%m%d").date() for r in rows]


async def _staged_tables(conn: asyncpg.Connection) -> list[str]:
    """bid_events_YYYYMMDD tables that exist but are not attached (interrupted repair)."""
    rows = await conn.fetch(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname ~ '^bid_events_[0-9]{8}$' "
        "AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid) ORDER BY 1"
    )
    return [r["relname"] for r in rows]


async def _default_days(conn: asyncpg.Connection) -> list[tuple[dt.date, int]]:
    rows = await conn.fetch(
        f"SELECT created_at::date AS day, count(*) AS n FROM {DEFAULT} GROUP BY 1 ORDER BY 1"
    )
    return [(r["day"], int(r["n"])) for r in rows]


async def _acquire_lock(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY))


async def _misaligned_partitions(conn: asyncpg.Connection) -> list[str]:
    """Day partitions whose lower bound is not UTC midnight. The backend creates
    bounds as `day::timestamptz` in *its* session TimeZone; this script works in UTC.
    Any mismatch means the two would disagree on which day a row belongs to."""
    rows = await conn.fetch(
        "SELECT c.relname, (regexp_match(pg_get_expr(c.relpartbound, c.oid), $$FROM \\('([^']+)'\\)$$))[1]::timestamptz AS lo "
        "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid JOIN pg_class p ON p.oid = i.inhparent "
        "WHERE p.relname = $1 AND c.relname ~ '^bid_events_[0-9]{8}$'",
        PARENT,
    )
    bad = []
    for r in rows:
        lo = r["lo"].astimezone(dt.timezone.utc)
        if lo.time() != dt.time.min or r["relname"] != _pname(lo.date()):
            bad.append(f"{r['relname']} FROM {lo.isoformat()}")
    return bad


# --------------------------------------------------------------------------- status


async def cmd_status(conn: asyncpg.Connection, args: argparse.Namespace) -> int:
    server_tz = await conn.fetchval("SELECT reset_val FROM pg_settings WHERE name = 'TimeZone'")
    keep = _retention_days()
    today = await conn.fetchval("SELECT current_date")
    _log(f"server TimeZone={server_tz} (script session: UTC)  current_date={today}  BID_EVENTS_RETENTION_DAYS={keep}")
    misaligned = await _misaligned_partitions(conn)
    if misaligned:
        _log(f"MISALIGNED partition bounds (not UTC midnight): {', '.join(misaligned[:5])}"
             + (f" (+{len(misaligned) - 5} more)" if len(misaligned) > 5 else "")
             + " -> backend session TimeZone is not UTC; repair-default will refuse")

    days = await _day_partitions(conn)
    if days:
        horizon = (days[-1] - today).days
        _log(f"partitions: {len(days)}  first={days[0]}  last={days[-1]}  horizon_days={horizon}")
        gaps = [d for d in (days[0] + dt.timedelta(n) for n in range((days[-1] - days[0]).days + 1)) if d not in set(days)]
        if gaps:
            _log(f"  gaps inside range: {len(gaps)} days, e.g. {', '.join(str(g) for g in gaps[:5])}")
    else:
        _log("partitions: none")

    attention = bool(misaligned)
    staged = await _staged_tables(conn)
    if staged:
        attention = True
        _log(f"STAGED (created, not attached) tables: {', '.join(staged)} -> rerun repair-default")

    default_days = await _default_days(conn)
    total_default = sum(n for _, n in default_days)
    if total_default:
        attention = True
        _log(f"DEFAULT partition: {total_default} rows over {len(default_days)} day(s):")
        cutoff = today - dt.timedelta(days=keep)
        for day, n in default_days:
            tag = "EXPIRED (older than retention)" if day < cutoff else ("live day" if day >= today else "past day")
            _log(f"  {day}: {n:>10}  {tag}")
    else:
        _log("DEFAULT partition: empty")

    if await _table_exists(conn, LEGACY):
        est = await conn.fetchval("SELECT reltuples::bigint FROM pg_class WHERE relname = $1", LEGACY)
        est_txt = "unknown (not analyzed)" if est is None or est < 0 else f"~{est} by planner estimate"
        _log(f"legacy table {LEGACY}: present ({est_txt} rows) -> verify-legacy / backfill-legacy, then DROP")
    else:
        _log(f"legacy table {LEGACY}: absent")
    return EXIT_ATTENTION if attention else EXIT_OK


# --------------------------------------------------------------------------- ensure


async def cmd_ensure(conn: asyncpg.Connection, args: argparse.Namespace) -> int:
    today = await conn.fetchval("SELECT current_date")
    to_day = today + dt.timedelta(days=args.ahead)
    created = await conn.fetchval("SELECT bid_events_ensure_partitions($1, $2)", today, to_day)
    horizon = await conn.fetchval("SELECT bid_events_partition_horizon_days()")
    _log(f"ensure: created={created} partitions for {today}..{to_day}; horizon_days={horizon}")
    return EXIT_OK


# --------------------------------------------------------------------------- repair-default


async def _count_range(conn: asyncpg.Connection, table: str, lo: dt.datetime, hi: dt.datetime) -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE created_at >= $1 AND created_at < $2", lo, hi))


def _overriding(target: str) -> str:
    """id — GENERATED ALWAYS AS IDENTITY у родителя: перенос с сохранением id
    требует OVERRIDING SYSTEM VALUE. У standalone-таблицы identity нет."""
    return "OVERRIDING SYSTEM VALUE " if target == PARENT else ""


async def _move_batch(conn: asyncpg.Connection, target: str, cols: list[str], lo, hi, batch: int) -> int:
    """One batch DEFAULT -> target, in its own transaction. Returns rows moved."""
    sql = (
        f"WITH picked AS (SELECT ctid FROM {DEFAULT} WHERE created_at >= $1 AND created_at < $2 LIMIT $3), "
        f"moved AS (DELETE FROM {DEFAULT} d USING picked WHERE d.ctid = picked.ctid RETURNING {_qcols(cols, 'd.')}) "
        f"INSERT INTO {target} ({_qcols(cols)}) {_overriding(target)}SELECT {_qcols(cols)} FROM moved"
    )
    async with conn.transaction():
        status = await conn.execute(sql, lo, hi, batch)
    return int(status.rsplit(" ", 1)[-1] or 0)


async def _repair_past_day(conn: asyncpg.Connection, day: dt.date, cols: list[str], batch: int) -> tuple[int, int]:
    """Staged path for a day nobody writes to any more: standalone table, batched
    move, ATTACH. Idempotent: resumes an interrupted run (table exists, not attached)."""
    lo = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    hi = lo + dt.timedelta(days=1)
    pname = _pname(day)
    exists = await _table_exists(conn, pname)
    attached = exists and await _is_attached(conn, pname)

    before_total = await _count_range(conn, PARENT, lo, hi)
    if exists and not attached:
        before_total += await _count_range(conn, pname, lo, hi)
    if not exists:
        await conn.execute(f'CREATE TABLE "{pname}" (LIKE {PARENT} INCLUDING DEFAULTS INCLUDING CONSTRAINTS)')
        _log(f"  {day}: created standalone {pname}")

    target = PARENT if attached else pname
    moved = 0
    while True:
        n = await _move_batch(conn, target, cols, lo, hi, batch)
        if n == 0:
            break
        moved += n
        _log(f"  {day}: moved {moved} rows so far")

    if not attached:
        async with conn.transaction():
            # Same lock ATTACH takes anyway, acquired first: a late insert for this
            # range cannot slip into DEFAULT between the sweep and the ATTACH check.
            await conn.execute(f"LOCK TABLE {DEFAULT} IN ACCESS EXCLUSIVE MODE")
            # last sweep inside the attach transaction: nothing may remain in DEFAULT for the range
            await _move_batch_in_tx(conn, pname, cols, lo, hi)
            # DDL не принимает bind-параметры: литералы собирает format(%L) на стороне PG.
            attach_sql = await conn.fetchval(
                "SELECT format('ALTER TABLE %I ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)', $1::text, $2::text, $3::timestamptz, $4::timestamptz)",
                PARENT, pname, lo, hi,
            )
            await conn.execute(attach_sql)
        _log(f"  {day}: attached {pname}")

    after_total = await _count_range(conn, PARENT, lo, hi)
    left = await _count_range(conn, DEFAULT, lo, hi)
    if after_total != before_total or left != 0:
        raise RuntimeError(f"{day}: count mismatch before={before_total} after={after_total} left_in_default={left}")
    return moved, after_total


async def _move_batch_in_tx(conn: asyncpg.Connection, target: str, cols: list[str], lo, hi) -> int:
    sql = (
        f"WITH moved AS (DELETE FROM {DEFAULT} WHERE created_at >= $1 AND created_at < $2 RETURNING {_qcols(cols)}) "
        f"INSERT INTO {target} ({_qcols(cols)}) {_overriding(target)}SELECT {_qcols(cols)} FROM moved"
    )
    status = await conn.execute(sql, lo, hi)
    return int(status.rsplit(" ", 1)[-1] or 0)


async def _repair_live_day(conn: asyncpg.Connection, day: dt.date, cols: list[str], batch: int) -> int:
    """Today/future: writers are active for this range, so create+move+attach must be
    one transaction (bid_events_ensure_partitions, migration 057). If the partition
    already exists (rows got into DEFAULT via a race), drain in batches through the parent."""
    lo = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    hi = lo + dt.timedelta(days=1)
    pname = _pname(day)
    moved = 0
    if not await _table_exists(conn, pname):
        n_before = await _count_range(conn, DEFAULT, lo, hi)
        await conn.fetchval("SELECT bid_events_ensure_partitions($1, $1)", day)
        moved += n_before
        _log(f"  {day}: created {pname} and moved {n_before} rows atomically")
    while True:
        n = await _move_batch(conn, PARENT, cols, lo, hi, batch)
        if n == 0:
            break
        moved += n
    left = await _count_range(conn, DEFAULT, lo, hi)
    if left:
        raise RuntimeError(f"{day}: {left} rows still in DEFAULT after repair")
    return moved


async def _purge_expired_day(conn: asyncpg.Connection, day: dt.date, batch: int) -> int:
    lo = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    hi = lo + dt.timedelta(days=1)
    deleted = 0
    while True:
        status = await conn.execute(
            f"DELETE FROM {DEFAULT} d USING (SELECT ctid FROM {DEFAULT} WHERE created_at >= $1 AND created_at < $2 LIMIT $3) p "
            "WHERE d.ctid = p.ctid",
            lo, hi, batch,
        )
        n = int(status.rsplit(" ", 1)[-1] or 0)
        if n == 0:
            break
        deleted += n
    return deleted


async def cmd_repair_default(conn: asyncpg.Connection, args: argparse.Namespace) -> int:
    misaligned = await _misaligned_partitions(conn)
    if misaligned:
        _log("ERROR: existing partition bounds are not UTC midnight (backend session TimeZone is not UTC): "
             + ", ".join(misaligned[:5]) + ". Fix the server/backend TimeZone first; aborting.")
        return EXIT_ERROR
    today = await conn.fetchval("SELECT current_date")
    cutoff = today - dt.timedelta(days=_retention_days())
    cols = await _columns(conn, PARENT)
    default_days = await _default_days(conn)
    staged = await _staged_tables(conn)
    for name in staged:
        day = dt.datetime.strptime(name[11:], "%Y%m%d").date()
        if day not in {d for d, _ in default_days}:
            default_days.append((day, 0))
    default_days.sort()
    if not default_days:
        _log("repair-default: DEFAULT is empty, nothing staged")
        return EXIT_OK

    expired_left = 0
    total_moved = 0
    for day, n in default_days:
        if day < cutoff:
            if args.dry_run:
                _log(f"  {day}: {n} rows EXPIRED (older than retention cutoff {cutoff}) -> would {'purge' if args.purge_expired else 'leave'}")
                expired_left += 0 if args.purge_expired else n
            elif args.purge_expired:
                deleted = await _purge_expired_day(conn, day, args.batch)
                _log(f"  {day}: purged {deleted} expired rows")
            else:
                expired_left += n
                _log(f"  {day}: {n} rows EXPIRED (older than {cutoff}); rerun with --purge-expired to delete")
            continue
        if args.dry_run:
            path = "atomic ensure (live day)" if day >= today else "staged batches + ATTACH (past day)"
            _log(f"  {day}: {n} rows -> would move via {path}")
            continue
        if day >= today:
            moved = await _repair_live_day(conn, day, cols, args.batch)
        else:
            moved, _ = await _repair_past_day(conn, day, cols, args.batch)
        total_moved += moved

    left = int(await conn.fetchval(f"SELECT count(*) FROM {DEFAULT}"))
    _log(f"repair-default: moved={total_moved} left_in_default={left} expired_left={expired_left}{' (dry-run)' if args.dry_run else ''}")
    if args.dry_run:
        return EXIT_ATTENTION if default_days else EXIT_OK
    return EXIT_ATTENTION if (left or expired_left) else EXIT_OK


# --------------------------------------------------------------------------- legacy


_ANTI_JOIN = (
    "NOT EXISTS (SELECT 1 FROM {parent} b WHERE b.created_at = l.created_at AND b.bid_id = l.bid_id "
    "AND b.event_type = l.event_type AND b.imp_id IS NOT DISTINCT FROM l.imp_id)"
).format(parent=PARENT)


async def _legacy_window(conn: asyncpg.Connection) -> tuple[dt.date, dt.date, dt.date] | None:
    today = await conn.fetchval("SELECT current_date")
    cutoff = today - dt.timedelta(days=_retention_days())
    row = await conn.fetchrow(f"SELECT min(created_at)::date AS lo, max(created_at)::date AS hi FROM {LEGACY}")
    if row is None or row["lo"] is None:
        return None
    return max(row["lo"], cutoff), row["hi"], cutoff


async def _legacy_missing_for_day(conn: asyncpg.Connection, day: dt.date) -> int:
    lo = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
    hi = lo + dt.timedelta(days=1)
    return int(
        await conn.fetchval(
            f"SELECT count(*) FROM {LEGACY} l WHERE l.created_at >= $1 AND l.created_at < $2 AND {_ANTI_JOIN}", lo, hi
        )
    )


async def cmd_verify_legacy(conn: asyncpg.Connection, args: argparse.Namespace) -> int:
    if not await _table_exists(conn, LEGACY):
        _log(f"verify-legacy: {LEGACY} absent — nothing to verify")
        return EXIT_OK
    window = await _legacy_window(conn)
    if window is None:
        _log(f"verify-legacy: {LEGACY} is empty")
        return EXIT_OK
    lo_day, hi_day, cutoff = window
    missing_total = 0
    day = lo_day
    while day <= hi_day:
        missing = await _legacy_missing_for_day(conn, day)
        if missing:
            _log(f"  {day}: {missing} legacy rows missing in {PARENT}")
        missing_total += missing
        day += dt.timedelta(days=1)
    _log(f"verify-legacy: window {lo_day}..{hi_day} (retention cutoff {cutoff}); missing={missing_total}")
    return EXIT_OK if missing_total == 0 else EXIT_ATTENTION


async def cmd_backfill_legacy(conn: asyncpg.Connection, args: argparse.Namespace) -> int:
    if not await _table_exists(conn, LEGACY):
        _log(f"backfill-legacy: {LEGACY} absent — nothing to do")
        return EXIT_OK
    window = await _legacy_window(conn)
    if window is None:
        _log(f"backfill-legacy: {LEGACY} is empty")
        return EXIT_OK
    lo_day, hi_day, cutoff = window
    parent_cols = await _columns(conn, PARENT)
    legacy_cols = await _columns(conn, LEGACY)
    cols = [c for c in parent_cols if c in legacy_cols and c != "id"]
    skipped = sorted(set(legacy_cols) - set(cols) - {"id"})
    _log(f"backfill-legacy: window {lo_day}..{hi_day} (cutoff {cutoff}); columns={len(cols)}"
         + (f"; legacy-only columns ignored: {', '.join(skipped)}" if skipped else ""))

    inserted_total = 0
    day = lo_day
    while day <= hi_day:
        lo = dt.datetime.combine(day, dt.time.min, tzinfo=dt.timezone.utc)
        hi = lo + dt.timedelta(days=1)
        missing = await _legacy_missing_for_day(conn, day)
        if missing == 0:
            day += dt.timedelta(days=1)
            continue
        if args.dry_run:
            _log(f"  {day}: {missing} rows would be copied")
            inserted_total += missing
        else:
            async with conn.transaction():
                await conn.fetchval("SELECT bid_events_ensure_partitions($1, $1)", day)
                status = await conn.execute(
                    f"INSERT INTO {PARENT} ({_qcols(cols)}) SELECT {_qcols(cols, 'l.')} FROM {LEGACY} l "
                    f"WHERE l.created_at >= $1 AND l.created_at < $2 AND {_ANTI_JOIN}",
                    lo, hi,
                )
            n = int(status.rsplit(" ", 1)[-1] or 0)
            inserted_total += n
            _log(f"  {day}: copied {n} rows (missing before: {missing})")
        day += dt.timedelta(days=1)

    if args.dry_run:
        _log(f"backfill-legacy: {inserted_total} rows would be copied (dry-run)")
        return EXIT_ATTENTION if inserted_total else EXIT_OK
    _log(f"backfill-legacy: copied {inserted_total} rows; verifying")
    return await cmd_verify_legacy(conn, args)


# --------------------------------------------------------------------------- main


COMMANDS = {
    "status": cmd_status,
    "ensure": cmd_ensure,
    "repair-default": cmd_repair_default,
    "backfill-legacy": cmd_backfill_legacy,
    "verify-legacy": cmd_verify_legacy,
}
NEEDS_LOCK = {"ensure", "repair-default", "backfill-legacy"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    e = sub.add_parser("ensure")
    e.add_argument("--ahead", type=int, default=int(os.environ.get("BID_EVENTS_PARTITIONS_AHEAD_DAYS", "60")))
    r = sub.add_parser("repair-default")
    r.add_argument("--batch", type=int, default=20000)
    r.add_argument("--purge-expired", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("backfill-legacy")
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("verify-legacy")
    return p


async def run(args: argparse.Namespace, dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        # All day math (created_at::date, partition bounds) in this script is UTC.
        await conn.execute("SET TIME ZONE 'UTC'")
        if not await _is_partitioned(conn):
            _log(f"{PARENT} is not partitioned (apply migration 033 first)")
            return EXIT_NOT_PARTITIONED
        if args.command in NEEDS_LOCK and not await _acquire_lock(conn):
            _log("another partition_repair run holds the advisory lock; try later")
            return EXIT_LOCKED
        return await COMMANDS[args.command](conn, args)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        _log("DATABASE_URL is not set")
        return EXIT_ERROR
    try:
        return asyncio.run(run(args, dsn))
    except Exception as e:  # noqa: BLE001 — operator tool: report and exit non-zero
        _log(f"ERROR: {type(e).__name__}: {e}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
