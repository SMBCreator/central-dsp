"""Seed / check data for the backup->destroy->restore drill (C2 / O-02).

    python ops/tests/seed_drill.py seed  --dsn postgresql://... --out expected.json
    python ops/tests/seed_drill.py check --dsn postgresql://... --expected expected.json
    python ops/tests/seed_drill.py tamper --dsn postgresql://...   # balance += 1 for one advertiser

`seed` writes money exactly the way the application does (billing_core.bill_win and
routers/billing._money_op statements), so the ledger snapshot exercises all three
reconciliation statuses:
  * advertiser A: deposit + 150 spends with 6-dp costs + adjustment  -> status 'rounding'
    (balance NUMERIC(14,4) is rounded on every spend, amount is NUMERIC(14,6));
  * advertiser B: deposit only                                          -> status 'ok';
  * advertiser C: balance set without a ledger row (legacy seed)        -> status 'mismatch',
    pre-existing at backup time — restore must report it but NOT fail on it.
Only asyncpg is required (same as backend/migrate.py).
"""
import argparse
import asyncio
import json
import sys
import uuid
from decimal import Decimal, ROUND_HALF_UP

import asyncpg

Q6 = Decimal("0.000001")


def cpm_to_cost(cpm: Decimal) -> Decimal:  # mirrors backend/money.py
    return (cpm.quantize(Q6, ROUND_HALF_UP) / Decimal(1000)).quantize(Q6, ROUND_HALF_UP)


async def seed(dsn: str) -> dict:
    conn = await asyncpg.connect(dsn)
    try:
        tag = uuid.uuid4().hex[:8]
        uid = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'drill', 'admin') RETURNING id",
            f"drill-{tag}@test.local")
        adv_a = await conn.fetchval("INSERT INTO advertisers (user_id, name, status) VALUES ($1, 'drill-A', 'active') RETURNING id", uid)
        adv_b = await conn.fetchval("INSERT INTO advertisers (user_id, name, status) VALUES ($1, 'drill-B', 'active') RETURNING id", uid)
        adv_c = await conn.fetchval("INSERT INTO advertisers (user_id, name, status, balance) VALUES ($1, 'drill-C', 'active', 75.00) RETURNING id", uid)

        async def money_op(adv, typ, amount: Decimal):  # routers/billing._money_op
            async with conn.transaction():
                old = await conn.fetchval("SELECT balance FROM advertisers WHERE id = $1 FOR UPDATE", adv)
                new = old + amount
                await conn.execute("UPDATE advertisers SET balance = $2 WHERE id = $1", adv, new)
                await conn.execute(
                    "INSERT INTO transactions (user_id, advertiser_id, type, amount, balance_after, description) VALUES ($1, $2, $3, $4, $5, 'drill')",
                    uid, adv, typ, amount, new)

        await money_op(adv_a, "deposit", Decimal("1000.00"))
        await money_op(adv_b, "deposit", Decimal("200.00"))

        camp = await conn.fetchval(
            "INSERT INTO campaigns (advertiser_id, user_id, name, status, daily_budget, total_budget) VALUES ($1, $2, 'drill', 'active', 500, 900) RETURNING id",
            adv_a, uid)
        li = await conn.fetchval(
            "INSERT INTO line_items (campaign_id, user_id, name, bid_type, bid_price, daily_budget) VALUES ($1, $2, 'drill', 'cpm', 12.3457, 500) RETURNING id",
            camp, uid)
        for i in range(150):  # billing_core.bill_win statements
            cost = cpm_to_cost(Decimal("12.3457") + Decimal(i) / Decimal(7))
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO transactions (user_id, advertiser_id, campaign_id, type, amount, bid_id) VALUES ($1, $2, $3, 'spend', $4, $5)",
                    uid, adv_a, camp, -cost, f"drill-{tag}-{i}")
                await conn.execute("UPDATE advertisers SET balance = balance - $2 WHERE id = $1", adv_a, cost)
                await conn.execute("UPDATE campaigns SET spent_total = spent_total + $2 WHERE id = $1", camp, cost)
        await money_op(adv_a, "adjustment", Decimal("-50.00"))

        await conn.execute(
            """INSERT INTO bid_events (event_type, bid_id, campaign_id, line_item_id, price, clear_price)
               SELECT CASE WHEN g % 3 = 0 THEN 'win' ELSE 'bid' END, 'drill-ev-' || g, $1, $2, 12.3457, 11.5
               FROM generate_series(1, 500) g""", camp, li)

        return await snapshot(conn)
    finally:
        await conn.close()


async def snapshot(conn) -> dict:
    advs = await conn.fetch(
        """SELECT a.id::text AS id, a.balance::text AS balance,
                  COALESCE((SELECT SUM(amount) FROM transactions t WHERE t.advertiser_id = a.id), 0)::numeric(16,6)::text AS ledger_sum,
                  (SELECT count(*) FROM transactions t WHERE t.advertiser_id = a.id) AS tx_count
           FROM advertisers a ORDER BY a.id""")
    return {
        "advertisers": {r["id"]: {"balance": r["balance"], "ledger_sum": r["ledger_sum"], "tx_count": r["tx_count"]} for r in advs},
        "transactions": await conn.fetchval("SELECT count(*) FROM transactions"),
        "transactions_total": str(await conn.fetchval("SELECT COALESCE(SUM(amount),0)::numeric(18,6) FROM transactions")),
        "bid_events": await conn.fetchval("SELECT count(*) FROM bid_events"),
        "campaigns": await conn.fetchval("SELECT count(*) FROM campaigns"),
        "campaign_spent_total": str(await conn.fetchval("SELECT COALESCE(SUM(spent_total),0) FROM campaigns")),
        "users": await conn.fetchval("SELECT count(*) FROM users"),
        "schema_migrations": await conn.fetchval("SELECT count(*) FROM schema_migrations"),
    }


async def check(dsn: str, expected: dict) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        actual = await snapshot(conn)
    finally:
        await conn.close()
    bad = 0
    for k, v in expected.items():
        if actual.get(k) != v:
            bad += 1
            print(f"MISMATCH {k}: expected={v!r} actual={actual.get(k)!r}")
    if not bad:
        print(f"check ok: {len(expected['advertisers'])} advertisers, {expected['transactions']} transactions, "
              f"{expected['bid_events']} bid_events, {expected['schema_migrations']} migrations match the seed")
    return 1 if bad else 0


async def tamper(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        adv = await conn.fetchval("SELECT id FROM advertisers WHERE name = 'drill-A'")
        await conn.execute("UPDATE advertisers SET balance = balance + 1 WHERE id = $1", adv)
        print(f"tampered: advertiser {adv} balance += 1 (no ledger row)")
    finally:
        await conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["seed", "check", "tamper"])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--out")
    ap.add_argument("--expected")
    a = ap.parse_args()
    if a.mode == "seed":
        snap = asyncio.run(seed(a.dsn))
        with open(a.out or "expected.json", "w") as fh:
            json.dump(snap, fh, indent=1, default=str)
        print(f"seeded: {json.dumps({k: v for k, v in snap.items() if k != 'advertisers'}, default=str)}")
        return 0
    if a.mode == "check":
        with open(a.expected) as fh:
            return asyncio.run(check(a.dsn, json.load(fh)))
    return asyncio.run(tamper(a.dsn))


if __name__ == "__main__":
    sys.exit(main())
