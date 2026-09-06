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

	gateScript        *redis.Script // luaGateAndReserve
	releaseScript     *redis.Script // luaReleaseBid (loss/expired)
	settleScript      *redis.Script // luaSettleWin
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
// ARGV[1..3]: cap limits aligned with KEYS[1..3] (0 = window disabled)
// ARGV[4]:    cost (RUB, float) — CPM/1000 for one impression
// ARGV[5]:    camp daily budget (RUB)
// ARGV[6]:    line-item daily budget (RUB, 0 = unlimited)
// ARGV[7]:    TTL seconds until Europe/Moscow midnight + grace (GC only)
// ARGV[8]:    advertiser balance snapshot from feed ('' = no advertiser constraint)
// ARGV[9]:    total budget remaining snapshot from feed
// ARGV[10]:   '1' when the campaign has a total budget (ARGV[9] enforced)
// ARGV[11]:   bid_id ('' = do not track in pending:bids)
// ARGV[12]:   pending deadline (unix seconds) for the sweeper
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
`

// ---------------------------------------------------------------------------
// Per-bid terminal states (audit M-06).
//
// bidstate:{bid_id} = win | loss | expired — ставится атомарно в том же
// Lua-скрипте, что и движение денег, поэтому:
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
//
// luaReleaseBid ARGV: 1 state ('loss'|'expired'), 2 reserve, 3 bid_id,
//                     4 bidstate TTL, 5 has_total, 6 has_li
// Returns 1 when the reservation was refunded now, 0 when the bid already
// had a terminal state (nothing moved).
// ---------------------------------------------------------------------------

const luaReleaseBid = `
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
`

// luaSettleWin ARGV: 1 reserve, 2 clearing cost, 3 bid_id, 4 bidstate TTL,
//
//	5 has_total, 6 has_li, 7 spend-key TTL,
//	8 assume_released ('1' when the reservation is known to
//	  be gone — e.g. bid resolved from Postgres after bidmeta expired)
//
// Returns 0 duplicate win, 1 reserve converted to clearing, 2 clearing
// re-charged after a prior loss/expire.
const luaSettleWin = `
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

// Key builders shared with backend/cache.py (contract).
func AvailAdvKey(advertiserID string) string { return "avail:adv:" + advertiserID }
func AvailCampKey(campaignID string) string  { return "avail:camp:" + campaignID }
func ResAdvKey(advertiserID string) string   { return "res:adv:" + advertiserID }
func ResCampKey(campaignID string) string    { return "res:camp:" + campaignID }
func BidStateKey(bidID string) string        { return "bidstate:" + bidID }

// PendingBidsKey — ZSET bid_id -> unix deadline; sweeper expires overdue bids.
const PendingBidsKey = "pending:bids"

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
}

// Reserve — bid-time gate (freq read-only + дневные бюджеты + доступный баланс
// + остаток total) и постановка бида в pending:bids. Возвращает описание
// резерва для bidmeta и allowed.
func (c *Client) Reserve(ctx context.Context, now time.Time, in ReserveInput) (BidReservation, bool, error) {
	res := BidReservation{
		BidID: in.BidID, CampaignID: in.CampaignID, LineItemID: in.LiID, AdvertiserID: in.AdvertiserID,
		Reserve: in.Cost, BusinessDate: BusinessDate(now),
		HasTotal: in.HasTotal && in.AdvertiserID != "", HasLi: in.LiBudget > 0,
	}
	if in.CampBudget <= 0 {
		return res, false, nil
	}
	dev := in.DeviceID
	if dev == "" {
		dev = "-"
	}
	keys := make([]string, 0, 10)
	capArgs := make([]string, 0, 3)
	for _, w := range in.Caps {
		if w.Limit <= 0 || w.PeriodHours <= 0 || len(keys) >= 3 {
			continue
		}
		keys = append(keys, fmt.Sprintf("freq:%s:%dh:%s", in.CampaignID, w.PeriodHours, dev))
		limit := w.Limit
		if in.DeviceID == "" {
			limit = 0
		}
		capArgs = append(capArgs, strconv.Itoa(limit))
	}
	for len(keys) < 3 {
		keys = append(keys, fmt.Sprintf("freq:%s:pad%d:%s", in.CampaignID, len(keys), dev))
		capArgs = append(capArgs, "0")
	}
	adv := in.AdvertiserID
	if adv == "" {
		adv = "-"
	}
	keys = append(keys,
		SpendKey("camp", in.CampaignID, res.BusinessDate), SpendKey("li", in.LiID, res.BusinessDate),
		AvailAdvKey(adv), AvailCampKey(in.CampaignID), ResAdvKey(adv), ResCampKey(in.CampaignID),
		PendingBidsKey,
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
	}
	out, err := c.gateScript.Run(ctx, c.rdb, keys, args...).Int64()
	if err != nil {
		return res, false, fmt.Errorf("redisx: gate_and_reserve: %w", err)
	}
	return res, out == 1, nil
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
	return []string{
		BidStateKey(r.BidID), PendingBidsKey,
		SpendKey("camp", r.CampaignID, r.BusinessDate), SpendKey("li", r.LineItemID, r.BusinessDate),
		AvailAdvKey(adv), AvailCampKey(r.CampaignID), ResAdvKey(adv), ResCampKey(r.CampaignID),
	}
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

// SettleWin — резерв -> clearing (или досписание, если резерв уже возвращён).
// В production это делает backend (cache.settle_win) после DB-биллинга; порт
// здесь — контракт и тесты паритета. Возвращает код luaSettleWin.
func (c *Client) SettleWin(ctx context.Context, now time.Time, r BidReservation, clearing float64, assumeReleased bool) (int64, error) {
	if r.BusinessDate == "" {
		r.BusinessDate = BusinessDate(now)
	}
	out, err := c.settleScript.Run(ctx, c.rdb, r.keys(),
		f64(r.Reserve), f64(clearing), r.BidID, strconv.FormatInt(int64(BidStateTTL/time.Second), 10),
		b01(r.HasTotal), b01(r.HasLi), strconv.FormatInt(secondsUntilBusinessMidnight(now), 10),
		b01(assumeReleased)).Int64()
	if err != nil {
		return 0, fmt.Errorf("redisx: settle_win: %w", err)
	}
	return out, nil
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
		BidID        string  `json:"bid_id"`
		CampaignID   string  `json:"campaign_id"`
		LineItemID   string  `json:"line_item_id"`
		AdvertiserID string  `json:"advertiser_id"`
		Price        float64 `json:"price"`
		Reserve      float64 `json:"reserve"`
		BusinessDate string  `json:"business_date"`
		HasTotal     int     `json:"has_total"`
		HasLi        int     `json:"has_li"`
		IsTest       int     `json:"is_test"`
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
