# Central DSP

Self-hosted programmatic DSP для российского рынка: OpenRTB 2.5/2.6 биддер на Go,
кабинет рекламодателя/агентства на Next.js, management API на FastAPI, биллинг
в копейках с ledger в PostgreSQL, маркировка рекламы через ОРД (38-ФЗ/347-ФЗ),
RBAC на 9 ролей.

## Стек и границы ответственности

| Компонент | Технология | Что делает |
|---|---|---|
| `bidder-go/` | Go 1.25, Redis | **Единственный production RTB runtime**: `POST /rtb/bid/{endpoint_key}`, `/rtb/loss`; таргетинг, прайсинг, bid shading, pCTR, budget-gate в Redis (Lua), события в Redis Stream `rtb:events` |
| `backend/` | Python 3.12, FastAPI, asyncpg | Management API, аутентификация и RBAC, биллинг/ledger, event-callbacks (`/rtb/win`, `/burl`, `/imp`, `/click`, `/view`, `/vevent`), ОРД, статистика, feed кандидатов для Go (`/rtb-feed/*`), `/cron/*`, симулятор |
| `backend/scheduler.py` | тот же образ backend | Планировщик `/cron/*` по таблице `scheduled_jobs` (PostgreSQL). Единственный production scheduler |
| `frontend/` | Next.js 16, React 19, Tailwind | Кабинет: кампании, креативы, аудитории, отчёты, биллинг, ОРД, supply-партнёры, пользователи |
| PostgreSQL 15 | — | Источник истины: сущности, ledger, `bid_events` (партиции), `stats_hourly`, `scheduled_jobs`, `audit_log` |
| Redis 7 | — | Горячий путь биддера: бюджет-гейт, счётчики, кэш кандидатов, `rtb:events` |
| Caddy | `compose.prod.yml` | TLS, маршрутизация `/api/*` → backend, `/rtb/*` → bidder-go, остальное → frontend |
| Prometheus | profile `monitoring` | Метрики backend / bidder-go / scheduler, алерты `ops/prometheus/alerts.yml` |

Явно зафиксированные решения (подробно — `docs/ARCHITECTURE.md`):

```text
Production RTB bidder:        Go bidder only (bidder-go/).
Legacy Java RTB4FREE:         deprecated and removed (C1c).
Python RTB serving:           deprecated; removed separately in C1d.
Python process_bid_request:   reference implementation for parity tests only,
                              not a production RTB serving runtime.
Scheduler:                    PostgreSQL scheduled_jobs + backend/scheduler.py.
Celery / Beat / Flower:       not used in target production architecture.
Vercel:                       preview/frontend deployment only; not part of
                              production runtime, not a source of scheduler config.
```

## Быстрый старт (dev, Docker Compose)

```bash
git clone https://github.com/SMBCreator/central-dsp.git
cd central-dsp
cp .env.example .env          # заполнить секреты: openssl rand -hex 32
docker compose up -d --build
```

| Сервис | Адрес (dev) |
|---|---|
| Кабинет | http://127.0.0.1:3000 |
| API / Swagger | http://127.0.0.1:8000/docs |
| Go bidder | http://127.0.0.1:8090/healthz |
| Prometheus (`--profile monitoring`) | http://127.0.0.1:9091 |

Production-топология (`compose.prod.yml`: Caddy 80/443, внутренние порты
закрыты) и вариант с managed PostgreSQL/Redis (`compose.external.yml`) —
в `docs/DEPLOYMENT.md`.

## Тесты

```bash
# backend: нужен PostgreSQL (DATABASE_URL=postgresql://...), схема — python migrate.py
cd backend && pip install . pytest pytest-asyncio httpx "fakeredis[lua]" pyyaml && pytest

# Go bidder (включая Go/Python parity tests)
cd bidder-go && go test ./...

# frontend
cd frontend && pnpm install && pnpm exec tsc --noEmit && pnpm test && pnpm build
```

CI (`.github/workflows/ci.yml`) прогоняет то же самое плюс `docker compose
config` для трёх топологий, `caddy validate` и `promtool check rules`.

## Структура репозитория

```text
backend/          FastAPI: routers/, migrations/, scheduler.py, ord/, tests/
bidder-go/        Go bidder: cmd/bidder, internal/{rtb,targeting,shading,pctr,feed,redisx,...}
frontend/         Next.js кабинет: app/, components/, lib/
compose.yml       dev-стек; compose.prod.yml / compose.external.yml — overlays
Caddyfile         реверс-прокси production
ops/prometheus/   prometheus.yml, alerts.yml
docs/             см. ниже
```

## Документация

| Документ | Назначение |
|---|---|
| `docs/ARCHITECTURE.md` | Целевая архитектура: компоненты, потоки данных, контракты, решения |
| `docs/DEPLOYMENT.md` | Dev / prod / external Compose, Caddy, секреты, миграции, планировщик, бэкапы, smoke и пилот |
| `docs/CONTRACTS.md` | Контракты между UI, FastAPI, Postgres, Redis и Go bidder и тесты, которые их охраняют |
| `docs/audit/remediation_plan_2026-09-06.md` | **Единственный активный план работ** (roadmap + remediation) |
| `docs/runbooks/` | Действия при алертах и релизах |
| `docs/audit/` | RBAC-матрица, parity matrix, audit evidence, security notes |
| `docs/ord_checklist.md`, `docs/audit/ord_spec.md` | Интеграция с ОРД / ЕРИР |

Исторические планы (RTB4FREE/Java, старый Vue, Celery) удалены из рабочего
дерева и доступны только через Git history.

## Лицензия

Proprietary. Все права принадлежат владельцу репозитория.
