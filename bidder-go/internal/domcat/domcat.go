// Package domcat — справочник «домен → тематика IAB» (Фаза 3 контекстного
// таргетинга): подстановка категорий, когда SSP их не прислала, и самообучение
// справочника по трафику.
//
// Справочник живёт отдельно от основного снапшота фида: ошибка его загрузки
// оставляет прежнюю карту и не затрагивает ставки.
package domcat

import (
	"context"
	"log/slog"
	"strings"
	"sync/atomic"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/targeting"
)

const (
	// FeedPath — GET /rtb-feed/domain-categories (backend/routers/bidder_feed.py).
	FeedPath = "/rtb-feed/domain-categories"
	// DefaultRefresh — как часто подгружать карту.
	DefaultRefresh = 5 * time.Minute
	// maxDomains — потолок записей, принимаемых из фида (защита памяти).
	maxDomains = 500_000
)

// Normalize приводит домен запроса к ключу справочника: нижний регистр,
// без `www.` и завершающей точки. "" — если домен не годится для справочника
// (пустой, bundle приложения без точки, слишком длинный).
func Normalize(domain string) string {
	d := strings.TrimSuffix(strings.ToLower(strings.TrimSpace(domain)), ".")
	d = strings.TrimPrefix(d, "www.")
	if d == "" || len(d) > 253 || !strings.Contains(d, ".") {
		return ""
	}
	return d
}

// Map — неизменяемая карта домен → канонические IAB-коды; подмена атомарна,
// чтение с горячего пути не блокируется.
type Map struct {
	m atomic.Pointer[map[string][]string]
}

// Payload — формат GET /rtb-feed/domain-categories.
type Payload struct {
	Count     int                 `json:"count"`
	Truncated bool                `json:"truncated"`
	Domains   map[string][]string `json:"domains"`
}

// Lookup возвращает категории домена (уже нормализованного через Normalize).
// Результат только читается. nil — домена нет или карта не загружена.
func (m *Map) Lookup(domain string) []string {
	if m == nil {
		return nil
	}
	p := m.m.Load()
	if p == nil {
		return nil
	}
	return (*p)[domain]
}

// Len — число доменов в карте.
func (m *Map) Len() int {
	if m == nil {
		return 0
	}
	if p := m.m.Load(); p != nil {
		return len(*p)
	}
	return 0
}

// Set собирает карту из фида: ключи и коды нормализуются так же, как категории
// запроса (targeting.CollectCats), пустые записи отбрасываются.
func (m *Map) Set(p *Payload) {
	out := make(map[string][]string, len(p.Domains))
	for d, codes := range p.Domains {
		if len(out) >= maxDomains {
			break
		}
		key := Normalize(d)
		if key == "" {
			continue
		}
		if cats := targeting.CollectCats(codes); len(cats) > 0 {
			out[key] = cats
		}
	}
	m.m.Store(&out)
}

// Getter — загрузка JSON с backend (feed.Store.GetJSON).
type Getter func(ctx context.Context, path string, out any) error

// Load подгружает карту; при ошибке прежняя карта остаётся.
func (m *Map) Load(ctx context.Context, get Getter) error {
	var p Payload
	if err := get(ctx, FeedPath, &p); err != nil {
		return err
	}
	m.Set(&p)
	slog.Info("domain categories loaded", "domains", m.Len(), "truncated", p.Truncated)
	return nil
}

// Run грузит карту сразу и затем раз в interval, пока ctx не отменён.
func (m *Map) Run(ctx context.Context, get Getter, interval time.Duration) {
	if interval <= 0 {
		interval = DefaultRefresh
	}
	load := func() {
		lctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		if err := m.Load(lctx, get); err != nil && ctx.Err() == nil {
			slog.Warn("domain categories refresh failed; keeping previous map", "err", err, "domains", m.Len())
		}
	}
	load()
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			load()
		}
	}
}
