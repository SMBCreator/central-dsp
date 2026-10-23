// Central DSP Go bidder — entrypoint.
//
// Скелет из итерации audit/prod-readiness. Функциональная спека:
// docs/audit/go_bidder_plan.md + docs/audit/parity_matrix.md
// (Python backend/routers/rtb.py — источник истины по поведению).
package main

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"runtime/debug"
	"strings"
	"syscall"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/config"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/prefilter"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/rtb"
)

// redisSetReader — адаптер redisx.Client -> prefilter.SetReader (SMEMBERS).
type redisSetReader struct{ c *redisx.Client }

func (r redisSetReader) SMembers(ctx context.Context, key string) ([]string, error) {
	return r.c.Raw().SMembers(ctx, key).Result()
}

// isProduction — как startup_checks.is_production в backend.
func isProduction() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV"))) {
	case "production", "prod":
		return true
	}
	return false
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// defaultTrustedProxies — loopback и приватные сети (RFC 1918 / ULA): Caddy
// в docker-сети compose или на той же машине. Биддер не должен быть доступен
// с публичного адреса напрямую — только через прокси.
const defaultTrustedProxies = "127.0.0.0/8,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"

func main() {
	// `bidder -healthcheck` — Docker HEALTHCHECK для distroless-образа (нет
	// sh/wget): GET /livez на собственном порту, exit 0 только на 200. Liveness,
	// не readiness (audit O-05): фид/Redis проверяет /healthz для деплоя и
	// балансировщика, а Docker не должен считать живой процесс unhealthy из-за
	// отказа зависимости.
	if len(os.Args) > 1 && (os.Args[1] == "-healthcheck" || os.Args[1] == "--healthcheck") {
		os.Exit(healthcheck(env("BIDDER_ADDR", ":8090")))
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	// Audit S-04: секреты проверяются до открытия порта. В production
	// плейсхолдер/короткий секрет = процесс не стартует, а не «торгует с
	// подделываемыми event-токенами».
	if warns, err := config.Assert(config.OSEnv); err != nil {
		slog.Error("startup config check failed", "err", err.Error())
		os.Exit(1)
	} else {
		for _, w := range warns {
			slog.Warn("config (dev, not fatal)", "problem", w)
		}
	}

	addr := env("BIDDER_ADDR", ":8090")

	// Audit R-02: FEED_MAX_AGE — сколько торговать по старому снапшоту, когда
	// backend недоступен. Дальше — no-bid (feed_stale) и /healthz 503.
	feedMaxAge := feed.DefaultMaxAge
	if raw := os.Getenv("FEED_MAX_AGE"); raw != "" {
		d, err := time.ParseDuration(raw)
		if err != nil || d <= 0 {
			slog.Error("startup config check failed", "err", "FEED_MAX_AGE must be a positive duration like 120s", "got", raw)
			os.Exit(1)
		}
		feedMaxAge = d
	}
	store := feed.NewStore(feed.Config{
		BackendURL: env("BACKEND_INTERNAL_URL", "http://backend:8000"),
		FeedToken:  env("RTB_API_TOKEN", os.Getenv("INTERNAL_FEED_TOKEN")), // Bearer для GET /rtb-feed/*
		Refresh:    30 * time.Second,
		MaxAge:     feedMaxAge,
	})

	// Redis: бюджеты (Lua), freq caps, QPS, shading-факторы, bidmeta.
	// Без Redis биддер работает в degraded-режиме (без бюджет/freq-гейтов) —
	// допустимо только в dev.
	// Дефолт localhost — только dev: в production REDIS_URL обязателен
	// (config.Assert выше), иначе биддер и backend считают бюджет в разных Redis.
	var rc *redisx.Client
	if url := env("REDIS_URL", "redis://localhost:6379/0"); url != "" {
		var err error
		rc, err = redisx.New(url)
		if err != nil {
			slog.Error("redis init failed", "err", err)
			os.Exit(1)
		}
		pingCtx, cancelPing := context.WithTimeout(context.Background(), 2*time.Second)
		if err := rc.Ping(pingCtx); err != nil {
			slog.Warn("redis unreachable at startup (degraded mode)", "err", err)
		} else {
			// Audit M-03: Redis хранит деньги торгов. Любая eviction-политика в
			// production = возможный перерасход бюджета => не стартуем.
			rep, err := rc.CheckDurability(pingCtx)
			switch {
			case err != nil:
				slog.Warn("redis durability check skipped", "err", err)
			case rep.Fatal != "":
				if isProduction() {
					slog.Error("unsafe redis for production", "problem", rep.Fatal)
					os.Exit(1)
				}
				slog.Warn("unsafe redis (dev, not fatal)", "problem", rep.Fatal)
			default:
				if rep.Warning != "" {
					slog.Warn("redis persistence", "warning", rep.Warning)
				}
				slog.Info("redis durability ok", "policy", rep.MaxmemoryPolicy, "appendonly", rep.AppendOnly)
			}
		}
		cancelPing()
	}

	h := rtb.NewHandler(store, rc)
	// Тот же секрет, что и для фида: backend подписывает им автопроверку SSP
	// (обход IP-whitelist) и запрос принудительного рефреша фида.
	internalToken := env("RTB_API_TOKEN", os.Getenv("INTERNAL_FEED_TOKEN"))
	h.SetInternalToken(internalToken)

	// Audit S-06: X-Forwarded-For / X-Real-IP принимаются только от доверенных
	// прокси (Caddy в compose-сети / на loopback). Дефолт — loopback + приватные
	// сети, чего достаточно для стандартного деплоя; за внешним LB задайте его
	// адреса явно. Пустое значение ("TRUSTED_PROXIES=") = никому не верить.
	trustedProxiesRaw, trustedProxiesSet := os.LookupEnv("TRUSTED_PROXIES")
	if !trustedProxiesSet {
		trustedProxiesRaw = defaultTrustedProxies
	}
	trustedProxies, err := rtb.ParseTrustedProxies(trustedProxiesRaw)
	if err != nil {
		slog.Error("startup config check failed", "err", err.Error())
		os.Exit(1)
	}
	h.SetTrustedProxies(trustedProxies)
	slog.Info("trusted proxies for X-Forwarded-For", "networks", trustedProxiesRaw)

	// Audit R-03: тот же предфильтр, что в Python (THROTTLE_* env, Redis SET
	// prefilter:dc_subnets). Без Redis datacenter-проверка выключена (fail-open).
	pfCfg := prefilter.FromEnv()
	var dcSource prefilter.SetReader
	if rc != nil {
		dcSource = redisSetReader{rc}
	}
	pf := prefilter.New(pfCfg, dcSource)
	pf.Refresh()
	h.SetPrefilter(pf)
	// ID sync (docs/ID_SYNC.md): по умолчанию off — идентификация как раньше.
	idSync := os.Getenv("ID_SYNC_ENABLED")
	h.SetIDSync(idSync == "1" || strings.EqualFold(idSync, "true"))
	slog.Info("id sync", "enabled", h.IDSyncEnabled())
	slog.Info("prefilter", "enabled", pfCfg.Enabled, "countries", len(pfCfg.Countries),
		"require_device_id", pfCfg.RequireDeviceID, "dc_cache", dcSource != nil)

	geoOpts := rtb.GeoOptionsFromEnv()
	h.SetGeoOptions(geoOpts)
	slog.Info("geo v2", "latlon", geoOpts.LatLon, "radius_km", geoOpts.RadiusKM)

	// Prometheus-метрики: счётчики hot path + gauges состояния (GAP-011).
	reg := metrics.New()
	h.SetMetrics(reg)
	reg.SetWinWithoutImpSource(redisx.WinWithoutImp.Load)
	reg.RegisterGauge("bidder_feed_age_seconds", func() float64 { return store.Age().Seconds() })
	reg.RegisterGauge("bidder_feed_campaigns", func() float64 { return float64(store.Len()) })
	reg.RegisterGauge("bidder_feed_max_age_seconds", func() float64 { return store.MaxAge().Seconds() })
	reg.RegisterGauge("bidder_feed_stale", func() float64 {
		if store.Fresh() {
			return 0
		}
		return 1
	})
	reg.RegisterGauge("bidder_feed_refresh_errors_total", func() float64 { return float64(store.RefreshErrors()) })
	// Один снимок Readiness на скрейп: bidder_redis_up и bidder_ready не могут
	// разойтись между собой и с ответом /healthz (audit O-05).
	readinessGauge := func(pick func(rtb.Readiness) bool) func() float64 {
		return func() float64 {
			if pick(h.Readiness(context.Background())) {
				return 1
			}
			return 0
		}
	}
	reg.RegisterGauge("bidder_redis_up", readinessGauge(func(rd rtb.Readiness) bool { return rd.RedisOK }))
	reg.RegisterGauge("bidder_ready", readinessGauge(func(rd rtb.Readiness) bool { return rd.Ready }))

	mux := http.NewServeMux()
	mux.HandleFunc("POST /rtb/bid/{endpointKey}", h.Bid)
	mux.HandleFunc("POST /rtb/win", h.Win)
	// lurl SSP вызывают GET-ом (пиксель/серверный GET); POST оставлен для совместимости.
	mux.HandleFunc("GET /rtb/loss", h.Loss)
	mux.HandleFunc("POST /rtb/loss", h.Loss)
	mux.HandleFunc("POST /rtb/imp", h.Imp)
	mux.HandleFunc("GET /livez", h.Livez)
	mux.HandleFunc("GET /healthz", h.Healthz)
	mux.Handle("GET /metrics", reg.Handler())
	// Принудительный рефреш фида: backend дёргает перед автопроверкой SSP
	// (draft->testing должен попасть в снапшот сразу, а не через 30 с).
	// Только с internal-токеном; Caddy этот путь наружу не проксирует.
	mux.HandleFunc("POST /internal/feed/reload", h.FeedReload)
	// GEO v2: топ несопоставленных region/city от SSP (для geo_ssp_alias).
	mux.HandleFunc("GET /internal/geo/unmatched", h.GeoUnmatched)

	srv := &http.Server{
		Addr:              addr,
		Handler:           recoverMiddleware(mux, reg),
		ReadHeaderTimeout: 2 * time.Second,
		ReadTimeout:       5 * time.Second, // RTB timeout budget: ответ обязан уйти < 100ms
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	go store.Run(ctx)

	// Audit M-06: sweeper резервов. Бид без win/loss дольше DefaultReserveTTL
	// переводится в expired и возвращает деньги на все ключи. Терминальное
	// состояние ставит Lua (SET NX), поэтому несколько инстансов безопасны.
	if rc != nil {
		reg.RegisterGauge("bidder_reserve_pending", func() float64 {
			gctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
			defer cancel()
			return float64(rc.PendingCount(gctx))
		})
		go runReserveSweeper(ctx, rc, reg)
	}

	go func() {
		slog.Info("bidder-go listening", "addr", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("server error", "err", err)
			os.Exit(1)
		}
	}()

	<-ctx.Done()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_ = srv.Shutdown(shutdownCtx)
}

// runReserveSweeper — каждые 5 с снимает до 2000 истёкших резервов из pending:bids.
func runReserveSweeper(ctx context.Context, rc *redisx.Client, reg *metrics.Registry) {
	t := time.NewTicker(5 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			sctx, cancel := context.WithTimeout(ctx, 3*time.Second)
			expired, orphans, err := rc.SweepExpired(sctx, time.Now(), 2000)
			cancel()
			if err != nil {
				slog.Warn("reserve sweeper", "err", err)
				continue
			}
			if expired > 0 || orphans > 0 {
				reg.IncReserveExpired(expired)
				reg.IncReserveOrphans(orphans)
				slog.Info("reserve sweeper", "expired", expired, "orphans", orphans)
			}
		}
	}
}

// recoverMiddleware не даёт одному кривому bid-запросу (nil в неожиданном
// месте OpenRTB, паника в таргетинге) уронить весь процесс: net/http сам
// ловит паники, но пишет только в stderr и без метрики. Здесь — slog со
// стеком, счётчик bidder_panics_total и no-bid (204) SSP вместо обрыва TCP.
// http.ErrAbortHandler пропускаем: это штатный способ оборвать ответ.
func recoverMiddleware(next http.Handler, reg *metrics.Registry) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			rec := recover()
			if rec == nil {
				return
			}
			if rec == http.ErrAbortHandler {
				panic(rec)
			}
			reg.IncPanic()
			slog.Error("handler panic recovered",
				"method", r.Method, "path", r.URL.Path, "panic", rec,
				"stack", string(debug.Stack()))
			// Заголовки могли уже уйти — тогда просто завершаем ответ.
			w.WriteHeader(http.StatusNoContent)
		}()
		next.ServeHTTP(w, r)
	})
}

// healthcheck возвращает код процесса для Docker HEALTHCHECK: 0 = /livez
// ответил 200 (процесс и HTTP-сервер живы), 1 = всё остальное.
// livezPath — единственный путь, который опрашивает Docker HEALTHCHECK.
const livezPath = "/livez"

func healthcheck(addr string) int {
	host := "127.0.0.1"
	_, port, err := net.SplitHostPort(addr)
	if err != nil {
		port = "8090"
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get("http://" + net.JoinHostPort(host, port) + livezPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "healthcheck:", err)
		return 1
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintln(os.Stderr, "healthcheck: status", resp.StatusCode)
		return 1
	}
	return 0
}
