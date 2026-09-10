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
	"math"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// Client wraps a go-redis client with the bidder's atomic scripts.
type Client struct {
	rdb *redis.Client

	gateScript        *redis.Script // luaGateAndReserve
	releaseScript     *redis.Script // luaReleaseBid (loss/expired)
	settleScript      *redis.Script // luaSettleWin
	confirmScript     *redis.Script // luaConfirmImp (imp pixel, R-06)
	releaseGateScript *redis.Script // luaReleaseGate (legacy)
	qpsScript         *redis.Script
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
		rdb:               redis.NewClient(opt),
		gateScript:        redis.NewScript(luaGateAndReserve),
		releaseScript:     redis.NewScript(luaReleaseBid),
		settleScript:      redis.NewScript(luaSettleWin),
		confirmScript:     redis.NewScript(luaConfirmImp),
		releaseGateScript: redis.NewScript(luaReleaseGate),
		qpsScript:         redis.NewScript(luaQPSCheck),
	}, nil
}

// Raw exposes the underlying client (feed cache, pending bids, clid bridge).
func (c *Client) Raw() *redis.Client { return c.rdb }

// Ping verifies connectivity.
func (c *Client) Ping(ctx context.Context) error { return c.rdb.Ping(ctx).Err() }

// Close closes the pool.
func (c *Client) Close() error { return c.rdb.Close() }

// SafeMaxmemoryPolicy — единственная политика, при которой Redis не может
// «забыть» ключ spend:*/bidmeta (audit M-03). Зеркало startup_checks.py.
const SafeMaxmemoryPolicy = "noeviction"

// DurabilityReport — результат CheckDurability.
type DurabilityReport struct {
	MaxmemoryPolicy string
	AppendOnly      bool
	// Fatal — политика допускает eviction: в production торговать нельзя.
	Fatal string
	// Warning — нет AOF: рестарт Redis потеряет расход дня.
	Warning string
}

// CheckDurability опрашивает CONFIG GET maxmemory-policy / appendonly.
// Ошибка запроса (managed Redis без прав на CONFIG) возвращается как err —
// вызывающая сторона решает, считать ли это фатальным.
func (c *Client) CheckDurability(ctx context.Context) (DurabilityReport, error) {
	var rep DurabilityReport
	pol, err := c.rdb.ConfigGet(ctx, "maxmemory-policy").Result()
	if err != nil {
		return rep, fmt.Errorf("redisx: config get maxmemory-policy: %w", err)
	}
	rep.MaxmemoryPolicy = pol["maxmemory-policy"]
	if rep.MaxmemoryPolicy != "" && rep.MaxmemoryPolicy != SafeMaxmemoryPolicy {
		rep.Fatal = "Redis maxmemory-policy=" + rep.MaxmemoryPolicy + ": eviction may drop spend:*/bidmeta keys and reopen exhausted budgets; require " + SafeMaxmemoryPolicy
	}
	if ao, err := c.rdb.ConfigGet(ctx, "appendonly").Result(); err == nil {
		rep.AppendOnly = ao["appendonly"] == "yes"
		if !rep.AppendOnly {
			rep.Warning = "Redis appendonly=no: a restart loses today's spend counters"
		}
	}
	return rep, nil
}

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

// EmitEvent добавляет событие в стрим. Для события bid ошибка фатальна для
// бида (handler отзывает резерв и не отвечает SSP, audit R-02); для loss/win
// событие вторично и ошибка только логируется.
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
// Audit M-05/M-06: помимо дневных бюджетов гейт резервирует ДОСТУПНЫЙ баланс
// рекламодателя и остаток общего бюджета кампании, а каждый бид получает
// per-bid жизненный цикл reserve -> win | loss | expired (см. luaReleaseBid,
// luaSettleWin, pending:bids + sweeper).
//
// Ключи «available» = «сколько ещё можно обязать»: DB-значение минус
// невыполненные резервы. Их меняют только Lua-скрипты этого файла и backend
// (депозит/корректировка, reconcile). Отсутствующий ключ (свежий Redis)
// сеется из снимка фида — консервативно, до ближайшего reconcile.
//
// KEYS[1..3]: freq:{campaign_id}:{hours}h:{device_id} (pad-keys when <3 caps)
// KEYS[4]:    spend:camp:{campaign_id}:{business_date}
// KEYS[5]:    spend:li:{li_id}:{business_date}
// KEYS[6]:    avail:adv:{advertiser_id}   — доступный баланс рекламодателя (RUB)
// KEYS[7]:    avail:camp:{campaign_id}    — остаток общего бюджета кампании (RUB)
// KEYS[8]:    res:adv:{advertiser_id}     — сумма невыполненных резервов
// KEYS[9]:    res:camp:{campaign_id}      — сумма невыполненных резервов (total)
// KEYS[10]:   pending:bids                — ZSET bid_id -> deadline (sweeper)
// KEYS[11]:   freqinf:{campaign_id}:{device_id} — ZSET bid_id -> deadline: биды
//             этого устройства, ещё не получившие win/loss/expire (audit R-06)
// ARGV[1..3]: cap limits aligned with KEYS[1..3] (0 = window disabled)
// ARGV[4]:    cost (RUB, float) — CPM/1000 for one impression
// ARGV[5]:    camp daily budget (RUB)
// ARGV[6]:    line-item daily budget (RUB, 0 = unlimited)
// ARGV[7]:    TTL seconds until Europe/Moscow midnight + grace (GC only)
// ARGV[8]:    advertiser balance snapshot from feed ('' = no advertiser constraint)
// ARGV[9]:    total budget remaining snapshot from feed
// ARGV[10]:   '1' when the campaign has a total budget (ARGV[9] enforced)
// ARGV[11]:   bid_id ('' = do not track in pending:bids / freqinf)
// ARGV[12]:   pending deadline (unix seconds) for the sweeper
// ARGV[13]:   now (unix seconds) — prune of overdue freqinf members
//
// Frequency cap (audit R-06): gate formula is
//     confirmed_impressions (freq:*) + active_reservations (ZCARD freqinf) < cap
// freqinf members: "{bid_id}" — bid in flight (score = reserve deadline);
// "w:{bid_id}" — won, waiting for the imp pixel (score = win + WinImpTTL).
// Lifecycle: bid → in flight (here) → won_reserved (luaSettleWin moves the
// member, NO counter increment: a win notice is not an impression) →
// confirmed (luaConfirmImp: ZREM + INCR freq:*) | lost/expired/aborted
// (luaReleaseBid: ZREM) | expired_no_imp (score prune below). The prune
// returns how many "w:" members it dropped → metric win_without_imp (a
// broken SSP pixel shows up there). Old scheme (read-only counters + backend
// bump on /imp via bidfreq:{bid_id}) let N concurrent bids pass at cap-1.
//
// Returns {allowed(0|1), won_expired}.
// ---------------------------------------------------------------------------

const luaGateAndReserve = `
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
`

// ---------------------------------------------------------------------------
// Per-bid terminal states (audit M-06).
//
// bidstate:{bid_id} = win | loss | expired | aborted — ставится атомарно в том
// же Lua-скрипте, что и движение денег ('aborted' — биддер сам отозвал бид, не
// сумев записать bidmeta/событие, audit R-02), поэтому:
//   * повторный/поздний lurl не возвращает резерв дважды;
//   * loss после win не «размораживает» уже списанные деньги;
//   * win после loss/expired (SSP прислал уведомления не в том порядке или
//     резерв истёк) ДОсписывает clearing price вместо возврата разницы.
//
// KEYS[1]: bidstate:{bid_id}
// KEYS[2]: pending:bids
// KEYS[3]: spend:camp:{campaign_id}:{business_date}
// KEYS[4]: spend:li:{li_id}:{business_date}
// KEYS[5]: avail:adv:{advertiser_id}
// KEYS[6]: avail:camp:{campaign_id}
// KEYS[7]: res:adv:{advertiser_id}
// KEYS[8]: res:camp:{campaign_id}
// KEYS[9]: freqinf:{campaign_id}:{device_id}   — in-flight freq slot (R-06)
// KEYS[10..12]: freq:{campaign_id}:{hours}h:{device_id} — window counters
//               (pad keys when the bid has <3 windows; used by settle only)
//
// luaReleaseBid ARGV: 1 state ('loss'|'expired'|'aborted'), 2 reserve, 3 bid_id,
//                     4 bidstate TTL, 5 has_total, 6 has_li
// Returns 1 when the reservation was refunded now, 0 when the bid already
// had a terminal state (nothing moved). The freqinf slot is dropped in both
// cases: a bid that is not going to win must not hold the cap.
// ---------------------------------------------------------------------------

const luaReleaseBid = `
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
`

// luaSettleWin ARGV: 1 reserve, 2 clearing cost, 3 bid_id, 4 bidstate TTL,
//
//	5 has_total, 6 has_li, 7 spend-key TTL,
//	8 assume_released ('1' when the reservation is known to
//	  be gone — e.g. bid resolved from Postgres after bidmeta expired),
//	9..11 window hours aligned with KEYS[10..12] (0 = pad, no counter),
//	12 win→imp deadline (unix seconds) for the freq reservation,
//	13 '1' when the partner has no client-side pixel (freq_cap_on_win):
//	   the win is the impression, counters are incremented right here
//
// Returns 0 duplicate win, 1 reserve converted to clearing, 2 clearing
// re-charged after a prior loss/expire, 3 re-charged after the pending:burl
// sweep expired the win (late burl, R-07). prev == 'won' (billing_event=burl
// partner: win_pending already did the freq-cap step and kept the reservation)
// converts the reserve when the bid is still in pending:bids, re-charges
// otherwise; KEYS[13] = pending:burl. Frequency cap (R-06): a win notice
// is NOT an impression — the bid's in-flight freqinf member becomes a
// "w:{bid_id}" reservation that expires at ARGV[12] unless luaConfirmImp
// turns it into freq:* counters. Only ARGV[13]='1' partners commit on win.
const luaSettleWin = `
local reserve   = tonumber(ARGV[1])
local clearing  = tonumber(ARGV[2])
local spend_ttl = tonumber(ARGV[7])
local has_total = ARGV[5] == '1'
local has_li    = ARGV[6] == '1'
local prev = redis.call('GET', KEYS[1])
if prev == 'win' then return 0 end
redis.call('SET', KEYS[1], 'win', 'EX', tonumber(ARGV[4]))
local held = redis.call('ZREM', KEYS[2], ARGV[3])
redis.call('ZREM', KEYS[13], ARGV[3])
local pending = prev == 'won' or prev == 'won_expired'
if not pending then
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
local released = ARGV[8] == '1' or (prev and not pending) or (pending and held == 0)
if released then
  charge(KEYS[3], clearing)
  if has_li then charge(KEYS[4], clearing) end
  if redis.call('EXISTS', KEYS[5]) == 1 then redis.call('INCRBYFLOAT', KEYS[5], -clearing) end
  if has_total and redis.call('EXISTS', KEYS[6]) == 1 then redis.call('INCRBYFLOAT', KEYS[6], -clearing) end
  if prev == 'won_expired' then return 3 end
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
`

// luaConfirmImp — the imp pixel confirms the impression (audit R-06).
// KEYS[1]: freqinf:{campaign_id}:{device_id}
// KEYS[2..4]: freq:{campaign_id}:{hours}h:{device_id} (pad keys allowed)
// ARGV[1]: bid_id; ARGV[2..4]: window hours aligned with KEYS[2..4] (0 = pad);
// ARGV[5]: '1' when the counters were already incremented on win
//
//	(freq_cap_on_win partner) — only the reservation is dropped.
//
// Idempotency comes from the caller (bid_event_once(bid_id,'imp')); a pixel
// after the reservation expired or without a prior win still counts: the
// impression happened. Returns 0 nothing counted | 1 counted, reservation
// dropped | 3 counted, but no reservation existed (late pixel — see
// ImpConfirm.Late and rtb_imp_late_after_reservation_total).
const luaConfirmImp = `
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
`

// ImpConfirm — результат luaConfirmImp (Python cache.ImpConfirm).
type ImpConfirm struct {
	Counted bool // счётчики окон инкрементированы
	Late    bool // брони уже не было: пиксель после WinImpTTL или без win
}

// CapWindow is one frequency-cap window: at most Limit impressions per
// PeriodHours hours. Serialized into bidmeta.fcaps as a [limit, hours] tuple.
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

// PacingBurst — доля дневного бюджета «вперёд графика» при pacing=even, чтобы
// выдача не замирала в 00:00 МСК. Зеркало business_day.PACING_BURST.
const PacingBurst = 0.1

// BusinessDayFraction — доля бизнес-дня (Europe/Moscow), прошедшая к now, в [0, 1).
// Зеркало business_day.business_day_fraction: считать по UTC нельзя — тогда
// ровная кампания получила бы 87.5% бюджета в первые минуты бизнес-дня.
func BusinessDayFraction(now time.Time) float64 {
	local := now.In(businessLoc)
	return float64(local.Hour()*3600+local.Minute()*60+local.Second()) / 86400
}

// EvenPacingBudget — эффективный дневной бюджет кампании с pacing=even:
// daily × min(1, fraction + PacingBurst). Зеркало business_day.even_pacing_budget.
func EvenPacingBudget(daily float64, now time.Time) float64 {
	if daily <= 0 {
		return daily
	}
	return daily * math.Min(1.0, BusinessDayFraction(now)+PacingBurst)
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

// Key builders shared with backend/cache.py (contract).
func AvailAdvKey(advertiserID string) string { return "avail:adv:" + advertiserID }
func AvailCampKey(campaignID string) string  { return "avail:camp:" + campaignID }
func ResAdvKey(advertiserID string) string   { return "res:adv:" + advertiserID }
func ResCampKey(campaignID string) string    { return "res:camp:" + campaignID }
func BidStateKey(bidID string) string        { return "bidstate:" + bidID }

// FreqKey — счётчик выигранных показов окна: freq:{camp}:{hours}h:{device}.
func FreqKey(campaignID string, hours int, deviceID string) string {
	if deviceID == "" {
		deviceID = "-"
	}
	return fmt.Sprintf("freq:%s:%dh:%s", campaignID, hours, deviceID)
}

// FreqInflightKey — ZSET бидов устройства, ждущих win/loss (audit R-06).
func FreqInflightKey(campaignID, deviceID string) string {
	if deviceID == "" {
		deviceID = "-"
	}
	return "freqinf:" + campaignID + ":" + deviceID
}

// MaxCapWindows — сколько freq-окон несёт бид (KEYS[1..3] гейта).
const MaxCapWindows = 3

// DefaultWinImpTTL — сколько выигранный бид держит бронь freq cap в ожидании
// imp-пикселя (R-06). Старт 120 с; уточняется по p95 win→imp из bid_events
// (runbook), далее per-format.
const DefaultWinImpTTL = 120 * time.Second

// WinImpTTL — действующее значение: env WIN_IMP_TTL (секунды) либо
// DefaultWinImpTTL. Должно совпадать с backend (cache.WIN_IMP_TTL): обе
// стороны пишут score брони в один freqinf ZSET.
var WinImpTTL = winImpTTLFromEnv()

func winImpTTLFromEnv() time.Duration {
	if raw := strings.TrimSpace(os.Getenv("WIN_IMP_TTL")); raw != "" {
		if sec, err := strconv.Atoi(raw); err == nil && sec > 0 {
			return time.Duration(sec) * time.Second
		}
	}
	return DefaultWinImpTTL
}

// freqWindowKeys — ровно MaxCapWindows ключей окон + часы (0 у pad-ключей).
func freqWindowKeys(campaignID, deviceID string, caps []CapWindow) ([]string, []int) {
	keys := make([]string, 0, MaxCapWindows)
	hours := make([]int, 0, MaxCapWindows)
	for _, w := range caps {
		if w.Limit <= 0 || w.PeriodHours <= 0 || len(keys) >= MaxCapWindows {
			continue
		}
		keys = append(keys, FreqKey(campaignID, w.PeriodHours, deviceID))
		h := w.PeriodHours
		if deviceID == "" {
			h = 0
		}
		hours = append(hours, h)
	}
	dev := deviceID
	if dev == "" {
		dev = "-"
	}
	for len(keys) < MaxCapWindows {
		keys = append(keys, fmt.Sprintf("freq:%s:pad%d:%s", campaignID, len(keys), dev))
		hours = append(hours, 0)
	}
	return keys, hours
}

// PendingBidsKey — ZSET bid_id -> unix deadline; sweeper expires overdue bids.
const PendingBidsKey = "pending:bids"

// PendingBurlKey — ZSET выигранных бидов SSP с billing_event=burl, ждущих burl
// (R-07). Пишет/чистит backend (cache.win_pending / expire_burl /
// /cron/sweep-burl); здесь — KEYS[13] luaSettleWin и контракт паритета.
const PendingBurlKey = "pending:burl"

// DefaultReserveTTL — сколько резерв ждёт win/loss до принудительного expire.
// Поздний win после expire не теряется: luaSettleWin досписывает clearing.
const DefaultReserveTTL = 90 * time.Second

// BidStateTTL — жизнь bidstate:{bid}. Дольше bidmeta (1h), чтобы поздний win
// по PG-fallback увидел, что резерв уже возвращён.
const BidStateTTL = 2 * time.Hour

// ReserveInput — всё, что нужно гейту для одного бида.
type ReserveInput struct {
	BidID        string
	CampaignID   string
	LiID         string
	AdvertiserID string // "" — без ограничения по балансу (только тесты/legacy)
	Cost         float64
	CampBudget   float64
	LiBudget     float64
	// Снимки фида (сеют avail:* при отсутствии ключа).
	AdvBalance     float64
	TotalRemaining float64
	HasTotal       bool
	DeviceID       string
	Caps           []CapWindow
	ReserveTTL     time.Duration // 0 = DefaultReserveTTL
}

// BidReservation — то, что кладётся в bidmeta и нужно для release/settle.
type BidReservation struct {
	BidID        string  `json:"bid_id"`
	CampaignID   string  `json:"campaign_id"`
	LineItemID   string  `json:"line_item_id"`
	AdvertiserID string  `json:"advertiser_id"`
	Reserve      float64 `json:"reserve"`
	BusinessDate string  `json:"business_date"`
	HasTotal     bool    `json:"-"`
	HasLi        bool    `json:"-"`
	// Frequency cap (R-06): устройство и окна бида. По ним release снимает
	// in-flight слот, а settle инкрементирует счётчики окон.
	DeviceID string      `json:"fdev"`
	Caps     []CapWindow `json:"fcaps"`
	// CountOnWin — партнёр без клиентского пикселя (freq_cap_on_win):
	// кап фиксирует показ на win, imp только снимает бронь.
	CountOnWin bool `json:"fwin,omitempty"`
}

// Reserve — bid-time gate (freq-окна с учётом in-flight бидов + дневные
// бюджеты + доступный баланс + остаток total) и постановка бида в pending:bids
// и freqinf. Возвращает описание резерва для bidmeta и allowed.
func (c *Client) Reserve(ctx context.Context, now time.Time, in ReserveInput) (BidReservation, bool, error) {
	res := BidReservation{
		BidID: in.BidID, CampaignID: in.CampaignID, LineItemID: in.LiID, AdvertiserID: in.AdvertiserID,
		Reserve: in.Cost, BusinessDate: BusinessDate(now),
		HasTotal: in.HasTotal && in.AdvertiserID != "", HasLi: in.LiBudget > 0,
		DeviceID: in.DeviceID, Caps: activeCaps(in.Caps),
	}
	if in.CampBudget <= 0 {
		return res, false, nil
	}
	keys, _ := freqWindowKeys(in.CampaignID, in.DeviceID, in.Caps)
	capArgs := make([]string, 0, MaxCapWindows)
	for _, w := range res.Caps {
		limit := w.Limit
		if in.DeviceID == "" {
			limit = 0
		}
		capArgs = append(capArgs, strconv.Itoa(limit))
	}
	for len(capArgs) < MaxCapWindows {
		capArgs = append(capArgs, "0")
	}
	adv := in.AdvertiserID
	if adv == "" {
		adv = "-"
	}
	keys = append(keys,
		SpendKey("camp", in.CampaignID, res.BusinessDate), SpendKey("li", in.LiID, res.BusinessDate),
		AvailAdvKey(adv), AvailCampKey(in.CampaignID), ResAdvKey(adv), ResCampKey(in.CampaignID),
		PendingBidsKey, FreqInflightKey(in.CampaignID, in.DeviceID),
	)
	advBalance := ""
	if in.AdvertiserID != "" {
		advBalance = f64(in.AdvBalance)
	}
	hasTotal := "0"
	if res.HasTotal {
		hasTotal = "1"
	}
	ttl := in.ReserveTTL
	if ttl <= 0 {
		ttl = DefaultReserveTTL
	}
	args := []interface{}{
		capArgs[0], capArgs[1], capArgs[2],
		f64(in.Cost), f64(in.CampBudget), f64(in.LiBudget),
		strconv.FormatInt(secondsUntilBusinessMidnight(now), 10),
		advBalance, f64(in.TotalRemaining), hasTotal,
		in.BidID, strconv.FormatInt(now.Add(ttl).Unix(), 10),
		strconv.FormatInt(now.Unix(), 10),
	}
	out, err := c.gateScript.Run(ctx, c.rdb, keys, args...).Int64Slice()
	if err != nil {
		return res, false, fmt.Errorf("redisx: gate_and_reserve: %w", err)
	}
	if len(out) > 1 && out[1] > 0 {
		WinWithoutImp.Add(uint64(out[1]))
	}
	return res, len(out) > 0 && out[0] == 1, nil
}

// GateAndReserve — legacy-обёртка (дневные бюджеты без баланса/total и без
// pending-трекинга). Используется старыми тестами; hot path зовёт Reserve.
func (c *Client) GateAndReserve(ctx context.Context, campaignID, liID string, cost, campBudget, liBudget float64, deviceID string, caps []CapWindow) (bool, error) {
	return c.GateAndReserveAt(ctx, time.Now(), campaignID, liID, cost, campBudget, liBudget, deviceID, caps)
}

// GateAndReserveAt is GateAndReserve with an explicit clock (tests, midnight edge).
func (c *Client) GateAndReserveAt(ctx context.Context, now time.Time, campaignID, liID string, cost, campBudget, liBudget float64, deviceID string, caps []CapWindow) (bool, error) {
	_, ok, err := c.Reserve(ctx, now, ReserveInput{
		CampaignID: campaignID, LiID: liID, Cost: cost, CampBudget: campBudget, LiBudget: liBudget,
		DeviceID: deviceID, Caps: caps,
	})
	return ok, err
}

func f64(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

// WinWithoutImp — выигранные биды, бронь которых истекла без imp-пикселя
// (счётчик из prune гейта). Экспортируется в /metrics как
// rtb_freq_win_without_imp_total; аномальная доля = сломан пиксель SSP.
var WinWithoutImp atomic.Uint64

// activeCaps — валидные окна бида (limit>0, hours>0), не больше MaxCapWindows.
func activeCaps(caps []CapWindow) []CapWindow {
	out := make([]CapWindow, 0, MaxCapWindows)
	for _, w := range caps {
		if w.Limit <= 0 || w.PeriodHours <= 0 || len(out) >= MaxCapWindows {
			continue
		}
		out = append(out, w)
	}
	return out
}

func b01(v bool) string {
	if v {
		return "1"
	}
	return "0"
}

func (r BidReservation) keys() []string {
	adv := r.AdvertiserID
	if adv == "" {
		adv = "-"
	}
	freqKeys, _ := freqWindowKeys(r.CampaignID, r.DeviceID, r.Caps)
	keys := []string{
		BidStateKey(r.BidID), PendingBidsKey,
		SpendKey("camp", r.CampaignID, r.BusinessDate), SpendKey("li", r.LineItemID, r.BusinessDate),
		AvailAdvKey(adv), AvailCampKey(r.CampaignID), ResAdvKey(adv), ResCampKey(r.CampaignID),
		FreqInflightKey(r.CampaignID, r.DeviceID),
	}
	keys = append(keys, freqKeys...)
	return append(keys, PendingBurlKey)
}

// freqHoursArgs — ARGV[9..11] luaSettleWin: часы окон (0 = pad).
func (r BidReservation) freqHoursArgs() []interface{} {
	_, hours := freqWindowKeys(r.CampaignID, r.DeviceID, r.Caps)
	out := make([]interface{}, 0, MaxCapWindows)
	for _, h := range hours {
		out = append(out, strconv.Itoa(h))
	}
	return out
}

// ReleaseBid переводит бид в терминальное состояние state ("loss"|"expired")
// и возвращает резерв на все ключи одним атомарным скриптом. Идемпотентен:
// второй вызов (или вызов после win) ничего не двигает и возвращает false.
func (c *Client) ReleaseBid(ctx context.Context, r BidReservation, state string) (bool, error) {
	if r.BidID == "" || r.CampaignID == "" {
		return false, nil
	}
	if r.BusinessDate == "" {
		r.BusinessDate = BusinessDate(time.Now())
	}
	out, err := c.releaseScript.Run(ctx, c.rdb, r.keys(),
		state, f64(r.Reserve), r.BidID, strconv.FormatInt(int64(BidStateTTL/time.Second), 10),
		b01(r.HasTotal), b01(r.HasLi)).Int64()
	if err != nil {
		return false, fmt.Errorf("redisx: release_bid: %w", err)
	}
	return out == 1, nil
}

// SettleWin — резерв -> clearing (или досписание, если резерв уже возвращён)
// и фиксация показа в freq-счётчиках окон (R-06). В production это делает
// backend (cache.settle_win) после DB-биллинга; порт здесь — контракт и тесты
// паритета. Возвращает код luaSettleWin.
func (c *Client) SettleWin(ctx context.Context, now time.Time, r BidReservation, clearing float64, assumeReleased bool) (int64, error) {
	if r.BusinessDate == "" {
		r.BusinessDate = BusinessDate(now)
	}
	args := []interface{}{
		f64(r.Reserve), f64(clearing), r.BidID, strconv.FormatInt(int64(BidStateTTL/time.Second), 10),
		b01(r.HasTotal), b01(r.HasLi), strconv.FormatInt(secondsUntilBusinessMidnight(now), 10),
		b01(assumeReleased),
	}
	args = append(args, r.freqHoursArgs()...)
	args = append(args, strconv.FormatInt(now.Add(WinImpTTL).Unix(), 10), b01(r.CountOnWin))
	out, err := c.settleScript.Run(ctx, c.rdb, r.keys(), args...).Int64()
	if err != nil {
		return 0, fmt.Errorf("redisx: settle_win: %w", err)
	}
	return out, nil
}

// ConfirmImp — imp-пиксель подтверждает показ: бронь freqinf снимается,
// счётчики окон инкрементируются (R-06). Идемпотентность — у вызывающего
// (bid_event_once). В production это делает backend (cache.confirm_imp);
// порт здесь — контракт и тесты паритета.
func (c *Client) ConfirmImp(ctx context.Context, r BidReservation) (ImpConfirm, error) {
	if len(r.Caps) == 0 {
		return ImpConfirm{}, nil
	}
	freqKeys, hours := freqWindowKeys(r.CampaignID, r.DeviceID, r.Caps)
	keys := append([]string{FreqInflightKey(r.CampaignID, r.DeviceID)}, freqKeys...)
	args := []interface{}{r.BidID}
	for _, h := range hours {
		args = append(args, strconv.Itoa(h))
	}
	args = append(args, b01(r.CountOnWin))
	out, err := c.confirmScript.Run(ctx, c.rdb, keys, args...).Int64()
	if err != nil {
		return ImpConfirm{}, fmt.Errorf("redisx: confirm_imp: %w", err)
	}
	return ImpConfirm{Counted: out&1 == 1, Late: out&2 == 2}, nil
}

// ReleaseGate — legacy: возврат только дневных ключей без per-bid состояния.
// Оставлен для старых тестов; hot path использует ReleaseBid.
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
	if err := c.releaseGateScript.Run(ctx, c.rdb, keys, f64(cost)).Err(); err != nil {
		return fmt.Errorf("redisx: release_gate: %w", err)
	}
	return nil
}

const luaReleaseGate = `
local cost = tonumber(ARGV[1])
for i = 1, #KEYS do
  if redis.call('EXISTS', KEYS[i]) == 1 then
    local v = tonumber(redis.call('INCRBYFLOAT', KEYS[i], -cost))
    if v < 0 then
      local ttl = redis.call('TTL', KEYS[i])
      if ttl > 0 then redis.call('SET', KEYS[i], '0', 'EX', ttl) else redis.call('SET', KEYS[i], '0') end
    end
  end
end
return 1
`

// SweepExpired — истёкшие резервы из pending:bids -> expired + refund.
// Безопасен при нескольких инстансах биддера: терминальное состояние ставит
// SET NX внутри Lua, второй инстанс получит 0. Возвращает (expired, orphans):
// orphan — bidmeta уже нет (TTL), деньги вернёт reconcile.
func (c *Client) SweepExpired(ctx context.Context, now time.Time, limit int64) (expired, orphans int, err error) {
	ids, err := c.rdb.ZRangeByScore(ctx, PendingBidsKey, &redis.ZRangeBy{
		Min: "-inf", Max: strconv.FormatInt(now.Unix(), 10), Offset: 0, Count: limit,
	}).Result()
	if err != nil {
		return 0, 0, fmt.Errorf("redisx: sweep zrange: %w", err)
	}
	for _, id := range ids {
		raw, gerr := c.rdb.Get(ctx, "bidmeta:"+id).Result()
		if gerr != nil {
			_ = c.rdb.ZRem(ctx, PendingBidsKey, id).Err()
			orphans++
			continue
		}
		r, ok := ParseReservation(raw)
		if !ok {
			_ = c.rdb.ZRem(ctx, PendingBidsKey, id).Err()
			orphans++
			continue
		}
		r.BidID = id
		released, rerr := c.ReleaseBid(ctx, r, "expired")
		if rerr != nil {
			return expired, orphans, rerr
		}
		if released {
			expired++
		}
	}
	return expired, orphans, nil
}

// PendingCount — размер pending:bids (gauge).
func (c *Client) PendingCount(ctx context.Context) int64 {
	n, err := c.rdb.ZCard(ctx, PendingBidsKey).Result()
	if err != nil {
		return -1
	}
	return n
}

// ParseReservation читает поля резерва из bidmeta JSON (формат persistBidMeta).
func ParseReservation(raw string) (BidReservation, bool) {
	var m struct {
		BidID        string      `json:"bid_id"`
		CampaignID   string      `json:"campaign_id"`
		LineItemID   string      `json:"line_item_id"`
		AdvertiserID string      `json:"advertiser_id"`
		Price        float64     `json:"price"`
		Reserve      float64     `json:"reserve"`
		BusinessDate string      `json:"business_date"`
		HasTotal     int         `json:"has_total"`
		HasLi        int         `json:"has_li"`
		IsTest       int         `json:"is_test"`
		FreqDevice   string      `json:"fdev"`
		FreqCaps     []CapWindow `json:"fcaps"`
		FreqWin      int         `json:"fwin"`
	}
	if json.Unmarshal([]byte(raw), &m) != nil || m.CampaignID == "" {
		return BidReservation{}, false
	}
	if m.IsTest != 0 {
		return BidReservation{}, false
	}
	reserve := m.Reserve
	if reserve <= 0 {
		reserve = m.Price / 1000.0
	}
	return BidReservation{
		BidID: m.BidID, CampaignID: m.CampaignID, LineItemID: m.LineItemID, AdvertiserID: m.AdvertiserID,
		Reserve: reserve, BusinessDate: m.BusinessDate, HasTotal: m.HasTotal == 1, HasLi: m.HasLi == 1,
		DeviceID: m.FreqDevice, Caps: activeCaps(m.FreqCaps), CountOnWin: m.FreqWin == 1,
	}, true
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
