module github.com/SMBCreator/central-dsp/bidder-go

go 1.25

// Планируемые зависимости имплементационной итерации (сейчас скелет — stdlib only):
//   github.com/redis/go-redis/v9   — freq caps / budget reserve / shading factors / QPS
//   github.com/jackc/pgx/v5        — прямое чтение кампаний (fallback к feed)
//   github.com/prometheus/client_golang — метрики /metrics
