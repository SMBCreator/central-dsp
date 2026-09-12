"""Transactional billing for won impressions.

Single entry point `bill_win` runs the whole money mutation inside one DB
transaction with a hard idempotency guard (partial unique index on
transactions.bid_id WHERE type='spend'):

  1. INSERT ledger row (ON CONFLICT DO NOTHING) -- duplicate => no-op
  2. UPDATE advertiser balance (RETURNING new balance)
  3. backfill balance_after on the ledger row
  4. bump campaign / line item spend counters
  5. auto-pause campaigns when the balance or total budget is exhausted

Everything commits or nothing does: a crash between steps can never leave
a charge without a ledger row or vice versa.
"""
from dataclasses import dataclass
from decimal import Decimal

import asyncpg

from business_day import business_date
from money import CURRENCY, ZERO, money


@dataclass
class BillResult:
    billed: bool          # False => duplicate (already billed for this bid)
    cost: Decimal
    balance_after: Decimal | None
    advertiser_id: str | None
    paused: bool          # campaigns auto-paused by this call


async def bill_win(
    conn: asyncpg.Connection,
    *,
    bid_id: str,
    campaign_id,
    line_item_id,
    cost: Decimal,
    description: str | None = None,
) -> BillResult:
    """Bill one paid event of a bid: the won impression for CPM line items,
    the first click for CPC ones (audit R-03). Either way there is exactly one
    'spend' ledger row per bid_id. `conn` must be a dedicated connection
    (not a pool) -- the function opens its own transaction."""
    cost = money(cost)
    description = description or f"Win notice bid={bid_id}"
    if cost < ZERO:
        raise ValueError(f"negative cost {cost} for bid {bid_id}")

    async with conn.transaction():
        owner = await conn.fetchrow(
            """
            SELECT a.id AS advertiser_id, a.user_id
            FROM campaigns c JOIN advertisers a ON a.id = c.advertiser_id
            WHERE c.id = $1
            FOR UPDATE OF a
            """,
            campaign_id,
        )
        if owner is None:
            # Campaign/advertiser vanished between bid and win: log nothing to
            # the ledger (no one to charge), report as not billed.
            return BillResult(False, cost, None, None, False)

        ledger_id = await conn.fetchval(
            """
            INSERT INTO transactions (user_id, advertiser_id, campaign_id, bid_id,
                                      type, amount, currency, description)
            VALUES ($1, $2, $3, $4, 'spend', $5, $6, $7)
            ON CONFLICT (bid_id) WHERE type = 'spend' AND bid_id IS NOT NULL
            DO NOTHING
            RETURNING id
            """,
            owner["user_id"], owner["advertiser_id"], campaign_id, bid_id,
            -cost, CURRENCY, description,
        )
        if ledger_id is None:
            return BillResult(False, cost, None, str(owner["advertiser_id"]), False)

        balance_after = await conn.fetchval(
            "UPDATE advertisers SET balance = balance - $2, updated_at = now() WHERE id = $1 RETURNING balance",
            owner["advertiser_id"], cost,
        )
        await conn.execute(
            "UPDATE transactions SET balance_after = $2 WHERE id = $1",
            ledger_id, balance_after,
        )
        # spent_today belongs to a business day (Europe/Moscow): the first win
        # of a new day restarts the counter instead of waiting for a cron reset.
        today = business_date()
        camp = await conn.fetchrow(
            """
            UPDATE campaigns
            SET spent_today = CASE WHEN spent_today_date = $3 THEN spent_today + $2 ELSE $2 END,
                spent_today_date = $3,
                spent_total = spent_total + $2,
                updated_at = now()
            WHERE id = $1
            RETURNING total_budget, spent_total
            """,
            campaign_id, cost, today,
        )
        if line_item_id is not None:
            await conn.execute(
                """
                UPDATE line_items
                SET spent_today = CASE WHEN spent_today_date = $3 THEN spent_today + $2 ELSE $2 END,
                    spent_today_date = $3,
                    updated_at = now()
                WHERE id = $1
                """,
                line_item_id, cost, today,
            )

        paused = False
        if money(balance_after) <= ZERO:
            await conn.execute(
                "UPDATE campaigns SET status = 'paused', stop_reason = 'balance', stopped_at = now(), updated_at = now() "
                "WHERE advertiser_id = $1 AND status = 'active'",
                owner["advertiser_id"],
            )
            paused = True
        elif camp and money(camp["total_budget"]) > ZERO and money(camp["spent_total"]) >= money(camp["total_budget"]):
            await conn.execute(
                "UPDATE campaigns SET status = 'paused', stop_reason = 'total_budget', stopped_at = now(), updated_at = now() "
                "WHERE id = $1 AND status = 'active'",
                campaign_id,
            )
            paused = True

    return BillResult(True, cost, money(balance_after), str(owner["advertiser_id"]), paused)
