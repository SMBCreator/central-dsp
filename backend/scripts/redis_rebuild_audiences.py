"""Rebuild the Redis audience mirror (audz:{segment_id}) from Postgres (audit R-06).

audience_members is the source of truth (segment_id, device_id, expires_at);
the bidder only reads Redis. After a Redis loss (OOM, recreate without the
AOF volume, flush) or right after the deploy that moved from aud:* SETs to
audz:* ZSETs, include-segments are empty and campaigns with audience_include
go silent until this runs. /cron/audience-sync re-seeds empty keys hourly;
this script does it now and, with --force, also rewrites non-empty keys.

Usage (inside the backend container or with DATABASE_URL/REDIS_URL set):

    python scripts/redis_rebuild_audiences.py             # dry-run: per-segment PG vs Redis counts
    python scripts/redis_rebuild_audiences.py --apply     # seed segments whose audz: key is empty
    python scripts/redis_rebuild_audiences.py --apply --force   # DEL + full rewrite of every active segment

Exit codes: 0 ok, 1 error, 2 differences found in dry-run.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
import redis.asyncio as redis  # noqa: E402

from audiences import AUDIENCE_PREFIX, MAX_TTL_DAYS, audience_key  # noqa: E402

_KEY_GRACE = 24 * 3600
_BATCH = 5000


async def rebuild(conn: asyncpg.Connection, r: redis.Redis, *, write: bool, force: bool) -> int:
    now = int(time.time())
    segs = await conn.fetch(
        "SELECT s.id, s.name, s.ttl_days, COUNT(m.device_id) AS live "
        "FROM audience_segments s LEFT JOIN audience_members m "
        "  ON m.segment_id = s.id AND m.expires_at > now() "
        "WHERE s.is_active GROUP BY s.id, s.name, s.ttl_days ORDER BY s.name"
    )
    changed = 0
    for seg in segs:
        key = audience_key(str(seg["id"]))
        in_redis = int(await r.zcount(key, now, "+inf") or 0)
        live = int(seg["live"] or 0)
        status = "ok" if in_redis == live else ("EMPTY" if in_redis == 0 else "DRIFT")
        print(f"{seg['name'][:40]:40} {str(seg['id'])[:8]}  pg={live:8d}  redis={in_redis:8d}  {status}")
        needs = force or (in_redis == 0 and live > 0)
        if not needs:
            continue
        changed += 1
        if not write:
            continue
        if force:
            await r.delete(key)
        offset = 0
        while True:
            rows = await conn.fetch(
                "SELECT device_id, expires_at FROM audience_members "
                "WHERE segment_id = $1 AND expires_at > now() ORDER BY device_id LIMIT $2 OFFSET $3",
                seg["id"], _BATCH, offset,
            )
            if not rows:
                break
            await r.zadd(key, {row["device_id"]: int(row["expires_at"].timestamp()) for row in rows})
            offset += len(rows)
            if len(rows) < _BATCH:
                break
        await r.expire(key, min(int(seg["ttl_days"]), MAX_TTL_DAYS) * 24 * 3600 + _KEY_GRACE)
        print(f"  -> seeded {offset} members into {key}")
    return changed


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write keys (default: dry-run)")
    ap.add_argument("--force", action="store_true", help="rewrite every active segment, not only empty keys")
    args = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    rurl = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(dsn)
    r = redis.from_url(rurl, decode_responses=True)
    try:
        changed = await rebuild(conn, r, write=args.apply, force=args.force)
    finally:
        await conn.close()
        await r.aclose()
    if args.apply:
        print(f"applied: {changed} segments seeded ({AUDIENCE_PREFIX}*)")
        return 0
    if changed:
        print(f"dry-run: {changed} segments need seeding (run with --apply)")
        return 2
    print("dry-run: Redis mirror matches Postgres")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
