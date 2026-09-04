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


# One roundtrip for the whole bid-time gate: frequency cap check plus
# atomic budget reservation on campaign AND line-item keys. Rolls back the
# campaign reservation when the line-item budget is exhausted.
_GATE_LUA = """
local cost        = tonumber(ARGV[4])
local camp_budget = tonumber(ARGV[5])
local li_budget   = tonumber(ARGV[6])
local ttl         = tonumber(ARGV[7])
for i = 1, 3 do
  local cap = tonumber(ARGV[i])
  if cap > 0 then
    local seen = tonumber(redis.call('GET', KEYS[i]) or '0')
    if seen >= cap then return 0 end
  end
end
local camp = redis.call('INCRBYFLOAT', KEYS[4], cost)
if redis.call('TTL', KEYS[4]) < 0 then redis.call('EXPIRE', KEYS[4], ttl) end
if tonumber(camp) > camp_budget then
  redis.call('INCRBYFLOAT', KEYS[4], -cost)
  return 0
end
if li_budget > 0 then
  local li = redis.call('INCRBYFLOAT', KEYS[5], cost)
  if redis.call('TTL', KEYS[5]) < 0 then redis.call('EXPIRE', KEYS[5], ttl) end
  if tonumber(li) > li_budget then
    redis.call('INCRBYFLOAT', KEYS[5], -cost)
    redis.call('INCRBYFLOAT', KEYS[4], -cost)
    return 0
  end
end
return 1
"""


async def gate_and_reserve(
    campaign_id: str,
    li_id: str,
    cost: float,
    camp_budget: float,
    li_budget: float,
    device_id: str = "",
    freq_cap: int = 0,
    caps: list[tuple[int, int]] | None = None,
) -> bool:
    """Bid-time gate in a single Redis roundtrip.

    Checks up to 3 frequency-cap windows (read-only) and atomically reserves
    budget on both the campaign and line-item daily-spend keys. Either both
    reservations succeed or neither does. `caps` is a list of
    (impressions, period_hours); the legacy `freq_cap` (24h) is used when
    `caps` is empty.
    """
    if camp_budget <= 0:
        return False
    windows = [(int(a), int(b)) for a, b in (caps or []) if int(a) > 0 and int(b) > 0][:3]
    if not windows and freq_cap:
        windows = [(int(freq_cap), 24)]
    dev = device_id or "-"
    keys, cap_args = [], []
    for cap_n, hours in windows:
        keys.append(f"freq:{campaign_id}:{hours}h:{dev}")
        cap_args.append(str(cap_n if device_id else 0))
    while len(keys) < 3:
        keys.append(f"freq:{campaign_id}:pad{len(keys)}:{dev}")
        cap_args.append("0")
    r = get_redis()
    ttl = _seconds_until_utc_midnight()
    ok = await r.eval(
        _GATE_LUA, 5,
        *keys,
        f"spend:camp:{campaign_id}",
        f"spend:li:{li_id}",
        *cap_args, str(cost), str(camp_budget),
        str(li_budget), str(ttl),
    )
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


async def bump_frequency_multi(campaign_id: str, device_id: str, windows: list[tuple[int, int]]) -> None:
    """Increment all frequency-cap window counters after a confirmed impression."""
    if not device_id:
        return
    r = get_redis()
    for _cap, hours in windows[:3]:
        key = f"freq:{campaign_id}:{int(hours)}h:{device_id}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, int(hours) * 3600)


async def bump_frequency(campaign_id: str, device_id: str, period_hours: int) -> None:
    """Increment the frequency counter after a confirmed impression."""
    if not device_id:
        return
    r = get_redis()
    key = f"freq:{campaign_id}:{device_id}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, period_hours * 3600)


# --- Per-partner QPS limiting ------------------------------------------------
async def check_qps(partner_id: str, limit: int) -> bool:
    """Fixed 1-second window counter shared by all bidder replicas.

    Returns True while the partner is under its QPS limit. limit <= 0 means
    unlimited. The key expires after 2s so idle partners cost nothing.
    """
    if not limit or limit <= 0:
        return True
    r = get_redis()
    import time as _time

    key = f"qps:{partner_id}:{int(_time.time())}"
    # Single roundtrip: INCR + EXPIRE pipelined (unconditional EXPIRE on a
    # 2s key is harmless and saves the extra conditional roundtrip).
    pipe = r.pipeline(transaction=False)
    pipe.incr(key)
    pipe.expire(key, 2)
    count, _ = await pipe.execute()
    return count <= limit


# --- Idempotency ------------------------------------------------------------
async def mark_once(kind: str, token: str, ttl: int = 86400) -> bool:
    """Returns True the first time a (kind, token) pair is seen."""
    r = get_redis()
    return bool(await r.set(f"once:{kind}:{token}", "1", nx=True, ex=ttl))


# --- Brute-force protection ---------------------------------------------------
#
# Логин защищён счётчиком НЕУДАЧНЫХ попыток (не всех запросов): легитимный
# пользователь, входящий 10 раз за 5 минут, не должен получать 429, а
# перебор пароля — должен упереться в лимит. Два независимых окна:
#   login:email — по логину (защищает конкретный аккаунт от перебора с многих IP)
#   login:ip    — по IP клиента (защищает от перебора многих логинов с одного IP)
# Успешный вход сбрасывает счётчик по логину. Fail-open при недоступном Redis.

LOGIN_FAILS_PER_EMAIL = int(os.environ.get("LOGIN_FAILS_PER_EMAIL", "8"))
LOGIN_FAILS_PER_IP = int(os.environ.get("LOGIN_FAILS_PER_IP", "30"))
LOGIN_FAIL_WINDOW_SECONDS = int(os.environ.get("LOGIN_FAIL_WINDOW_SECONDS", "900"))


async def login_blocked(email_key: str, client_ip: str) -> int:
    """0 — можно пробовать; иначе число секунд до снятия блокировки (Retry-After)."""
    try:
        r = get_redis()
        pipe = r.pipeline(transaction=False)
        pipe.get(f"rl:login:email:{email_key}")
        pipe.ttl(f"rl:login:email:{email_key}")
        pipe.get(f"rl:login:ip:{client_ip}")
        pipe.ttl(f"rl:login:ip:{client_ip}")
        e_cnt, e_ttl, ip_cnt, ip_ttl = await pipe.execute()
        blocked_email = LOGIN_FAILS_PER_EMAIL > 0 and int(e_cnt or 0) >= LOGIN_FAILS_PER_EMAIL
        blocked_ip = LOGIN_FAILS_PER_IP > 0 and int(ip_cnt or 0) >= LOGIN_FAILS_PER_IP
        if not (blocked_email or blocked_ip):
            return 0
        retry = 0
        if blocked_email:
            retry = max(retry, int(e_ttl or 0))
        if blocked_ip:
            retry = max(retry, int(ip_ttl or 0))
        return max(retry, 1)  # TTL может быть -1 в гонке с EXPIRE — всё равно блок
    except Exception:
        return 0


async def record_login_failure(email_key: str, client_ip: str) -> None:
    """Неудачная попытка: +1 в оба окна. Окно фиксированное — TTL ставится
    при первом инкременте и не продлевается, иначе атакующий держал бы
    жертву заблокированной бесконечно, продолжая слать запросы."""
    try:
        r = get_redis()
        for key in (f"rl:login:email:{email_key}", f"rl:login:ip:{client_ip}"):
            pipe = r.pipeline(transaction=False)
            pipe.incr(key)
            pipe.ttl(key)
            cnt, ttl = await pipe.execute()
            if int(ttl) < 0:
                await r.expire(key, LOGIN_FAIL_WINDOW_SECONDS)
    except Exception:
        pass


async def clear_login_failures(email_key: str) -> None:
    """Успешный вход — счётчик по логину сбрасывается (по IP — нет: с этого IP
    могли перебирать другие аккаунты)."""
    try:
        await get_redis().delete(f"rl:login:email:{email_key}")
    except Exception:
        pass


async def rate_limit(scope: str, key: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window rate limiter. Returns True while under the limit.

    Used for login brute-force protection (GAP-002): scope="login",
    key=client ip or normalized email. Fails open on Redis errors so a
    Redis outage does not lock everyone out of the panel.
    """
    if not limit or limit <= 0:
        return True
    try:
        r = get_redis()
        rk = f"rl:{scope}:{key}"
        pipe = r.pipeline(transaction=False)
        pipe.incr(rk)
        pipe.expire(rk, window_seconds)
        count, _ = await pipe.execute()
        return int(count) <= limit
    except Exception:
        return True
