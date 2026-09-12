"""Migration runner for Central DSP.

Usage:
    python migrate.py                 # apply all pending migrations
    python migrate.py --dry-run       # show what would be applied
    python migrate.py --stamp-all     # mark every migration as applied WITHOUT
                                      # executing (one-time bootstrap for the
                                      # existing production database)
    python migrate.py --stamp 033_x.sql   # mark ONE pending migration as applied
                                          # (after running it manually, e.g. a
                                          # heavy partitioning migration)

Rules:
  - Migrations live in backend/migrations/NNN_name.sql, applied in filename order.
  - Each applied file is recorded in schema_migrations with its sha256, so a
    modified historical file is detected and refused.
  - A Postgres advisory lock guarantees only one runner executes at a time.
  - Requires DATABASE_URL (same variable the app uses).
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
ADVISORY_LOCK_KEY = 0x0CD5_0001  # arbitrary constant, unique to this runner

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    sha256     TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stamped    BOOLEAN NOT NULL DEFAULT FALSE
);
"""


def _files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        return 2
    dry_run = "--dry-run" in sys.argv
    stamp_all = "--stamp-all" in sys.argv
    stamp_one: str | None = None
    if "--stamp" in sys.argv:
        idx = sys.argv.index("--stamp")
        if idx + 1 >= len(sys.argv):
            print("ERROR: --stamp requires a migration filename", file=sys.stderr)
            return 2
        stamp_one = Path(sys.argv[idx + 1]).name

    conn = await asyncpg.connect(dsn=dsn)
    try:
        got_lock = await conn.fetchval("SELECT pg_try_advisory_lock($1)", ADVISORY_LOCK_KEY)
        if not got_lock:
            print("ERROR: another migration run is in progress", file=sys.stderr)
            return 3
        if dry_run:
            # --dry-run ничего не пишет, даже bootstrap-таблицу: на пустой базе
            # все миграции просто отображаются как pending (ops/preprod_check.sh).
            has_table = await conn.fetchval("SELECT to_regclass('public.schema_migrations') IS NOT NULL")
            if not has_table:
                print("schema_migrations table does not exist yet (fresh database)")
        else:
            has_table = True
            await conn.execute(BOOTSTRAP_SQL)

        applied = {
            r["filename"]: r["sha256"]
            for r in (await conn.fetch("SELECT filename, sha256 FROM schema_migrations") if has_table else [])
        }

        pending = []
        for path in _files():
            digest = _sha256(path)
            if path.name in applied:
                if applied[path.name] != digest:
                    print(f"ERROR: {path.name} was modified after being applied "
                          f"(recorded {applied[path.name][:12]}, current {digest[:12]}). "
                          "Create a new migration instead.", file=sys.stderr)
                    return 4
                continue
            pending.append((path, digest))

        if not pending:
            print("Nothing to do: all migrations applied.")
            return 0

        if stamp_one is not None:
            match = [(p, d) for p, d in pending if p.name == stamp_one]
            if not match:
                print(f"ERROR: {stamp_one} is not a pending migration "
                      "(already applied or file not found)", file=sys.stderr)
                return 5
            pending = match
            stamp_all = True  # переиспользуем ветку stamp ниже только для этого файла

        for path, digest in pending:
            if stamp_all:
                await conn.execute(
                    "INSERT INTO schema_migrations (filename, sha256, stamped) VALUES ($1, $2, TRUE)",
                    path.name, digest,
                )
                print(f"stamped  {path.name}")
            elif dry_run:
                print(f"pending  {path.name}")
            else:
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename, sha256) VALUES ($1, $2)",
                        path.name, digest,
                    )
                print(f"applied  {path.name}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
