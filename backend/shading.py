"""Bid shading: hourly factor recompute + hot-path cache.

Transparent algorithm (no ML): for each (campaign, supply_partner) pair
over a trailing 24h window of bid_events:
  ratio    = avg(clear_price / price) over wins
  win_rate = wins / bids
  raw      = clamp(ratio + 0.05, MIN_FACTOR, 1.0)
  feedback: win_rate < 0.20 -> +0.05 (we are losing too much)
            win_rate > 0.60 -> -0.03 (we are overpaying)
  smoothing: EMA 0.5 with previous factor (no abrupt jumps)
Pairs with < MIN_BIDS bids keep factor 1.0 (never shade blindly).

Hot path: get_factor() reads an in-memory dict refreshed at most every
FACTOR_TTL seconds. Fail-open: any error -> factor 1.0 (bid as before).
"""
from __future__ import annotations

import time
from decimal import Decimal

from db import get_pool

MIN_FACTOR = 0.55
MIN_BIDS = 50
EMA_ALPHA = 0.5

_factors: dict[tuple[str, str], float] = {}
_loaded_at: float = 0.0
FACTOR_TTL = 60.0


async def recompute_factors() -> dict:
    """Hourly cron: recompute factors from trailing 24h of bid_events."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH agg AS (
                SELECT campaign_id, supply_partner_id,
                       count(*) FILTER (WHERE event_type = 'bid')  AS bids,
                       count(*) FILTER (WHERE event_type = 'win')  AS wins,
                       avg(clear_price / NULLIF(price, 0))
                           FILTER (WHERE event_type = 'win'
                                   AND clear_price IS NOT NULL
                                   AND price > 0)                  AS avg_ratio
                FROM bid_events
                WHERE created_at > now() - interval '24 hours'
                  AND campaign_id IS NOT NULL
                  AND supply_partner_id IS NOT NULL
                  AND NOT is_test
                GROUP BY campaign_id, supply_partner_id
            )
            SELECT a.*, f.factor AS prev_factor
            FROM agg a
            LEFT JOIN bid_shading_factors f
                   ON f.campaign_id = a.campaign_id
                  AND f.supply_partner_id = a.supply_partner_id
            """
        )
        updated = 0
        for r in rows:
            bids = int(r["bids"] or 0)
            wins = int(r["wins"] or 0)
            if bids < MIN_BIDS or wins == 0 or r["avg_ratio"] is None:
                factor = 1.0
            else:
                ratio = float(r["avg_ratio"])
                raw = min(max(ratio + 0.05, MIN_FACTOR), 1.0)
                win_rate = wins / bids
                if win_rate < 0.20:
                    raw = min(raw + 0.05, 1.0)
                elif win_rate > 0.60:
                    raw = max(raw - 0.03, MIN_FACTOR)
                prev = float(r["prev_factor"]) if r["prev_factor"] is not None else 1.0
                factor = round(EMA_ALPHA * raw + (1 - EMA_ALPHA) * prev, 4)
                factor = min(max(factor, MIN_FACTOR), 1.0)
            await conn.execute(
                """
                INSERT INTO bid_shading_factors
                    (campaign_id, supply_partner_id, factor, bids, wins, avg_ratio, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, now())
                ON CONFLICT (campaign_id, supply_partner_id)
                DO UPDATE SET factor = EXCLUDED.factor, bids = EXCLUDED.bids,
                              wins = EXCLUDED.wins, avg_ratio = EXCLUDED.avg_ratio,
                              updated_at = now()
                """,
                r["campaign_id"], r["supply_partner_id"],
                Decimal(str(factor)), bids, wins, r["avg_ratio"],
            )
            updated += 1
    global _loaded_at
    _loaded_at = 0.0  # force hot-path cache refresh
    return {"pairs_updated": updated}


def should_shade(bid_shading_enabled: bool, auction_type: int) -> bool:
    """Shade only in explicit first-price auctions (OpenRTB `at` = 1).

    In second-price auctions (at = 2, also the spec default when `at` is
    absent) the clearing price is set by the auction itself, so lowering
    the bid saves nothing and only reduces win rate."""
    return bool(bid_shading_enabled) and auction_type == 1


def compute_raw_factor(avg_ratio: float, bids: int, wins: int, prev: float = 1.0) -> float:
    """Pure math for tests: same formula as recompute_factors."""
    if bids < MIN_BIDS or wins == 0:
        return 1.0
    raw = min(max(avg_ratio + 0.05, MIN_FACTOR), 1.0)
    win_rate = wins / bids
    if win_rate < 0.20:
        raw = min(raw + 0.05, 1.0)
    elif win_rate > 0.60:
        raw = max(raw - 0.03, MIN_FACTOR)
    factor = round(EMA_ALPHA * raw + (1 - EMA_ALPHA) * prev, 4)
    return min(max(factor, MIN_FACTOR), 1.0)


async def load_factors() -> None:
    """Refresh in-memory factor cache (called lazily from hot path)."""
    global _factors, _loaded_at
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT campaign_id, supply_partner_id, factor FROM bid_shading_factors"
            )
        _factors = {
            (str(r["campaign_id"]), str(r["supply_partner_id"])): float(r["factor"])
            for r in rows
        }
        _loaded_at = time.monotonic()
    except Exception:
        _loaded_at = time.monotonic()  # fail-open: keep old dict, retry after TTL


async def get_factor(campaign_id: str, supply_partner_id: str) -> float:
    """Hot path: cached factor lookup, 1.0 when unknown (fail-open)."""
    if time.monotonic() - _loaded_at > FACTOR_TTL:
        await load_factors()
    return _factors.get((str(campaign_id), str(supply_partner_id)), 1.0)
