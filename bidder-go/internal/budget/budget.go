// Package budget: атомарный резерв дневного бюджета в Redis.
//
// Имплементация переносит 1:1 Lua-скрипт из backend/cache.py
// (gate_and_reserve / reserve_budget): CAS-цикл исключён, скрипт атомарен,
// double-spend невозможен. Деньги — в копейках (int64), НИКОГДА не float.
package budget

// ReserveResult — результат попытки резерва.
type ReserveResult int

const (
	Reserved ReserveResult = iota // резерв успешен, можно биддить
	Exhausted                     // дневной бюджет исчерпан
	Unavailable                   // Redis недоступен: fail-closed (не биддим)
)

// TODO(impl): Reserve(ctx, campaignID, kopecks) / Release(ctx, campaignID, kopecks)
// через go-redis Script (EVALSHA). Ключ и TTL — как в Python:
// budget:{campaign_id}:{yyyy-mm-dd}, TTL до конца суток (MSK).
