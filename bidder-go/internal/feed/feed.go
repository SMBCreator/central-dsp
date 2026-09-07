// Package feed: загрузка активных кандидатов (line item + creative),
// SSP-партнёров и глобальных доменных листов из backend.
//
// Контракт (backend/routers/bidder_feed.py):
//
//	GET {BACKEND_URL}/rtb-feed/campaigns  -> {candidates: [...], domain_lists: [...]}
//	GET {BACKEND_URL}/rtb-feed/partners   -> {partners: [...]}
//
// Auth: Authorization: Bearer ${RTB_API_TOKEN}.
package feed

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Candidate — строка кандидата, паритет с SQL-выборкой Python hot path
// (process_bid_request). Money-поля приходят числами из JSON; финальную
// проверку бюджета делает атомарный Lua-скрипт в Redis, поэтому
// накопительные расхождения исключены.
type Candidate struct {
	LiID       string          `json:"li_id"`
	BidPrice   float64         `json:"bid_price"`
	BidType    string          `json:"bid_type"`
	MaxBid     *float64        `json:"max_bid"`
	LiBudget   *float64        `json:"li_budget"`
	Targeting  json.RawMessage `json:"targeting"`
	CampaignID string          `json:"campaign_id"`
	OwnerID    string          `json:"owner_id"`

	CampBudget float64 `json:"camp_budget"`
	// Финансовые снимки для гейта (audit M-05): баланс рекламодателя и остаток
	// общего бюджета. Сеют avail:* в Redis только при отсутствии ключа — сам
	// лимит держат Lua-резервы, а не эти числа.
	AdvBalance   float64         `json:"adv_balance"`
	TotalBudget  float64         `json:"total_budget"`
	SpentTotal   float64         `json:"spent_total"`
	FreqCapImps  *int            `json:"frequency_cap_impressions"`
	FreqCapHours *int            `json:"frequency_cap_period_hours"`
	FreqCaps     json.RawMessage `json:"frequency_caps"`
	Pacing       string          `json:"pacing"`
	AdvertiserID string          `json:"advertiser_id"`
	BidShading   bool            `json:"bid_shading"`
	AdvDomain    string          `json:"adv_domain"`

	CreativeID      string `json:"creative_id"`
	Type            string `json:"type"`
	Width           *int   `json:"width"`
	Height          *int   `json:"height"`
	AssetURL        string `json:"asset_url"`
	ClickURL        string `json:"click_url"`
	HTMLSnippet     string `json:"html_snippet"`
	NativeTitle     string `json:"native_title"`
	NativeDesc      string `json:"native_description"`
	NativeCTA       string `json:"native_cta"`
	NativeIconURL   string `json:"native_icon_url"`
	VideoURL        string `json:"video_url"`
	VideoDuration   *int   `json:"video_duration"`
	VastXML         string `json:"vast_xml"`
	Name            string `json:"name"`
	ImpTrackerURL   string `json:"imp_tracker_url"`
	ClickTrackerURL string `json:"click_tracker_url"`
	Erid            string `json:"erid"`
	// Brand-safety сигналы креатива (миграция 035): bid.cat / фильтр bcat,
	// bid.attr / фильтр imp.banner|video.battr.
	IABCategories []string `json:"iab_categories"`
	Attrs         []int    `json:"attrs"`

	// Информация о рекламодателе (38-ФЗ): для инфо-значка на креативе
	AdvName    string `json:"adv_name"`
	AdvINN     string `json:"adv_inn"`
	AdvOGRN    string `json:"adv_ogrn"`
	AdvAddress string `json:"adv_address"`

	// разобранный targeting (заполняется при рефреше, не в hot path)
	T *Targeting `json:"-"`
}

// Targeting — распарсенный targeting JSON line item.
// Все поля опциональны; пустое значение = нет ограничения.
// Семантика — паритет с _match_targeting/_match_imp_targeting (rtb.py).
type Targeting struct {
	GeoCountries    []string `json:"geo_countries"`
	GeoRegions      []string `json:"geo_regions"`
	GeoCities       []string `json:"geo_cities"`
	DeviceTypes     []string `json:"device_types"`
	OS              []string `json:"os"`
	Browsers        []string `json:"browsers"`
	Languages       []string `json:"languages"`
	ConnectionTypes []string `json:"connection_types"`
	IABCategories   []string `json:"iab_categories"`
	IABBlock        []string `json:"iab_block"`
	DomainAllowlist []string `json:"domain_allowlist"`
	DomainBlocklist []string `json:"domain_blocklist"`
	AgeMin          int      `json:"age_min"`
	AgeMax          int      `json:"age_max"`
	Genders         []string `json:"genders"`
	Hours           []int    `json:"hours"`
	Days            []int    `json:"days"`
	SSPAllowlist    []string `json:"ssp_allowlist"`
	TrafficTypes    []string `json:"traffic_types"`
	Carriers        []string `json:"carriers"`
	OSVersionMin    string   `json:"os_version_min"`
	AppBundlesBlock []string `json:"app_bundles_block"`
	AppBundlesAllow []string `json:"app_bundles_allow"`
	PublisherBlock  []string `json:"publisher_block"`
	PublisherAllow  []string `json:"publisher_allow"`
	KeywordsBlock   []string `json:"keywords_block"`
	KeywordsAllow   []string `json:"keywords_allow"`
	IPRanges        []string `json:"ip_ranges"`
	BlockCOPPA      bool     `json:"block_coppa"`
	BlockGDPR       bool     `json:"block_gdpr"`
	SkipDNT         bool     `json:"skip_dnt"`
	// SSP-сигналы (session 04.09): производитель устройства (device.make),
	// обязательный source.schain (прозрачность цепочки поставки).
	DeviceMakes    []string `json:"device_makes"`
	SChainRequired bool     `json:"schain_required"`

	BannerPositions     []int `json:"banner_positions"`
	InterstitialOnly    bool  `json:"interstitial_only"`
	ExcludeInterstitial bool  `json:"exclude_interstitial"`
	RewardedOnly        bool  `json:"rewarded_only"`
	SecureOnly          bool  `json:"secure_only"`
	VideoMinDuration    int   `json:"video_min_duration"`
	VideoSkippableOnly  bool  `json:"video_skippable_only"`
	// imp-level SSP-сигналы: минимальная прогнозная viewability (imp.metric
	// type=viewability, 0..100), video.plcmt (OpenRTB 2.6: 1 instream,
	// 2 accompanying, 3 interstitial, 4 no-content/standalone; fallback
	// placement 2.5), video.protocols (VAST 2/3/4 = 2,3,7; wrapper = 5,6,8).
	ViewabilityMin  int   `json:"viewability_min"`
	VideoPlacements []int `json:"video_placements"`
	VideoProtocols  []int `json:"video_protocols"`

	DealIDs         []string `json:"deal_ids"`
	AudienceInclude []string `json:"audience_include"`
	AudienceExclude []string `json:"audience_exclude"`
}

// Partner — SSP-партнёр (supply_partners).
type Partner struct {
	ID             string          `json:"id"`
	EndpointKey    string          `json:"endpoint_key"`
	AuthTokenHash  string          `json:"auth_token_hash"` // hex(sha256(token)), миграция 032; "" = токен не выдан
	QPSLimit       int             `json:"qps_limit"`
	BidFloor       float64         `json:"bid_floor"`
	Status         string          `json:"status"`
	AllowedAdTypes json.RawMessage `json:"allowed_ad_types"`
	IPWhitelist    json.RawMessage `json:"ip_whitelist"` // JSON-массив IP/CIDR серверов SSP; пусто = любой IP

	// Окно безразрывной ротации токена (миграция 039, audit S-06): предыдущий
	// хэш принимается, пока now < TokenPrevValidUntil. Backend отдаёт поля
	// только при открытом окне.
	TokenPrevHash       string     `json:"token_prev_hash"`
	TokenPrevValidUntil *time.Time `json:"token_prev_valid_until"`

	AllowedTypes map[string]bool `json:"-"`
	IPNets       []*net.IPNet    `json:"-"` // распарсенный IPWhitelist
	// WhitelistInvalid — ip_whitelist не разобрался целиком. Fail-closed: такой
	// партнёр не биддится (метрика bidder_no_bid_total{reason="whitelist_invalid"}),
	// а не торгуется с любым IP, как было бы при молчаливом пропуске мусора.
	WhitelistInvalid bool `json:"-"`
}

// ParseIPWhitelist разбирает JSON-массив строк IP или CIDR (объект/строка с
// объектом — как ParseAllowedTypes). Любой неразборчивый элемент — ошибка:
// backend валидирует список при сохранении (ssp_tokens.validate_ip_whitelist),
// так что ошибка здесь = повреждённые данные, и партнёр должен закрыться
// (audit S-06), а не открыться.
func ParseIPWhitelist(raw json.RawMessage) ([]*net.IPNet, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	var items []string
	if err := json.Unmarshal(raw, &items); err != nil {
		var s string
		if json.Unmarshal(raw, &s) != nil || json.Unmarshal([]byte(s), &items) != nil {
			return nil, fmt.Errorf("ip_whitelist: not a JSON array of strings: %w", err)
		}
	}
	out := make([]*net.IPNet, 0, len(items))
	for _, it := range items {
		it = strings.TrimSpace(it)
		if it == "" {
			continue
		}
		if _, n, err := net.ParseCIDR(it); err == nil {
			out = append(out, n)
			continue
		}
		if ip := net.ParseIP(it); ip != nil {
			bits := 32
			if ip.To4() == nil {
				bits = 128
			}
			out = append(out, &net.IPNet{IP: ip, Mask: net.CIDRMask(bits, bits)})
			continue
		}
		return nil, fmt.Errorf("ip_whitelist: invalid IP/CIDR %q", it)
	}
	return out, nil
}

// DomainList — глобальные white/blacklist на уровне аккаунта.
type DomainList struct {
	UserID   string `json:"user_id"`
	Domain   string `json:"domain"`
	ListType string `json:"list_type"` // "black" | "white"
}

type Config struct {
	BackendURL string
	FeedToken  string // RTB_API_TOKEN
	Refresh    time.Duration
	// MaxAge — предельный возраст снапшота (audit R-02). Пока backend недоступен,
	// биддер торгует по старому слепку не дольше MaxAge: дальше pause, бюджет,
	// блокировка SSP или ротация токена в нём могут быть уже неверны → no-bid и
	// /healthz 503. 0 = DefaultMaxAge.
	MaxAge time.Duration
}

// DefaultMaxAge — FEED_MAX_AGE по умолчанию: четыре интервала рефреша.
const DefaultMaxAge = 120 * time.Second

// ErrNoToken — RTB_API_TOKEN пуст: фид не грузится, снапшот никогда не станет свежим.
var ErrNoToken = errors.New("feed: RTB_API_TOKEN not set")

// Snapshot — иммутабельный слепок фида для hot path (читается без блокировки).
type Snapshot struct {
	Candidates []Candidate
	Partners   map[string]*Partner        // by endpoint_key
	GBlack     map[string]map[string]bool // owner_id -> domains
	GWhite     map[string]map[string]bool
	LoadedAt   time.Time
}

// Status — состояние фида для /healthz и /internal/feed/reload.
type Status struct {
	Loaded      bool          // хотя бы один успешный рефреш
	Age         time.Duration // с последней успешной загрузки
	MaxAge      time.Duration
	Stale       bool // !Loaded || Age > MaxAge
	LastError   string
	LastAttempt time.Time
	Candidates  int
	Partners    int
}

type Store struct {
	cfg    Config
	client *http.Client

	mu   sync.RWMutex
	snap *Snapshot

	lastErr     string
	lastAttempt time.Time

	refreshErrors atomic.Int64
}

func NewStore(cfg Config) *Store {
	if cfg.Refresh <= 0 {
		cfg.Refresh = 30 * time.Second
	}
	if cfg.MaxAge <= 0 {
		cfg.MaxAge = DefaultMaxAge
	}
	return &Store{
		cfg:    cfg,
		client: &http.Client{Timeout: 5 * time.Second},
		snap:   &Snapshot{Partners: map[string]*Partner{}},
	}
}

// Refresh — немедленный рефреш снапшота (POST /internal/feed/reload).
// Возвращает реальный результат: backend по нему решает, попала ли кампания в
// снапшот перед автопроверкой SSP (audit R-02).
func (s *Store) Refresh(ctx context.Context) error { return s.refresh(ctx) }

// Run — цикл рефреша; завершение по ctx.
func (s *Store) Run(ctx context.Context) {
	_ = s.refresh(ctx)
	t := time.NewTicker(s.cfg.Refresh)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			_ = s.refresh(ctx)
		}
	}
}

// RefreshErrors — число неудачных рефрешей с запуска (метрика).
func (s *Store) RefreshErrors() int64 { return s.refreshErrors.Load() }

func (s *Store) recordError(err error) error {
	s.refreshErrors.Add(1)
	s.mu.Lock()
	s.lastErr = err.Error()
	s.lastAttempt = time.Now()
	s.mu.Unlock()
	return err
}

type feedError struct {
	status int
	path   string
}

func (e *feedError) Error() string { return "feed " + e.path + " bad status" }

func (s *Store) get(ctx context.Context, path string, out any) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.cfg.BackendURL+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+s.cfg.FeedToken)
	resp, err := s.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return &feedError{status: resp.StatusCode, path: path}
	}
	dec := json.NewDecoder(resp.Body)
	dec.UseNumber()
	return dec.Decode(out)
}

func (s *Store) refresh(ctx context.Context) error {
	if s.cfg.FeedToken == "" {
		slog.Warn("RTB_API_TOKEN not set; feed disabled")
		return s.recordError(ErrNoToken)
	}
	var camps struct {
		Candidates  []Candidate  `json:"candidates"`
		DomainLists []DomainList `json:"domain_lists"`
	}
	if err := s.get(ctx, "/rtb-feed/campaigns", &camps); err != nil {
		slog.Error("feed campaigns refresh failed", "err", err)
		return s.recordError(fmt.Errorf("campaigns: %w", err))
	}
	var parts struct {
		Partners []Partner `json:"partners"`
	}
	if err := s.get(ctx, "/rtb-feed/partners", &parts); err != nil {
		slog.Error("feed partners refresh failed", "err", err)
		return s.recordError(fmt.Errorf("partners: %w", err))
	}

	snap := &Snapshot{
		Partners: make(map[string]*Partner, len(parts.Partners)),
		GBlack:   map[string]map[string]bool{},
		GWhite:   map[string]map[string]bool{},
		LoadedAt: time.Now(),
	}
	for i := range parts.Partners {
		p := &parts.Partners[i]
		p.AllowedTypes = ParseAllowedTypes(p.AllowedAdTypes)
		nets, err := ParseIPWhitelist(p.IPWhitelist)
		if err != nil {
			// Fail-closed: партнёр остаётся в снапшоте, но hot path отвечает 403
			// и считает whitelist_invalid — видно в /metrics и логах.
			slog.Error("feed partner ip_whitelist invalid; partner fail-closed",
				"partner_id", p.ID, "endpoint_key", p.EndpointKey, "err", err)
			p.WhitelistInvalid = true
		}
		p.IPNets = nets
		snap.Partners[p.EndpointKey] = p
	}
	for i := range camps.Candidates {
		c := &camps.Candidates[i]
		c.T = ParseTargeting(c.Targeting)
	}
	snap.Candidates = camps.Candidates
	for _, dl := range camps.DomainLists {
		m := snap.GWhite
		if dl.ListType == "black" {
			m = snap.GBlack
		}
		owner := dl.UserID
		if m[owner] == nil {
			m[owner] = map[string]bool{}
		}
		m[owner][strings.ToLower(dl.Domain)] = true
	}

	s.mu.Lock()
	s.snap = snap
	s.lastErr = ""
	s.lastAttempt = snap.LoadedAt
	s.mu.Unlock()
	slog.Info("feed refreshed", "candidates", len(snap.Candidates), "partners", len(snap.Partners))
	return nil
}

// ParseTargeting разбирает targeting JSONB (объект или строку с объектом).
func ParseTargeting(raw json.RawMessage) *Targeting {
	t := &Targeting{}
	if len(raw) == 0 {
		return t
	}
	b := []byte(raw)
	var inner string
	if json.Unmarshal(b, &inner) == nil {
		b = []byte(inner)
	}
	_ = json.Unmarshal(b, t)
	return t
}

// ParseAllowedTypes разбирает allowed_ad_types (json-массив или строку с массивом).
func ParseAllowedTypes(raw json.RawMessage) map[string]bool {
	out := map[string]bool{}
	if len(raw) == 0 {
		return out
	}
	var types []string
	if err := json.Unmarshal(raw, &types); err != nil {
		var inner string
		if json.Unmarshal(raw, &inner) == nil {
			_ = json.Unmarshal([]byte(inner), &types)
		}
	}
	for _, t := range types {
		out[strings.ToLower(t)] = true
	}
	return out
}

// Snapshot — текущий слепок (иммутабельный; hot path не держит блокировку).
// SetSnapshot атомарно подменяет снапшот (тесты и предзагрузка).
func (s *Store) SetSnapshot(snap *Snapshot) {
	s.mu.Lock()
	s.snap = snap
	s.mu.Unlock()
}

func (s *Store) Snapshot() *Snapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.snap
}

func (s *Store) Len() int {
	return len(s.Snapshot().Candidates)
}

// Age — время с последней успешной загрузки фида (для healthz/алертов).
func (s *Store) Age() time.Duration {
	snap := s.Snapshot()
	if snap.LoadedAt.IsZero() {
		return 0
	}
	return time.Since(snap.LoadedAt)
}

// MaxAge — действующий предел возраста снапшота.
func (s *Store) MaxAge() time.Duration { return s.cfg.MaxAge }

// Fresh — можно ли торговать по текущему снапшоту (audit R-02): фид загружен
// хотя бы раз и не старше MaxAge. Hot path зовёт на каждый запрос — только
// чтение времени, без блокировки сверх RLock снапшота.
func (s *Store) Fresh() bool {
	snap := s.Snapshot()
	return snap != nil && !snap.LoadedAt.IsZero() && time.Since(snap.LoadedAt) <= s.cfg.MaxAge
}

// Status — состояние фида для /healthz и /internal/feed/reload.
func (s *Store) Status() Status {
	s.mu.RLock()
	snap, lastErr, lastAttempt := s.snap, s.lastErr, s.lastAttempt
	s.mu.RUnlock()
	st := Status{MaxAge: s.cfg.MaxAge, LastError: lastErr, LastAttempt: lastAttempt}
	if snap != nil {
		st.Candidates, st.Partners = len(snap.Candidates), len(snap.Partners)
		if !snap.LoadedAt.IsZero() {
			st.Loaded = true
			st.Age = time.Since(snap.LoadedAt)
		}
	}
	st.Stale = !st.Loaded || st.Age > s.cfg.MaxAge
	return st
}
