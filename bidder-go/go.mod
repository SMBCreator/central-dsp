module github.com/SMBCreator/central-dsp/bidder-go

go 1.25.14

// Планируемые зависимости имплементационной итерации (сейчас скелет — stdlib only):
//   github.com/redis/go-redis/v9   — freq caps / budget reserve / shading factors / QPS
//   github.com/jackc/pgx/v5        — прямое чтение кампаний (fallback к feed)
//   github.com/prometheus/client_golang — метрики /metrics

require (
	github.com/alicebob/miniredis/v2 v2.39.0
	github.com/google/uuid v1.6.0
	github.com/redis/go-redis/v9 v9.22.0
)

require (
	github.com/cespare/xxhash/v2 v2.3.0 // indirect
	github.com/yuin/gopher-lua v1.1.1 // indirect
	go.uber.org/atomic v1.11.0 // indirect
	golang.org/x/sys v0.30.0 // indirect
)
