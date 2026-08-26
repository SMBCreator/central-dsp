// Package shading: bid shading факторы (обучаются офлайн в Python cron,
// биддер только ЧИТАЕТ фактор из Redis: shading:{campaign_id}:{partner_id}).
//
// Инварианты (паритет с backend/shading.py + фикс GAP-005):
//   - shading применяется ТОЛЬКО при at=1 (first price) и bid_shading=true
//   - цена никогда не опускается ниже floor*1.01
//   - фактор в [MinFactor, 1.0]; отсутствие ключа = 1.0 (no-op)
package shading

import (
	"context"
	"fmt"
	"strconv"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// MinFactor — нижняя граница фактора, как FACTOR_MIN в Python.
const MinFactor = 0.60

// cacheTTL — локальный кэш фактора; крон пересчитывает раз в N минут,
// поэтому 10s устаревания безопасны и убирают Redis из hot path.
const cacheTTL = 10 * time.Second

// ShouldShade — гейт применения shading (порт should_shade из backend/shading.py).
func ShouldShade(bidShadingEnabled bool, auctionType int) bool {
	return bidShadingEnabled && auctionType == 1
}

// Apply применяет фактор к цене с floor-защитой (порт apply_shading).
func Apply(price, factor, floor float64) float64 {
	if factor < MinFactor {
		factor = MinFactor
	}
	if factor > 1.0 {
		factor = 1.0
	}
	shaded := price * factor
	minPrice := floor * 1.01
	if floor > 0 && shaded < minPrice {
		shaded = minPrice
	}
	if shaded > price {
		shaded = price
	}
	return shaded
}

type cacheEntry struct {
	factor  float64
	expires time.Time
}

// Store читает shading-факторы из Redis с локальным кэшем.
type Store struct {
	rdb   *redis.Client
	mu    sync.RWMutex
	cache map[string]cacheEntry
}

// NewStore создаёт Store поверх готового redis-клиента.
func NewStore(rdb *redis.Client) *Store {
	return &Store{rdb: rdb, cache: make(map[string]cacheEntry)}
}

// GetFactor возвращает фактор для (campaignID, partnerID); 1.0 если не задан.
func (s *Store) GetFactor(ctx context.Context, campaignID, partnerID string) float64 {
	key := fmt.Sprintf("shading:%s:%s", campaignID, partnerID)

	s.mu.RLock()
	if e, ok := s.cache[key]; ok && time.Now().Before(e.expires) {
		s.mu.RUnlock()
		return e.factor
	}
	s.mu.RUnlock()

	factor := 1.0
	if s.rdb != nil {
		if v, err := s.rdb.Get(ctx, key).Result(); err == nil {
			if f, perr := strconv.ParseFloat(v, 64); perr == nil {
				if f < MinFactor {
					f = MinFactor
				}
				if f > 1.0 {
					f = 1.0
				}
				factor = f
			}
		}
	}

	s.mu.Lock()
	s.cache[key] = cacheEntry{factor: factor, expires: time.Now().Add(cacheTTL)}
	s.mu.Unlock()
	return factor
}
