"""Rebuild today's Redis daily-spend counters from the ledger (audit M-03).

When Redis lost state (OOM, recreate without the AOF volume, manual flush)
the bidder would start the business day from zero and overspend by whatever
was already spent. This script recomputes the current business day's spend
per campaign and line item from `transactions` (type='spend' — the money
source of truth, written by bill_win) and writes the dated keys
`spend:camp:{id}:{YYYY-MM-DD}` / `spend:li:{id}:{YYYY-MM-DD}` with the same
TTL the bidders use.

Usage (inside the backend container or with DATABASE_URL/REDIS_URL set):

    python scripts/redis_rebuild_spend.py            # dry-run: print what would change
    python scripts/redis_rebuild_spend.py --apply    # write keys (never lowers a counter)
    python scripts/redis_rebuild_spend.py --apply --force   # overwrite even if Redis is higher

Without --force a key is only raised to the ledger value: a higher value in
Redis means live reservations for bids whose wins have not been billed yet,
and lowering it would let the gate overspend.

Exit codes: 0 ok, 1 error, 2 differences found in dry-run.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg  # noqa: E402
import redis.asyncio as redis  # noqa: E402

from business_day import (  # noqa: E402
    BUSINESS_TZ_NAME,
    business_date,
    seconds_until_business_midnight,
    spend_key,
)

_SPEND_SQL = """
SELECT t.campaign_id, e.line_item_id, SUM(-t.amount) AS spent
FROM transactions t
LEFT JOIN LATERAL (
    SELECT line_item_id FROM bid_events
    WHERE bid_id = t.bid_id AND event_type = 'win'
    ORDER BY created_at DESC LIMIT 1
) e ON TRUE
WHERE t.type = 'spend'
  AND t.campaign_id IS NOT NULL
  AND (t.created_at AT TIME ZONE $1)::date = $2
GROUP BY t.campaign_id, e.line_item_id
"""


async def compute_spend(conn: asyncpg.Connection, day) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
    camp: dict[str, Decimal] = {}
    li: dict[str, Decimal] = {}
    for row in await conn.fetch(_SPEND_SQL, BUSINESS_TZ_NAME, day):
        spent = Decimal(row["spent"] or 0)
        cid = str(row["campaign_id"])
        camp[cid] = camp.get(cid, Decimal(0)) + spent
        if row["line_item_id"] is not None:
            lid = str(row["line_item_id"])
            li[lid] = li.get(lid, Decimal(0)) + spent
    return camp, li


async def apply(r: redis.Redis, scope: str, values: dict[str, Decimal], day: str, ttl: int, *, write: bool, force: bool) -> int:
    changed = 0
    for entity_id, spent in values.items():
        key = spend_key(scope, entity_id, day)
        current_raw = await r.get(key)
        current = Decimal(current_raw) if current_raw else Decimal(0)
        target = spent
        if not force and current > target:
            action = "keep (redis higher: live reservations)"
        elif current == target:
            action = "ok"
        else:
            action = "SET" if write else "would SET"
            changed += 1
            if write:
                await r.set(key, str(target), ex=ttl)
        print(f"{key:<70} redis={current} ledger={target} -> {action}")
    return changed


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write keys (default: dry-run)")
    ap.add_argument("--force", action="store_true", help="overwrite even when Redis value is higher")
    ap.add_argument("--date", help="business date YYYY-MM-DD (default: today in %s)" % BUSINESS_TZ_NAME)
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    redis_url = os.environ.get("REDIS_URL") or os.environ.get("KV_URL")
    if not dsn or not redis_url:
        print("DATABASE_URL and REDIS_URL are required", file=sys.stderr)
        return 1

    day = business_date() if not args.date else __import__("datetime").date.fromisoformat(args.date)
    ttl = seconds_until_business_midnight()

    conn = await asyncpg.connect(dsn)
    r = redis.from_url(redis_url, decode_responses=True)
    try:
        camp, li = await compute_spend(conn, day)
        print(f"business date {day} ({BUSINESS_TZ_NAME}); {len(camp)} campaigns, {len(li)} line items with spend; ttl={ttl}s")
        changed = await apply(r, "camp", camp, day.isoformat(), ttl, write=args.apply, force=args.force)
        changed += await apply(r, "li", li, day.isoformat(), ttl, write=args.apply, force=args.force)
    finally:
        await conn.close()
        await r.aclose()

    if args.apply:
        print(f"applied: {changed} keys written")
        return 0
    print(f"dry-run: {changed} keys differ (run with --apply)")
    return 2 if changed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
