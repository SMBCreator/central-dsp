"""Redis client (Upstash) for budget guard, frequency capping, pacing."""
import os
from typing import NamedTuple

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


# One roundtrip for the whole bid-time gate (audit M-05/M-06/R-06): frequency
# cap check (won impressions + bids of this device still in flight) plus atomic
# reservation on campaign daily, line-item daily, advertiser available balance
# and campaign total-budget remaining, and registration of the bid in
# pending:bids and freqinf:{camp}:{device}. Byte-identical to bidder-go
# luaGateAndReserve (tests/test_redis_contracts.py) — key layout documented there.
_GATE_LUA = """
local cost        = tonumber(ARGV[4])
local camp_budget = tonumber(ARGV[5])
local li_budget   = tonumber(ARGV[6])
local ttl         = tonumber(ARGV[7])
local has_adv     = ARGV[8] ~= ''
local has_total   = ARGV[10] == '1'
local track_freq  = ARGV[11] ~= '' and (tonumber(ARGV[1]) > 0 or tonumber(ARGV[2]) > 0 or tonumber(ARGV[3]) > 0)
local inflight    = 0
local won_expired = 0
if track_freq then
  local overdue = redis.call('ZRANGEBYSCORE', KEYS[11], '-inf', ARGV[13])
  for _, m in ipairs(overdue) do
    if string.sub(m, 1, 2) == 'w:' then won_expired = won_expired + 1 end
  end
  if #overdue > 0 then redis.call('ZREMRANGEBYSCORE', KEYS[11], '-inf', ARGV[13]) end
  inflight = redis.call('ZCARD', KEYS[11])
end
for i = 1, 3 do
  local cap = tonumber(ARGV[i])
  if cap > 0 then
    local seen = tonumber(redis.call('GET', KEYS[i]) or '0')
    if seen + inflight >= cap then return {0, won_expired} end
  end
end
local function rollback(stage)
  if stage >= 4 then redis.call('INCRBYFLOAT', KEYS[7], cost) end
  if stage >= 3 then redis.call('INCRBYFLOAT', KEYS[6], cost) end
  if stage >= 2 and li_budget > 0 then redis.call('INCRBYFLOAT', KEYS[5], -cost) end
  redis.call('INCRBYFLOAT', KEYS[4], -cost)
  return {0, won_expired}
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
if track_freq then
  redis.call('ZADD', KEYS[11], ARGV[12], ARGV[11])
  redis.call('EXPIRE', KEYS[11], 600)
end
return {1, won_expired}
"""

# Terminal states per bid (mirror of bidder-go luaReleaseBid / luaSettleWin).
_RELEASE_BID_LUA = """
local reserve = tonumber(ARGV[2])
redis.call('ZREM', KEYS[9], ARGV[3], 'w:' .. ARGV[3])
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
local windows = 0
for i = 1, 3 do if tonumber(ARGV[8 + i]) > 0 then windows = windows + 1 end end
if windows > 0 then
  redis.call('ZREM', KEYS[9], ARGV[3])
  if ARGV[13] == '1' then
    for i = 1, 3 do
      local hours = tonumber(ARGV[8 + i])
      if hours > 0 then
        redis.call('INCR', KEYS[9 + i])
        if redis.call('TTL', KEYS[9 + i]) < 0 then redis.call('EXPIRE', KEYS[9 + i], hours * 3600) end
      end
    end
  else
    redis.call('ZADD', KEYS[9], ARGV[12], 'w:' .. ARGV[3])
    redis.call('EXPIRE', KEYS[9], 600)
  end
end
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

# imp pixel confirms the impression: reservation -> counters (bidder-go luaConfirmImp).
# Returns 0 nothing counted | 1 counted, reservation dropped | 3 counted, but no
# reservation existed (pixel after WIN_IMP_TTL or without a prior win).
_CONFIRM_IMP_LUA = """
local removed = redis.call('ZREM', KEYS[1], ARGV[1], 'w:' .. ARGV[1])
if ARGV[5] == '1' then return 0 end
local counted = 0
for i = 1, 3 do
  local hours = tonumber(ARGV[1 + i])
  if hours > 0 then
    redis.call('INCR', KEYS[1 + i])
    if redis.call('TTL', KEYS[1 + i]) < 0 then redis.call('EXPIRE', KEYS[1 + i], hours * 3600) end
    counted = 1
  end
end
if counted == 1 and removed == 0 then return 3 end
return counted
"""

PENDING_BIDS_KEY = "pending:bids"
DEFAULT_RESERVE_TTL = 90          # seconds a reservation waits for win/loss (bidder-go DefaultReserveTTL)
BID_STATE_TTL = 2 * 3600          # bidstate:{bid} lifetime (bidder-go BidStateTTL)
MAX_CAP_WINDOWS = 3               # freq windows per bid (bidder-go MaxCapWindows)
DEFAULT_WIN_IMP_TTL = 120         # seconds a won bid holds its freq-cap reservation waiting for imp (bidder-go DefaultWinImpTTL)


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        return default
    return value if value > 0 else default


# Env WIN_IMP_TTL: must be identical for backend and bidder-go (redisx.WinImpTTL);
# raise it when the runbook p95 win->imp query shows pixels slower than the TTL.
WIN_IMP_TTL = _env_positive_int("WIN_IMP_TTL", DEFAULT_WIN_IMP_TTL)


class ImpConfirm(NamedTuple):
    """Outcome of confirm_imp (bidder-go redisx.ImpConfirm)."""
    counted: bool   # window counters were incremented
    late: bool      # no reservation existed: pixel after WIN_IMP_TTL or without a prior win


def freq_key(campaign_id: str, hours: int, device_id: str) -> str:
    """Won-impression counter of one window (bidder-go FreqKey)."""
    return f"freq:{campaign_id}:{int(hours)}h:{device_id or '-'}"


def freq_inflight_key(campaign_id: str, device_id: str) -> str:
    """ZSET of this device's bids awaiting win/loss (bidder-go FreqInflightKey, R-06)."""
    return f"freqinf:{campaign_id}:{device_id or '-'}"


def active_caps(caps) -> list[tuple[int, int]]:
    """Valid (limit, hours) windows, at most MAX_CAP_WINDOWS (bidder-go activeCaps)."""
    out: list[tuple[int, int]] = []
    for w in caps or []:
        try:
            limit, hours = int(w[0]), int(w[1])
        except (TypeError, ValueError, IndexError):
            continue
        if limit > 0 and hours > 0 and len(out) < MAX_CAP_WINDOWS:
            out.append((limit, hours))
    return out


def _freq_window_keys(campaign_id: str, device_id: str, caps) -> tuple[list[str], list[int]]:
    """Exactly MAX_CAP_WINDOWS window keys + their hours (0 on pad keys and
    when the bid has no device) — bidder-go freqWindowKeys."""
    keys, hours = [], []
    for _limit, h in active_caps(caps):
        keys.append(freq_key(campaign_id, h, device_id))
        hours.append(h if device_id else 0)
    dev = device_id or "-"
    while len(keys) < MAX_CAP_WINDOWS:
        keys.append(f"freq:{campaign_id}:pad{len(keys)}:{dev}")
        hours.append(0)
    return keys, hours


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


def _reservation_keys(res: dict) -> list[str]:
    """KEYS of luaReleaseBid/luaSettleWin (bidder-go BidReservation.keys)."""
    campaign_id = res["campaign_id"]
    adv = res.get("advertiser_id") or "-"
    day = res.get("business_date") or business_date_str()
    device_id = res.get("device_id") or ""
    freq_keys, _ = _freq_window_keys(campaign_id, device_id, res.get("caps"))
    return [
        bid_state_key(res["bid_id"]), PENDING_BIDS_KEY,
        spend_key("camp", campaign_id, day), spend_key("li", res.get("line_item_id") or "", day),
        avail_adv_key(adv), avail_camp_key(campaign_id), res_adv_key(adv), res_camp_key(campaign_id),
        freq_inflight_key(campaign_id, device_id),
        *freq_keys,
    ]


def reservation_from_bidmeta(row) -> dict | None:
    """Extract the reservation description bidder-go/Python wrote into bidmeta.

    Returns None for test bids and rows without a campaign. Legacy rows
    (before audit M-06) have no `reserve`: fall back to price/1000 and no
    advertiser constraint, which matches what the old gate reserved.
    `fdev`/`fcaps` (R-06) carry the device and cap windows so that settle_win
    counts the won impression; rows without them (PG fallback) count nothing.
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
        "device_id": str(get("fdev") or ""),
        "caps": active_caps(get("fcaps") or []),
        "count_on_win": bool(int(get("fwin") or 0)),
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

    Checks up to 3 frequency-cap windows — confirmed impressions (freq:*)
    plus this device's active reservations (freqinf: bids in flight and wins
    awaiting the imp pixel, audit R-06) — and
    atomically reserves `cost` on the campaign and line-item daily-spend keys
    and — when `advertiser_id` is given — on the advertiser's available balance
    and the campaign's total-budget remaining. All-or-nothing. With `bid_id`
    the bid is tracked in pending:bids and freqinf until win/loss/expire.
    `caps` is a list of (impressions, period_hours); the legacy `freq_cap`
    (24h) is used when `caps` is empty.
    """
    if camp_budget <= 0:
        return False
    windows = active_caps(caps)
    if not windows and freq_cap:
        windows = [(int(freq_cap), 24)]
    keys, _hours = _freq_window_keys(campaign_id, device_id, windows)
    cap_args = [str(cap_n if device_id else 0) for cap_n, _h in windows]
    while len(cap_args) < MAX_CAP_WINDOWS:
        cap_args.append("0")
    r = get_redis()
    ttl = _daily_key_ttl()
    day = business_date_str()
    adv = advertiser_id or "-"
    import time as _time

    now = int(_time.time())
    out = await r.eval(
        _GATE_LUA, 11,
        *keys,
        spend_key("camp", campaign_id, day),
        spend_key("li", li_id, day),
        avail_adv_key(adv), avail_camp_key(campaign_id), res_adv_key(adv), res_camp_key(campaign_id),
        PENDING_BIDS_KEY, freq_inflight_key(campaign_id, device_id),
        *cap_args, str(cost), str(camp_budget), str(li_budget), str(ttl),
        str(adv_balance) if advertiser_id else "",
        str(total_remaining), "1" if (has_total and advertiser_id) else "0",
        bid_id or "", str(now + int(reserve_ttl)), str(now),
    )
    allowed, won_expired = (int(out[0]), int(out[1])) if isinstance(out, (list, tuple)) else (int(out), 0)
    if won_expired:
        try:
            from obs_metrics import inc as _obs_inc
            _obs_inc("rtb_freq_win_without_imp_total", won_expired)
        except Exception:
            pass
    return allowed == 1


async def release_bid(res: dict, state: str = "loss") -> bool:
    """Terminal state loss|expired + refund of the whole reservation, atomically.
    Returns False when the bid already had a terminal state (nothing moved)."""
    if not res or not res.get("campaign_id"):
        return False
    r = get_redis()
    out = await r.eval(
        _RELEASE_BID_LUA, 12,
        *_reservation_keys(res),
        state, str(res["reserve"]), res["bid_id"], str(BID_STATE_TTL),
        "1" if res.get("has_total") else "0", "1" if res.get("has_li") else "0",
    )
    return int(out) == 1


async def settle_win(res: dict, clearing_cost: float, *, assume_released: bool = False) -> int:
    """Reservation -> clearing price. 0 duplicate, 1 converted, 2 re-charged
    (bid had been released by loss/expire before the win arrived).

    Frequency cap (R-06): a win notice is not an impression. The first win
    turns the bid's in-flight freqinf member into a "w:{bid}" reservation
    that expires after WIN_IMP_TTL unless confirm_imp commits it to the
    freq:* counters. Partners with res["count_on_win"] (no client pixel)
    commit right here.
    """
    import time as _time

    r = get_redis()
    _keys, hours = _freq_window_keys(res["campaign_id"], res.get("device_id") or "", res.get("caps"))
    out = await r.eval(
        _SETTLE_WIN_LUA, 12,
        *_reservation_keys(res),
        str(res["reserve"]), str(clearing_cost), res["bid_id"], str(BID_STATE_TTL),
        "1" if res.get("has_total") else "0", "1" if res.get("has_li") else "0",
        str(_daily_key_ttl()), "1" if assume_released else "0",
        *[str(h) for h in hours],
        str(int(_time.time()) + WIN_IMP_TTL), "1" if res.get("count_on_win") else "0",
    )
    return int(out)


async def confirm_imp(res: dict) -> ImpConfirm:
    """The imp pixel confirms the impression (R-06): drop the bid's freqinf
    reservation and increment every window counter of res["caps"] for
    res["device_id"]. Idempotency is the caller's (bid_event_once). `late`
    tells the caller the reservation had already expired (or never existed)
    when the pixel arrived — the impression still counts, but the slot was
    free in the meantime. No-op without cap windows."""
    caps = active_caps(res.get("caps"))
    if not caps:
        return ImpConfirm(False, False)
    campaign_id = res["campaign_id"]
    device_id = res.get("device_id") or ""
    keys, hours = _freq_window_keys(campaign_id, device_id, caps)
    r = get_redis()
    out = await r.eval(
        _CONFIRM_IMP_LUA, 1 + MAX_CAP_WINDOWS,
        freq_inflight_key(campaign_id, device_id), *keys,
        res["bid_id"], *[str(h) for h in hours],
        "1" if res.get("count_on_win") else "0",
    )
    code = int(out)
    return ImpConfirm(counted=bool(code & 1), late=bool(code & 2))


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
# Lifecycle (audit R-06): gate_and_reserve takes an in-flight slot →
# settle_win turns it into a win reservation with WIN_IMP_TTL → confirm_imp
# commits it to the freq:* window counters (or release_bid / the gate prune
# drops it). Gate formula: confirmed + active_reservations < cap.


async def get_frequency(campaign_id: str, device_id: str, hours: int) -> int:
    """Won impressions of a device in one window (diagnostics/tests)."""
    r = get_redis()
    val = await r.get(freq_key(campaign_id, hours, device_id))
    return int(val or 0)


async def get_frequency_inflight(campaign_id: str, device_id: str) -> int:
    """Bids of a device still awaiting win/loss (diagnostics/tests)."""
    r = get_redis()
    return int(await r.zcard(freq_inflight_key(campaign_id, device_id)) or 0)


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
