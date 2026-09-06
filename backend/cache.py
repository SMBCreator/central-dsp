"""Redis client (Upstash) for budget guard, frequency capping, pacing."""
import os

import redis.asyncio as redis

from business_day import business_date_str, seconds_until_business_midnight, spend_key, spend_key_date

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


def _daily_key_ttl() -> int:
    """TTL of a daily spend key created now: until 00:00 Europe/Moscow + grace.

    The key name carries the business date, so the TTL is only garbage
    collection — the boundary itself is the date in the key (audit M-04).
    """
    return seconds_until_business_midnight()


async def reserve_budget(scope: str, entity_id: str, amount: float, budget: float) -> bool:
    """scope: 'camp' or 'li'; amount/budget in account currency."""
    if budget <= 0:
        return False
    r = get_redis()
    key = spend_key(scope, entity_id)
    ttl = _daily_key_ttl()
    ok = await r.eval(_BUDGET_LUA, 1, key, str(amount), str(budget), str(ttl))
    return bool(ok)


# One roundtrip for the whole bid-time gate (audit M-05/M-06): frequency cap
# check plus atomic reservation on campaign daily, line-item daily, advertiser
# available balance and campaign total-budget remaining, and registration of
# the bid in pending:bids. Byte-identical to bidder-go luaGateAndReserve
# (tests/test_redis_contracts.py) — key layout documented there.
_GATE_LUA = """
local cost        = tonumber(ARGV[4])
local camp_budget = tonumber(ARGV[5])
local li_budget   = tonumber(ARGV[6])
local ttl         = tonumber(ARGV[7])
local has_adv     = ARGV[8] ~= ''
local has_total   = ARGV[10] == '1'
for i = 1, 3 do
  local cap = tonumber(ARGV[i])
  if cap > 0 then
    local seen = tonumber(redis.call('GET', KEYS[i]) or '0')
    if seen >= cap then return 0 end
  end
end
local function rollback(stage)
  if stage >= 4 then redis.call('INCRBYFLOAT', KEYS[7], cost) end
  if stage >= 3 then redis.call('INCRBYFLOAT', KEYS[6], cost) end
  if stage >= 2 and li_budget > 0 then redis.call('INCRBYFLOAT', KEYS[5], -cost) end
  redis.call('INCRBYFLOAT', KEYS[4], -cost)
  return 0
end
local camp = redis.call('INCRBYFLOAT', KEYS[4], cost)
if redis.call('TTL', KEYS[4]) < 0 then redis.call('EXPIRE', KEYS[4], ttl) end
if tonumber(camp) > camp_budget then return rollback(1) end
if li_budget > 0 then
  local li = redis.call('INCRBYFLOAT', KEYS[5], cost)
  if redis.call('TTL', KEYS[5]) < 0 then redis.call('EXPIRE', KEYS[5], ttl) end
  if tonumber(li) > li_budget then return rollback(2) end
end
if has_adv then
  if redis.call('EXISTS', KEYS[6]) == 0 then redis.call('SET', KEYS[6], ARGV[8]) end
  local adv = redis.call('INCRBYFLOAT', KEYS[6], -cost)
  if tonumber(adv) < 0 then return rollback(3) end
  if has_total then
    if redis.call('EXISTS', KEYS[7]) == 0 then redis.call('SET', KEYS[7], ARGV[9]) end
    local tot = redis.call('INCRBYFLOAT', KEYS[7], -cost)
    if tonumber(tot) < 0 then return rollback(4) end
    redis.call('INCRBYFLOAT', KEYS[9], cost)
  end
  redis.call('INCRBYFLOAT', KEYS[8], cost)
end
if ARGV[11] ~= '' then redis.call('ZADD', KEYS[10], ARGV[12], ARGV[11]) end
return 1
"""

# Terminal states per bid (mirror of bidder-go luaReleaseBid / luaSettleWin).
_RELEASE_BID_LUA = """
local reserve = tonumber(ARGV[2])
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', tonumber(ARGV[4])) == false then
  redis.call('ZREM', KEYS[2], ARGV[3])
  return 0
end
redis.call('ZREM', KEYS[2], ARGV[3])
local function refund(key, amount)
  if redis.call('EXISTS', key) == 1 then
    local v = tonumber(redis.call('INCRBYFLOAT', key, -amount))
    if v < 0 then
      local ttl = redis.call('TTL', key)
      if ttl > 0 then redis.call('SET', key, '0', 'EX', ttl) else redis.call('SET', key, '0') end
    end
  end
end
refund(KEYS[3], reserve)
if ARGV[6] == '1' then refund(KEYS[4], reserve) end
if redis.call('EXISTS', KEYS[5]) == 1 then redis.call('INCRBYFLOAT', KEYS[5], reserve) end
refund(KEYS[7], reserve)
if ARGV[5] == '1' then
  if redis.call('EXISTS', KEYS[6]) == 1 then redis.call('INCRBYFLOAT', KEYS[6], reserve) end
  refund(KEYS[8], reserve)
end
return 1
"""

_SETTLE_WIN_LUA = """
local reserve   = tonumber(ARGV[1])
local clearing  = tonumber(ARGV[2])
local spend_ttl = tonumber(ARGV[7])
local has_total = ARGV[5] == '1'
local has_li    = ARGV[6] == '1'
local prev = redis.call('GET', KEYS[1])
if prev == 'win' then return 0 end
redis.call('SET', KEYS[1], 'win', 'EX', tonumber(ARGV[4]))
redis.call('ZREM', KEYS[2], ARGV[3])
local function refund(key, amount)
  if redis.call('EXISTS', key) == 1 then
    local v = tonumber(redis.call('INCRBYFLOAT', key, -amount))
    if v < 0 then
      local ttl = redis.call('TTL', key)
      if ttl > 0 then redis.call('SET', key, '0', 'EX', ttl) else redis.call('SET', key, '0') end
    end
  end
end
local function charge(key, amount)
  redis.call('INCRBYFLOAT', key, amount)
  if redis.call('TTL', key) < 0 then redis.call('EXPIRE', key, spend_ttl) end
end
if prev or ARGV[8] == '1' then
  charge(KEYS[3], clearing)
  if has_li then charge(KEYS[4], clearing) end
  if redis.call('EXISTS', KEYS[5]) == 1 then redis.call('INCRBYFLOAT', KEYS[5], -clearing) end
  if has_total and redis.call('EXISTS', KEYS[6]) == 1 then redis.call('INCRBYFLOAT', KEYS[6], -clearing) end
  return 2
end
local diff = reserve - clearing
if diff > 0 then
  refund(KEYS[3], diff)
  if has_li then refund(KEYS[4], diff) end
  if redis.call('EXISTS', KEYS[5]) == 1 then redis.call('INCRBYFLOAT', KEYS[5], diff) end
  if has_total and redis.call('EXISTS', KEYS[6]) == 1 then redis.call('INCRBYFLOAT', KEYS[6], diff) end
end
refund(KEYS[7], reserve)
if has_total then refund(KEYS[8], reserve) end
return 1
"""

PENDING_BIDS_KEY = "pending:bids"
DEFAULT_RESERVE_TTL = 90          # seconds a reservation waits for win/loss (bidder-go DefaultReserveTTL)
BID_STATE_TTL = 2 * 3600          # bidstate:{bid} lifetime (bidder-go BidStateTTL)


def avail_adv_key(advertiser_id: str) -> str:
    return f"avail:adv:{advertiser_id}"


def avail_camp_key(campaign_id: str) -> str:
    return f"avail:camp:{campaign_id}"


def res_adv_key(advertiser_id: str) -> str:
    return f"res:adv:{advertiser_id}"


def res_camp_key(campaign_id: str) -> str:
    return f"res:camp:{campaign_id}"


def bid_state_key(bid_id: str) -> str:
    return f"bidstate:{bid_id}"


def _reservation_keys(bid_id: str, campaign_id: str, li_id: str, advertiser_id: str, day: str) -> list[str]:
    adv = advertiser_id or "-"
    return [
        bid_state_key(bid_id), PENDING_BIDS_KEY,
        spend_key("camp", campaign_id, day), spend_key("li", li_id or "", day),
        avail_adv_key(adv), avail_camp_key(campaign_id), res_adv_key(adv), res_camp_key(campaign_id),
    ]


def reservation_from_bidmeta(row) -> dict | None:
    """Extract the reservation description bidder-go/Python wrote into bidmeta.

    Returns None for test bids and rows without a campaign. Legacy rows
    (before audit M-06) have no `reserve`: fall back to price/1000 and no
    advertiser constraint, which matches what the old gate reserved.
    """
    if row is None:
        return None
    get = row.get if hasattr(row, "get") else (lambda k, d=None: row[k] if k in row.keys() else d)
    campaign_id = get("campaign_id")
    if not campaign_id or int(get("is_test") or 0):
        return None
    reserve = get("reserve")
    if reserve is None or float(reserve) <= 0:
        reserve = float(get("price") or 0) / 1000.0
    return {
        "bid_id": str(get("bid_id") or ""),
        "campaign_id": str(campaign_id),
        "line_item_id": str(get("line_item_id") or ""),
        "advertiser_id": str(get("advertiser_id") or ""),
        "reserve": float(reserve),
        "business_date": str(get("business_date") or business_date_str()),
        "has_total": bool(int(get("has_total") or 0)),
        "has_li": bool(int(get("has_li") or 0)),
    }


async def gate_and_reserve(
    campaign_id: str,
    li_id: str,
    cost: float,
    camp_budget: float,
    li_budget: float,
    device_id: str = "",
    freq_cap: int = 0,
    caps: list[tuple[int, int]] | None = None,
    *,
    bid_id: str = "",
    advertiser_id: str = "",
    adv_balance: float = 0.0,
    total_remaining: float = 0.0,
    has_total: bool = False,
    reserve_ttl: int = DEFAULT_RESERVE_TTL,
) -> bool:
    """Bid-time gate in a single Redis roundtrip.

    Checks up to 3 frequency-cap windows (read-only) and atomically reserves
    `cost` on the campaign and line-item daily-spend keys and — when
    `advertiser_id` is given — on the advertiser's available balance and the
    campaign's total-budget remaining. All-or-nothing. With `bid_id` the bid
    is tracked in pending:bids until win/loss/expire (bidder-go sweeper).
    `caps` is a list of (impressions, period_hours); the legacy `freq_cap`
    (24h) is used when `caps` is empty.
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
    ttl = _daily_key_ttl()
    day = business_date_str()
    adv = advertiser_id or "-"
    import time as _time

    ok = await r.eval(
        _GATE_LUA, 10,
        *keys,
        spend_key("camp", campaign_id, day),
        spend_key("li", li_id, day),
        avail_adv_key(adv), avail_camp_key(campaign_id), res_adv_key(adv), res_camp_key(campaign_id),
        PENDING_BIDS_KEY,
        *cap_args, str(cost), str(camp_budget), str(li_budget), str(ttl),
        str(adv_balance) if advertiser_id else "",
        str(total_remaining), "1" if (has_total and advertiser_id) else "0",
        bid_id or "", str(int(_time.time()) + int(reserve_ttl)),
    )
    return bool(ok)


async def release_bid(res: dict, state: str = "loss") -> bool:
    """Terminal state loss|expired + refund of the whole reservation, atomically.
    Returns False when the bid already had a terminal state (nothing moved)."""
    if not res or not res.get("campaign_id"):
        return False
    r = get_redis()
    out = await r.eval(
        _RELEASE_BID_LUA, 8,
        *_reservation_keys(res["bid_id"], res["campaign_id"], res.get("line_item_id", ""),
                           res.get("advertiser_id", ""), res.get("business_date") or business_date_str()),
        state, str(res["reserve"]), res["bid_id"], str(BID_STATE_TTL),
        "1" if res.get("has_total") else "0", "1" if res.get("has_li") else "0",
    )
    return int(out) == 1


async def settle_win(res: dict, clearing_cost: float, *, assume_released: bool = False) -> int:
    """Reservation -> clearing price. 0 duplicate, 1 converted, 2 re-charged
    (bid had been released by loss/expire before the win arrived)."""
    r = get_redis()
    out = await r.eval(
        _SETTLE_WIN_LUA, 8,
        *_reservation_keys(res["bid_id"], res["campaign_id"], res.get("line_item_id", ""),
                           res.get("advertiser_id", ""), res.get("business_date") or business_date_str()),
        str(res["reserve"]), str(clearing_cost), res["bid_id"], str(BID_STATE_TTL),
        "1" if res.get("has_total") else "0", "1" if res.get("has_li") else "0",
        str(_daily_key_ttl()), "1" if assume_released else "0",
    )
    return int(out)


async def adjust_available_balance(advertiser_id: str, delta: float) -> None:
    """Deposit/adjust/refund: move avail:adv by delta if the key exists.
    A missing key is seeded by the next bid from the feed snapshot, which
    already contains the new DB balance."""
    r = get_redis()
    key = avail_adv_key(advertiser_id)
    if await r.exists(key):
        await r.incrbyfloat(key, delta)


async def reset_available(advertiser_id: str | None = None, campaign_id: str | None = None) -> None:
    """Drop avail:* so the next bid re-seeds from the feed (after DB-side
    edits such as total_budget change or reconcile)."""
    r = get_redis()
    keys = []
    if advertiser_id:
        keys.append(avail_adv_key(advertiser_id))
    if campaign_id:
        keys.append(avail_camp_key(campaign_id))
    if keys:
        await r.delete(*keys)


async def clear_stale_daily_spend(today: str | None = None) -> int:
    """Delete spend counters of PREVIOUS business days (and legacy undated keys).

    Safe to run at any time and any number of times: the current day's keys
    are never touched, so a late or repeated cron cannot re-open a budget.
    """
    r = get_redis()
    today = today or business_date_str()
    deleted = 0
    async for key in r.scan_iter(match="spend:*", count=200):
        day = spend_key_date(key)
        if day is None or day < today:
            await r.delete(key)
            deleted += 1
    return deleted


async def clear_daily_spend() -> int:
    """Backwards-compatible name; only stale days are cleared (see above)."""
    return await clear_stale_daily_spend()


async def release_budget(scope: str, entity_id: str, amount: float, day: str | None = None) -> None:
    """Refund a reservation (e.g. on loss) on the key of the bid's business day."""
    r = get_redis()
    key = spend_key(scope, entity_id, day)
    if await r.exists(key):
        await r.incrbyfloat(key, -amount)


async def get_spend(scope: str, entity_id: str, day: str | None = None) -> float:
    r = get_redis()
    val = await r.get(spend_key(scope, entity_id, day))
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
