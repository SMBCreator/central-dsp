// Package prefilter — порт backend/prefilter.py (Часть C, audit R-03).
//
// O(1)-отсев заведомо бесперспективных bid-запросов ДО таргетинга, Redis-гейта
// и аукциона. Это не бизнес-логика качества трафика (clean traffic — в
// snapshot.DirtyDomains), а дешёвая защита hot path. Причины — bounded enum,
// идут в bidder_prefilter_drops_total{reason} и НЕ считаются no-bid.
//
// Принципы те же, что в Python:
//   - fail-open: любая ошибка / недоступный Redis / пустой кэш → запрос проходит;
//   - никакого I/O на hot path: datacenter-префиксы кэшируются in-process и
//     обновляются в фоне (single-flight);
//   - конфигурация через env: THROTTLE_ENABLED (default on), THROTTLE_COUNTRIES
//     (csv, default "ru,rus"; пусто = гео-фильтр выключен),
//     THROTTLE_REQUIRE_DEVICE_ID (default on), THROTTLE_DC_CACHE_TTL (сек, 60).
//
// Один Redis SET prefilter:dc_subnets на оба биддера: админ-API backend'а
// пишет туда канонические префиксы "1.2.3." (/24) и "1.2." (/16).
package prefilter

import (
	"context"
	"log/slog"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Reason — причина отсечения (та же таксономия, что rtb_prefilter_drops_total).
type Reason string

const (
	ReasonBotUA        Reason = "bot_ua"
	ReasonGeoUnserved  Reason = "geo_unserved"
	ReasonNoDeviceID   Reason = "no_device_id"
	ReasonDatacenterIP Reason = "datacenter_ip"
)

// AllReasons — для инициализации нулевых серий в /metrics.
var AllReasons = []Reason{ReasonBotUA, ReasonGeoUnserved, ReasonNoDeviceID, ReasonDatacenterIP}

// DCSubnetsKey — Redis SET с datacenter-префиксами (общий с Python).
const DCSubnetsKey = "prefilter:dc_subnets"

// botUA — тот же паттерн, что _BOT_UA_RE в Python. Пустой UA не блокируем:
// часть in-app инвентаря легитимно приходит без UA.
var botUA = regexp.MustCompile(`(?i)bot|crawler|spider|scraper|curl|wget|python-requests|python-urllib|go-http-client|java/|libwww|httpclient|okhttp|headless|phantomjs|selenium|puppeteer|playwright`)

// Device — минимальная проекция device из bid-запроса, нужная предфильтру.
type Device struct {
	UA      string
	Country string // device.geo.country как пришёл
	IFA     string
	IP      string
	IPv6    string
}

// SetReader — SMEMBERS; удовлетворяется redis.Client (go-redis) через адаптер.
type SetReader interface {
	SMembers(ctx context.Context, key string) ([]string, error)
}

// Config — настройки предфильтра (из env через FromEnv или явно в тестах).
type Config struct {
	Enabled         bool
	Countries       map[string]bool // lower; пусто = гео-фильтр выключен
	RequireDeviceID bool
	DCCacheTTL      time.Duration
}

// FromEnv — та же семантика дефолтов, что в prefilter.py.
func FromEnv() Config {
	cfg := Config{
		Enabled:         envBool("THROTTLE_ENABLED", true),
		RequireDeviceID: envBool("THROTTLE_REQUIRE_DEVICE_ID", true),
		Countries:       map[string]bool{},
		DCCacheTTL:      60 * time.Second,
	}
	raw, ok := os.LookupEnv("THROTTLE_COUNTRIES")
	if !ok {
		raw = "ru,rus"
	}
	for _, c := range strings.Split(raw, ",") {
		if c = strings.ToLower(strings.TrimSpace(c)); c != "" {
			cfg.Countries[c] = true
		}
	}
	if v := os.Getenv("THROTTLE_DC_CACHE_TTL"); v != "" {
		if sec, err := strconv.ParseFloat(v, 64); err == nil && sec > 0 {
			cfg.DCCacheTTL = time.Duration(sec * float64(time.Second))
		}
	}
	return cfg
}

func envBool(name string, def bool) bool {
	raw, ok := os.LookupEnv(name)
	if !ok {
		return def
	}
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "0", "false", "no", "off", "":
		return false
	}
	return true
}

// Filter — предфильтр с in-process кэшем datacenter-префиксов.
type Filter struct {
	cfg   Config
	redis SetReader

	dc         atomic.Pointer[dcCache]
	refreshing atomic.Bool
	refreshMu  sync.Mutex // сериализует фоновые обновления
}

type dcCache struct {
	prefixes map[string]bool
	expires  time.Time
}

// New — redis может быть nil: тогда datacenter-проверка отключена (fail-open).
func New(cfg Config, redis SetReader) *Filter {
	return &Filter{cfg: cfg, redis: redis}
}

// Check — причина отсечения либо "" (пропустить дальше). Только синхронные
// O(1)-операции; Redis не трогается.
func (f *Filter) Check(d Device) Reason {
	if f == nil || !f.cfg.Enabled {
		return ""
	}
	// 1. Бот/краулер UA — самая дешёвая проверка первой.
	if d.UA != "" && botUA.MatchString(d.UA) {
		return ReasonBotUA
	}
	// 2. Гео вне обслуживаемых стран (только если страна явно указана).
	if len(f.cfg.Countries) > 0 {
		if c := strings.ToLower(strings.TrimSpace(d.Country)); c != "" && !f.cfg.Countries[c] {
			return ReasonGeoUnserved
		}
	}
	// 3. Нет идентификатора устройства — нечем считать freq cap.
	if f.cfg.RequireDeviceID && d.IFA == "" && d.IP == "" && d.IPv6 == "" {
		return ReasonNoDeviceID
	}
	// 4. Datacenter-подсеть (кэш из Redis; пустой кэш = пропуск).
	if d.IP != "" && ipMatchesDC(d.IP, f.dcPrefixes()) {
		return ReasonDatacenterIP
	}
	return ""
}

// dcPrefixes — актуальный набор префиксов; никогда не блокирует hot path:
// при устаревании отдаёт stale-значение и запускает фоновое обновление.
func (f *Filter) dcPrefixes() map[string]bool {
	if f.redis == nil {
		return nil
	}
	cur := f.dc.Load()
	if cur != nil && time.Now().Before(cur.expires) {
		return cur.prefixes
	}
	if f.refreshing.CompareAndSwap(false, true) {
		go f.refresh()
	}
	if cur != nil {
		return cur.prefixes
	}
	return nil // холодный старт: прогрев запущен, текущий запрос пропускаем
}

func (f *Filter) refresh() {
	defer f.refreshing.Store(false)
	f.refreshMu.Lock()
	defer f.refreshMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	members, err := f.redis.SMembers(ctx, DCSubnetsKey)
	if err != nil {
		slog.Warn("prefilter dc subnet cache refresh failed (fail-open)", "err", err)
		// Redis недоступен: продлеваем stale, чтобы не долбить его каждым запросом.
		if cur := f.dc.Load(); cur != nil {
			f.dc.Store(&dcCache{prefixes: cur.prefixes, expires: time.Now().Add(f.cfg.DCCacheTTL)})
		} else {
			f.dc.Store(&dcCache{prefixes: nil, expires: time.Now().Add(f.cfg.DCCacheTTL)})
		}
		return
	}
	set := make(map[string]bool, len(members))
	for _, m := range members {
		set[m] = true
	}
	f.dc.Store(&dcCache{prefixes: set, expires: time.Now().Add(f.cfg.DCCacheTTL)})
}

// Refresh — синхронное обновление кэша (старт биддера, тесты).
func (f *Filter) Refresh() {
	if f == nil || f.redis == nil {
		return
	}
	if f.refreshing.CompareAndSwap(false, true) {
		f.refresh()
	}
}

// ipMatchesDC — startswith по каноничным префиксам "1.2.3." (/24) и "1.2." (/16).
// IPv6 в datacenter-списке не поддерживается (как в Python).
func ipMatchesDC(ip string, prefixes map[string]bool) bool {
	if ip == "" || len(prefixes) == 0 {
		return false
	}
	parts := strings.Split(ip, ".")
	if len(parts) != 4 {
		return false
	}
	return prefixes[parts[0]+"."+parts[1]+"."+parts[2]+"."] || prefixes[parts[0]+"."+parts[1]+"."]
}
