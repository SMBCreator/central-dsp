// Package redisx provides Redis-backed atomic budget, frequency-cap and QPS
// primitives for the bidder hot path.
//
// The Lua scripts are 1:1 ports of backend/cache.py so that the Go bidder and
// the Python control plane operate on the same keys with identical semantics.
package redisx

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// Client wraps a go-redis client with the bidder's atomic scripts.
type Client struct {
	rdb *redis.Client

	budgetScript  *redis.Script
	gateScript    *redis.Script
	qpsScript     *redis.Script
	releaseScript *redis.Script
}

// New creates a Client from a redis URL (redis://host:port/db).
func New(url string) (*Client, error) {
	opt, err := redis.ParseURL(url)
	if err != nil {
		return nil, fmt.Errorf("redisx: parse url: %w", err)
	}
	// Hot-path tuning: fail fast, never queue for seconds.
	opt.DialTimeout = 500 * time.Millisecond
	opt.ReadTimeout = 150 * time.Millisecond
	opt.WriteTimeout = 150 * time.Millisecond
	opt.PoolSize = 64
	opt.MinIdleConns = 8
	return &Client{
		rdb:           redis.NewClient(opt),
		budgetScript:  redis.NewScript(luaBudgetReserve),
		gateScript:    redis.NewScript(luaGateAndReserve),
		qpsScript:     redis.NewScript(luaQPSCheck),
		releaseScript: redis.NewScript(luaBudgetRelease),
	}, nil
}

// Raw exposes the underlying client (feed cache, pending bids, clid bridge).
func (c *Client) Raw() *redis.Client { return c.rdb }

// Ping verifies connectivity.
func (c *Client) Ping(ctx context.Context) error { return c.rdb.Ping(ctx).Err() }

// Close closes the pool.
func (c *Client) Close() error { return c.rdb.Close() }

// ---------------------------------------------------------------------------
// Event stream (bid events -> backend consumer)
//
// Go-биддер не пишет в Postgres: каждое событие «bid» кладётся в Redis Stream,
// а backend (events_consumer.py, группа "backend") переносит его в bid_events и
// stats_hourly. Без этого win-rate/бид-статистика по Go-трафику была бы пустой.

// EventStreamKey — имя стрима; должно совпадать с backend/events_consumer.py.
const EventStreamKey = "rtb:events"

// eventStreamMaxLen ограничивает стрим (~200k событий ≈ несколько минут
// пикового трафика), чтобы лежащий consumer не съел память Redis.
const eventStreamMaxLen = 200_000

// EmitEvent добавляет событие в стрим. Fire-and-forget: ошибка логируется
// вызывающей стороной, торги не блокируются.
func (c *Client) EmitEvent(ctx context.Context, fields map[string]any) error {
	return c.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: EventStreamKey,
		MaxLen: eventStreamMaxLen,
		Approx: true,
		Values: fields,
	}).Err()
}

// ---------------------------------------------------------------------------
// Budget reservation (port of cache.py BUDGET_RESERVE_LUA)
//
// Keys: budget:{campaign_id}:{YYYYMMDD} (daily spent, micros)
//       budget_total:{campaign_id}     (lifetime spent, micros)
// Args: amount_micros, daily_limit_micros (0 = unlimited),
//       total_limit_micros (0 = unlimited), ttl_seconds
// Returns 1 if reserved, 0 if either cap would be exceeded.
// ---------------------------------------------------------------------------

const luaBudgetReserve = `
local daily_key = KEYS[1]
local total_key = KEYS[2]
local amount = tonumber(ARGV[1])
local daily_limit = tonumber(ARGV[2])
local total_limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local daily_spent = tonumber(redis.call('GET', daily_key) or '0')
local total_spent = tonumber(redis.call('GET', total_key) or '0')

if daily_limit > 0 and daily_spent + amount > daily_limit then
  return 0
end
if total_limit > 0 and total_spent + amount > total_limit then
  return 0
end

local new_daily = redis.call('INCRBY', daily_key, amount)
if new_daily == amount then
  redis.call('EXPIRE', daily_key, ttl)
end
redis.call('INCRBY', total_key, amount)
return 1
`

// ReserveBudget atomically reserves amountMicros against daily/total caps.
func (c *Client) ReserveBudget(ctx context.Context, campaignID string, day string, amountMicros, dailyLimitMicros, totalLimitMicros int64) (bool, error) {
	keys := []string{
		fmt.Sprintf("budget:%s:%s", campaignID, day),
		fmt.Sprintf("budget_total:%s", campaignID),
	}
	res, err := c.budgetScript.Run(ctx, c.rdb, keys,
		amountMicros, dailyLimitMicros, totalLimitMicros, 172800).Int64()
	if err != nil {
		return false, fmt.Errorf("redisx: budget reserve: %w", err)
	}
	return res == 1, nil
}

// ---------------------------------------------------------------------------
// Budget release (port of cache.py BUDGET_RELEASE_LUA) — used on loss/expiry.
// ---------------------------------------------------------------------------

const luaBudgetRelease = `
local daily_key = KEYS[1]
local total_key = KEYS[2]
local amount = tonumber(ARGV[1])

local daily = tonumber(redis.call('GET', daily_key) or '0')
if daily >= amount then
  redis.call('DECRBY', daily_key, amount)
else
  redis.call('SET', daily_key, '0', 'KEEPTTL')
end
local total = tonumber(redis.call('GET', total_key) or '0')
if total >= amount then
  redis.call('DECRBY', total_key, amount)
else
  redis.call('SET', total_key, '0')
end
return 1
`

// ReleaseBudget returns a previously reserved amount (bid lost / expired).
func (c *Client) ReleaseBudget(ctx context.Context, campaignID string, day string, amountMicros int64) error {
	keys := []string{
		fmt.Sprintf("budget:%s:%s", campaignID, day),
		fmt.Sprintf("budget_total:%s", campaignID),
	}
	if err := c.releaseScript.Run(ctx, c.rdb, keys, amountMicros).Err(); err != nil {
		return fmt.Errorf("redisx: budget release: %w", err)
	}
	return nil
}

// ---------------------------------------------------------------------------
// Bid-time gate (port of cache.py _GATE_LUA — 1:1).
//
// KEYS[1..3]: freq:{campaign_id}:{hours}h:{device_id} (pad-keys when <3 caps)
// KEYS[4]:    spend:camp:{campaign_id}
// KEYS[5]:    spend:li:{li_id}
// ARGV[1..3]: cap limits aligned with KEYS[1..3] (0 = window disabled)
// ARGV[4]:    cost (RUB, float) — CPM/1000 for one impression
// ARGV[5]:    camp daily budget (RUB)
// ARGV[6]:    line-item daily budget (RUB, 0 = unlimited)
// ARGV[7]:    TTL seconds until UTC midnight (matches clear_daily_spend cron)
//
// Frequency windows are checked READ-ONLY here: the counters are incremented
// by the backend on a confirmed impression (/rtb/imp reads bidfreq:{bid_id}),
// so lost auctions never consume the cap — same semantics as Python.
// ---------------------------------------------------------------------------

const luaGateAndReserve = `
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
`

// CapWindow is one frequency-cap window: at most Limit impressions per
// PeriodHours hours. Serialized to bidfreq as a [limit, hours] tuple.
type CapWindow struct {
	Limit       int
	PeriodHours int
}

// MarshalJSON encodes the window as the [limit, hours] tuple Python expects.
func (w CapWindow) MarshalJSON() ([]byte, error) {
	return []byte(fmt.Sprintf("[%d,%d]", w.Limit, w.PeriodHours)), nil
}

// UnmarshalJSON accepts both tuple ([limit, hours]) and legacy object forms.
func (w *CapWindow) UnmarshalJSON(b []byte) error {
	var tup [2]int
	if err := json.Unmarshal(b, &tup); err == nil {
		w.Limit, w.PeriodHours = tup[0], tup[1]
		return nil
	}
	var obj struct {
		Impressions int `json:"impressions"`
		PeriodHours int `json:"period_hours"`
	}
	if err := json.Unmarshal(b, &obj); err != nil {
		return err
	}
	w.Limit, w.PeriodHours = obj.Impressions, obj.PeriodHours
	return nil
}

// secondsUntilUTCMidnight mirrors cache.py _seconds_until_utc_midnight.
func secondsUntilUTCMidnight(now time.Time) int64 {
	utc := now.UTC()
	next := time.Date(utc.Year(), utc.Month(), utc.Day(), 0, 0, 0, 0, time.UTC).Add(24 * time.Hour)
	s := int64(next.Sub(utc).Seconds())
	if s < 60 {
		return 60
	}
	return s
}

// GateAndReserve is the 1:1 port of cache.py gate_and_reserve: checks up to
// three frequency-cap windows (read-only) and atomically reserves cost RUB on
// both spend:camp:{campaignID} and spend:li:{liID}. Returns allowed.
func (c *Client) GateAndReserve(ctx context.Context, campaignID, liID string, cost, campBudget, liBudget float64, deviceID string, caps []CapWindow) (bool, error) {
	if campBudget <= 0 {
		return false, nil
	}
	dev := deviceID
	if dev == "" {
		dev = "-"
	}
	keys := make([]string, 0, 5)
	capArgs := make([]string, 0, 3)
	for _, w := range caps {
		if w.Limit <= 0 || w.PeriodHours <= 0 || len(keys) >= 3 {
			continue
		}
		keys = append(keys, fmt.Sprintf("freq:%s:%dh:%s", campaignID, w.PeriodHours, dev))
		limit := w.Limit
		if deviceID == "" {
			limit = 0
		}
		capArgs = append(capArgs, strconv.Itoa(limit))
	}
	for len(keys) < 3 {
		keys = append(keys, fmt.Sprintf("freq:%s:pad%d:%s", campaignID, len(keys), dev))
		capArgs = append(capArgs, "0")
	}
	keys = append(keys, "spend:camp:"+campaignID, "spend:li:"+liID)
	args := []interface{}{
		capArgs[0], capArgs[1], capArgs[2],
		strconv.FormatFloat(cost, 'f', -1, 64),
		strconv.FormatFloat(campBudget, 'f', -1, 64),
		strconv.FormatFloat(liBudget, 'f', -1, 64),
		strconv.FormatInt(secondsUntilUTCMidnight(time.Now()), 10),
	}
	res, err := c.gateScript.Run(ctx, c.rdb, keys, args...).Int64()
	if err != nil {
		return false, fmt.Errorf("redisx: gate_and_reserve: %w", err)
	}
	return res == 1, nil
}

// ReleaseGate refunds a gate reservation on both keys (port of release_budget
// called for camp+li after a lost auction / expired pending bid).
func (c *Client) ReleaseGate(ctx context.Context, campaignID, liID string, cost float64) error {
	if err := c.rdb.IncrByFloat(ctx, "spend:camp:"+campaignID, -cost).Err(); err != nil {
		return err
	}
	if liID != "" {
		return c.rdb.IncrByFloat(ctx, "spend:li:"+liID, -cost).Err()
	}
	return nil
}

// ---------------------------------------------------------------------------
// Per-partner QPS limiting (port of cache.py check_qps — fixed 1s window)
// Key: qps:{partner_id}:{unix_second}
// ---------------------------------------------------------------------------

const luaQPSCheck = `
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local v = redis.call('INCR', key)
if v == 1 then
  redis.call('EXPIRE', key, 2)
end
if v > limit then
  return 0
end
return 1
`

// CheckQPS returns true when the partner is within its per-second budget.
func (c *Client) CheckQPS(ctx context.Context, partnerID string, limit int64, nowUnix int64) (bool, error) {
	if limit <= 0 {
		return true, nil
	}
	key := fmt.Sprintf("qps:%s:%d", partnerID, nowUnix)
	res, err := c.qpsScript.Run(ctx, c.rdb, []string{key}, limit).Int64()
	if err != nil {
		return false, fmt.Errorf("redisx: qps check: %w", err)
	}
	return res == 1, nil
}
