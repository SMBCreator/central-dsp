// Central DSP Go bidder — entrypoint.
//
// Скелет из итерации audit/prod-readiness. Функциональная спека:
// docs/audit/go_bidder_plan.md + docs/audit/parity_matrix.md
// (Python backend/routers/rtb.py — источник истины по поведению).
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/SMBCreator/central-dsp/bidder-go/internal/feed"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/metrics"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/redisx"
	"github.com/SMBCreator/central-dsp/bidder-go/internal/rtb"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	addr := env("BIDDER_ADDR", ":8090")

	store := feed.NewStore(feed.Config{
		BackendURL: env("BACKEND_INTERNAL_URL", "http://backend:8000"),
		FeedToken:  env("RTB_API_TOKEN", os.Getenv("INTERNAL_FEED_TOKEN")), // Bearer для GET /rtb-feed/*
		Refresh:    30 * time.Second,
	})

	// Redis: бюджеты (Lua), freq caps, QPS, shading-факторы, bidmeta.
	// Без Redis биддер работает в degraded-режиме (без бюджет/freq-гейтов) —
	// допустимо только в dev.
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
		}
		cancelPing()
	}

	h := rtb.NewHandler(store, rc)
	// Тот же секрет, что и для фида: backend подписывает им автопроверку SSP
	// (обход IP-whitelist) и запрос принудительного рефреша фида.
	internalToken := env("RTB_API_TOKEN", os.Getenv("INTERNAL_FEED_TOKEN"))
	h.SetInternalToken(internalToken)

	// Prometheus-метрики: счётчики hot path + gauges состояния (GAP-011).
	reg := metrics.New()
	h.SetMetrics(reg)
	reg.RegisterGauge("bidder_feed_age_seconds", func() float64 { return store.Age().Seconds() })
	reg.RegisterGauge("bidder_feed_campaigns", func() float64 { return float64(store.Len()) })
	reg.RegisterGauge("bidder_redis_up", func() float64 {
		if rc == nil {
			return 0
		}
		pingCtx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
		defer cancel()
		if rc.Ping(pingCtx) == nil {
			return 1
		}
		return 0
	})

	mux := http.NewServeMux()
	mux.HandleFunc("POST /rtb/bid/{endpointKey}", h.Bid)
	mux.HandleFunc("POST /rtb/win", h.Win)
	// lurl SSP вызывают GET-ом (пиксель/серверный GET); POST оставлен для совместимости.
	mux.HandleFunc("GET /rtb/loss", h.Loss)
	mux.HandleFunc("POST /rtb/loss", h.Loss)
	mux.HandleFunc("POST /rtb/imp", h.Imp)
	mux.HandleFunc("GET /healthz", h.Healthz)
	mux.Handle("GET /metrics", reg.Handler())
	// Принудительный рефреш фида: backend дёргает перед автопроверкой SSP
	// (draft->testing должен попасть в снапшот сразу, а не через 30 с).
	// Только с internal-токеном; Caddy этот путь наружу не проксирует.
	mux.HandleFunc("POST /internal/feed/reload", h.FeedReload)

	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 2 * time.Second,
		ReadTimeout:       5 * time.Second, // RTB timeout budget: ответ обязан уйти < 100ms
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	go store.Run(ctx)

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
