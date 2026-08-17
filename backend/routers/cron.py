"""Cron endpoints, hit by Vercel Cron in production.

Protected by CRON_SECRET when set (Vercel sends it as a Bearer token).
"""

import os

from fastapi import APIRouter, HTTPException, Request

from cache import clear_daily_spend
from db import get_pool
from rules_engine import evaluate_rules_for_user

router = APIRouter(prefix="/cron", tags=["cron"])


def _check_secret(request: Request) -> None:
    secret = os.environ.get("CRON_SECRET")
    if not secret:
        return  # not configured: allow (dev)
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Invalid cron secret")


@router.get("/rules")
async def run_all_rules(request: Request):
    """Evaluate auto-rules for every user with active rules."""
    _check_secret(request)
    pool = await get_pool()
    user_ids = await pool.fetch("SELECT DISTINCT user_id FROM rules WHERE status = 'active'")
    total_triggered = 0
    for row in user_ids:
        triggered = await evaluate_rules_for_user(str(row["user_id"]))
        total_triggered += len(triggered)
    return {"users_checked": len(user_ids), "rules_triggered": total_triggered}


@router.get("/reset-daily")
async def reset_daily_budgets(request: Request):
    """Reset daily spend counters in Postgres AND Redis (run at 00:00 UTC)."""
    _check_secret(request)
    pool = await get_pool()
    campaigns = await pool.execute("UPDATE campaigns SET spent_today = 0 WHERE spent_today > 0")
    line_items = await pool.execute("UPDATE line_items SET spent_today = 0 WHERE spent_today > 0")
    redis_keys = await clear_daily_spend()
    return {"campaigns": campaigns, "line_items": line_items, "redis_keys_cleared": redis_keys}
