// Package feed: загрузка активных кампаний из backend по внутреннему контракту
// GET {BACKEND_INTERNAL_URL}/internal/bidder-feed с заголовком X-Internal-Token.
// Тот же контракт использует Java-биддер (CampaignLoaderService) — сохраняем его.
package feed

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// Campaign — DTO фида. Поля — паритет с Python bidder-feed
// (см. backend/routers/bidder_feed.py). Денежные поля приходят строками
// и парсятся в фиксированную точность на стороне биддера.
type Campaign struct {
	ID           string          `json:"id"`
	Status       string          `json:"status"`
	MaxBid       string          `json:"max_bid"`       // Decimal-as-string
	DailyBudget  string          `json:"daily_budget"`  // Decimal-as-string
	BidShading   bool            `json:"bid_shading"`
	Erid         string          `json:"erid,omitempty"`
	GeoCountries []string        `json:"geo_countries,omitempty"`
	OSTargets    []string        `json:"os_targets,omitempty"`
	DomainWhite  []string        `json:"domain_whitelist,omitempty"`
	DomainBlack  []string        `json:"domain_blacklist,omitempty"`
	SSPAllowlist []string        `json:"ssp_allowlist,omitempty"`
	FreqCaps     json.RawMessage `json:"freq_caps,omitempty"` // multi-window: [{"window_sec":..,"max_imps":..}]
	Audiences    json.RawMessage `json:"audiences,omitempty"` // include/exclude sets
	Creatives    json.RawMessage `json:"creatives,omitempty"`
}

type Config struct {
	BackendURL string
	FeedToken  string
	Refresh    time.Duration
}

// Store — потокобезопасный кэш кампаний с периодическим рефрешем.
type Store struct {
	cfg    Config
	client *http.Client

	mu        sync.RWMutex
	campaigns []Campaign
	loadedAt  time.Time
}

func NewStore(cfg Config) *Store {
	if cfg.Refresh <= 0 {
		cfg.Refresh = 30 * time.Second
	}
	return &Store{
		cfg:    cfg,
		client: &http.Client{Timeout: 5 * time.Second},
	}
}

// Run — цикл рефреша; завершение по ctx.
func (s *Store) Run(ctx context.Context) {
	s.refresh(ctx)
	t := time.NewTicker(s.cfg.Refresh)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.refresh(ctx)
		}
	}
}

func (s *Store) refresh(ctx context.Context) {
	if s.cfg.FeedToken == "" {
		slog.Warn("INTERNAL_FEED_TOKEN not set; feed disabled")
		return
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.cfg.BackendURL+"/internal/bidder-feed", nil)
	if err != nil {
		return
	}
	req.Header.Set("X-Internal-Token", s.cfg.FeedToken)
	resp, err := s.client.Do(req)
	if err != nil {
		slog.Error("feed refresh failed", "err", err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		slog.Error("feed refresh bad status", "status", resp.StatusCode)
		return
	}
	var payload struct {
		Campaigns []Campaign `json:"campaigns"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		slog.Error("feed decode failed", "err", err)
		return
	}
	s.mu.Lock()
	s.campaigns = payload.Campaigns
	s.loadedAt = time.Now()
	s.mu.Unlock()
	slog.Info("feed refreshed", "campaigns", len(payload.Campaigns))
}

// Snapshot — копия среза кампаний для hot path (без блокировки на время аукциона).
func (s *Store) Snapshot() []Campaign {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.campaigns
}

func (s *Store) Len() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.campaigns)
}

// Age — время с последней успешной загрузки фида (для healthz/алертов).
func (s *Store) Age() time.Duration {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.loadedAt.IsZero() {
		return time.Duration(0)
	}
	return time.Since(s.loadedAt)
}
