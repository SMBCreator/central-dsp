"""Redis client (Upstash) for budget guard, frequency capping, pacing."""
import os
from datetime import datetime, timedelta, timezone

import redis.asyncio as redis

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL") or os.environ.get("KV_URL")
        if not url:
            raise RuntimeError("REDIS_URL is not configured")
        _client = redis.from_url(url, decode_responses=True)
    return _client


# --- Budget guard -----------------------------------------------------------
# Atomically reserve spend against a daily budget. Returns True when the
# reservation fits inside the budget, False otherwise (and rolls back).
_BUDGET_LUA = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local budget = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current = tonumber(redis.call('GET', key) or '0')
if current + amount > budget then
  return 0
end
redis.call('INCRBYFLOAT', key, amount)
if redis.call('TTL', key) < 0 then
  redis.call('EXPIRE', key, ttl)
end
return 1
"""


def _seconds_until_utc_midnight() -> int:
    """Daily spend keys must expire exactly at 00:00 UTC, matching the cron reset."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((tomorrow - now).total_seconds()), 60)


async def reserve_budget(scope: str, entity_id: str, amount: float, budget: float) -> bool:
    """scope: 'camp' or 'li'; amount/budget in account currency."""
    if budget <= 0:
        return False
    r = get_redis()
    key = f"spend:{scope}:{entity_id}"
    ttl = _seconds_until_utc_midnight()
    ok = await r.eval(_BUDGET_LUA, 1, key, str(amount), str(budget), str(ttl))
    return bool(ok)


async def clear_daily_spend() -> int:
    """Delete all daily spend reservations (called by the midnight cron)."""
    r = get_redis()
    deleted = 0
    async for key in r.scan_iter(match="spend:*", count=200):
        await r.delete(key)
        deleted += 1
    return deleted


async def release_budget(scope: str, entity_id: str, amount: float) -> None:
    """Refund a reservation (e.g. on loss)."""
    r = get_redis()
    key = f"spend:{scope}:{entity_id}"
    await r.incrbyfloat(key, -amount)


async def get_spend(scope: str, entity_id: str) -> float:
    r = get_redis()
    val = await r.get(f"spend:{scope}:{entity_id}")
    return float(val) if val else 0.0


# --- Frequency capping ------------------------------------------------------
async def check_frequency_cap(campaign_id: str, device_id: str, cap: int, period_hours: int) -> bool:
    """Read-only check at bid time: True when another impression is allowed.

    The counter is incremented only on a real impression (bump_frequency),
    so lost auctions never consume the cap.
    """
    if not device_id or cap <= 0:
        return True
    r = get_redis()
    count = await r.get(f"freq:{campaign_id}:{device_id}")
    return int(count or 0) < cap


async def bump_frequency(campaign_id: str, device_id: str, period_hours: int) -> None:
    """Increment the frequency counter after a confirmed impression."""
    if not device_id:
        return
    r = get_redis()
    key = f"freq:{campaign_id}:{device_id}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, period_hours * 3600)


# --- Idempotency ------------------------------------------------------------
async def mark_once(kind: str, token: str, ttl: int = 86400) -> bool:
    """Returns True the first time a (kind, token) pair is seen."""
    r = get_redis()
    return bool(await r.set(f"once:{kind}:{token}", "1", nx=True, ex=ttl))
