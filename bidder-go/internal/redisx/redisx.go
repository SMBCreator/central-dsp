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

	gateScript    *redis.Script
	releaseScript *redis.Script
	qpsScript     *redis.Script
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
		gateScript:    redis.NewScript(luaGateAndReserve),
		releaseScript: redis.NewScript(luaReleaseGate),
		qpsScript:     redis.NewScript(luaQPSCheck),
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
// Bid-time gate (port of cache.py _GATE_LUA — 1:1).
//
// Единственный источник истины по бюджету — ключи spend:camp:/spend:li:,
// общие с Python (cache.py). Старый порт BUDGET_RESERVE_LUA на ключах
// budget:{camp}:{day} удалён: backend их не читал, а «резерв» в них
// никогда не совпадал с фактическим расходом.
//
// KEYS[1..3]: freq:{campaign_id}:{hours}h:{device_id} (pad-keys when <3 caps)
// KEYS[4]:    spend:camp:{campaign_id}:{business_date}
// KEYS[5]:    spend:li:{li_id}:{business_date}
// ARGV[1..3]: cap limits aligned with KEYS[1..3] (0 = window disabled)
// ARGV[4]:    cost (RUB, float) — CPM/1000 for one impression
// ARGV[5]:    camp daily budget (RUB)
// ARGV[6]:    line-item daily budget (RUB, 0 = unlimited)
// ARGV[7]:    TTL seconds until Europe/Moscow midnight + grace (GC only)
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

// ---------------------------------------------------------------------------
// Business day (mirror of backend/business_day.py, audit M-04).
//
// Дневные бюджеты живут по календарному дню Europe/Moscow. Дата бизнес-дня
// входит в имя ключа spend:*:{YYYY-MM-DD}: граница дня — это смена даты, а не
// момент запуска cron или истечение TTL. TTL — только сборка мусора (до
// следующей полуночи МСК + grace), поэтому опоздавший loss возвращает резерв
// в ключ своего дня и никогда не уменьшает расход нового дня.
// ---------------------------------------------------------------------------

// keyGraceSeconds keeps yesterday's counter alive for late notices; same
// value as business_day.KEY_GRACE_SECONDS.
const keyGraceSeconds = 3600

var businessLoc = loadBusinessLocation()

func loadBusinessLocation() *time.Location {
	name := "Europe/Moscow"
	if loc, err := time.LoadLocation(name); err == nil {
		return loc
	}
	// Image without tzdata: Moscow has had no DST since 2014, fixed +03:00 is exact.
	return time.FixedZone("MSK", 3*3600)
}

// BusinessDate returns the YYYY-MM-DD business date of now.
func BusinessDate(now time.Time) string {
	return now.In(businessLoc).Format("2006-01-02")
}

// secondsUntilBusinessMidnight mirrors business_day.seconds_until_business_midnight.
func secondsUntilBusinessMidnight(now time.Time) int64 {
	local := now.In(businessLoc)
	next := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, businessLoc).AddDate(0, 0, 1)
	s := int64(next.Sub(local).Seconds())
	if s < 60 {
		s = 60
	}
	return s + keyGraceSeconds
}

// SpendKey builds the dated daily spend key. scope: "camp" | "li".
func SpendKey(scope, entityID, day string) string {
	return "spend:" + scope + ":" + entityID + ":" + day
}

// GateAndReserve is the 1:1 port of cache.py gate_and_reserve: checks up to
// three frequency-cap windows (read-only) and atomically reserves cost RUB on
// both spend:camp:{campaignID}:{day} and spend:li:{liID}:{day}. Returns allowed.
func (c *Client) GateAndReserve(ctx context.Context, campaignID, liID string, cost, campBudget, liBudget float64, deviceID string, caps []CapWindow) (bool, error) {
	return c.GateAndReserveAt(ctx, time.Now(), campaignID, liID, cost, campBudget, liBudget, deviceID, caps)
}

// GateAndReserveAt is GateAndReserve with an explicit clock (tests, midnight edge).
func (c *Client) GateAndReserveAt(ctx context.Context, now time.Time, campaignID, liID string, cost, campBudget, liBudget float64, deviceID string, caps []CapWindow) (bool, error) {
	if campBudget <= 0 {
		return false, nil
	}
	day := BusinessDate(now)
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
	keys = append(keys, SpendKey("camp", campaignID, day), SpendKey("li", liID, day))
	args := []interface{}{
		capArgs[0], capArgs[1], capArgs[2],
		strconv.FormatFloat(cost, 'f', -1, 64),
		strconv.FormatFloat(campBudget, 'f', -1, 64),
		strconv.FormatFloat(liBudget, 'f', -1, 64),
		strconv.FormatInt(secondsUntilBusinessMidnight(now), 10),
	}
	res, err := c.gateScript.Run(ctx, c.rdb, keys, args...).Int64()
	if err != nil {
		return false, fmt.Errorf("redisx: gate_and_reserve: %w", err)
	}
	return res == 1, nil
}

// luaReleaseGate refunds a reservation symmetrically to luaGateAndReserve.
//
// A refund must never create or drive a counter negative:
//   - spend:li does not exist when the line item has no daily budget
//     (gate skips it), so a blind INCRBYFLOAT would accumulate negative
//     spend and let the gate overspend once a budget is set later that day;
//   - a loss arriving after UTC midnight (key expired) must not seed the
//     new day's counter with a negative value.
//
// Hence: refund only existing keys and clamp at 0, keeping the TTL.
const luaReleaseGate = `
local cost = tonumber(ARGV[1])
for i = 1, #KEYS do
  if redis.call('EXISTS', KEYS[i]) == 1 then
    local v = tonumber(redis.call('INCRBYFLOAT', KEYS[i], -cost))
    if v < 0 then
      local ttl = redis.call('TTL', KEYS[i])
      if ttl > 0 then
        redis.call('SET', KEYS[i], '0', 'EX', ttl)
      else
        redis.call('SET', KEYS[i], '0')
      end
    end
  end
end
return 1
`

// ReleaseGate refunds a gate reservation on spend:camp and spend:li after a
// lost auction / expired pending bid. `day` is the business date the bid was
// reserved on (from bidmeta); empty means "today". See luaReleaseGate for the
// invariants.
func (c *Client) ReleaseGate(ctx context.Context, campaignID, liID string, cost float64, day string) error {
	if cost <= 0 || campaignID == "" {
		return nil
	}
	if day == "" {
		day = BusinessDate(time.Now())
	}
	keys := []string{SpendKey("camp", campaignID, day)}
	if liID != "" {
		keys = append(keys, SpendKey("li", liID, day))
	}
	if err := c.releaseScript.Run(ctx, c.rdb, keys, strconv.FormatFloat(cost, 'f', -1, 64)).Err(); err != nil {
		return fmt.Errorf("redisx: release_gate: %w", err)
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
