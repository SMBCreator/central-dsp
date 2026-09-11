# Central DSP — целевая архитектура

Документ описывает **текущую** production-архитектуру по состоянию кода
(2026-09-11). Планы и незакрытые долги — только в
`docs/audit/remediation_plan_2026-09-06.md`. Исторические варианты
(Java RTB4FREE, Python-биддер как serving runtime, Celery, старый Vue) удалены
из репозитория; их описание доступно только через Git history.

## 0. Зафиксированные решения

```text
Production RTB bidder:        Go bidder only (bidder-go/).
Legacy Java RTB4FREE:         deprecated and removed (C1c, 2026-09-11).
Python RTB serving:           deprecated; POST /rtb/bid в backend удаляется
                              отдельным шагом C1d.
Python process_bid_request:   reference implementation for parity tests only,
                              not a production RTB serving runtime.
Scheduler:                    PostgreSQL scheduled_jobs + backend/scheduler.py.
Celery / Beat / Flower:       not used in target production architecture.
Vercel:                       may be used only for preview/frontend deployment;
                              not part of production runtime and not a source
                              of scheduler configuration.
Simulator:                    в production вызывает только Go bidder; при его
                              недоступности — 503, без подмены на Python (C1d).
```

## 1. Компоненты

```text
                 SSP (OpenRTB 2.5/2.6)                  Браузер пользователя
                        │                                       │
            POST /rtb/bid/{key}  /rtb/loss          nurl/burl/imp/click/view
                        │                                       │
   ┌────────────────────▼───────────────────────────────────────▼────────────┐
   │ Caddy (compose.prod.yml, :80/:443)                                      │
   │   /rtb/*  → bidder-go:8090      /api/* → backend:8000 (prefix strip)    │
   │   /uploads/* → backend:8000     остальное → frontend:3000               │
   └──────────┬───────────────────────────────┬──────────────────┬───────────┘
              │                               │                  │
      ┌───────▼────────┐             ┌────────▼───────┐   ┌──────▼──────┐
      │ bidder-go      │  /rtb-feed  │ backend        │   │ frontend    │
      │ Go, :8090      │◄────────────│ FastAPI, :8000 │◄──│ Next.js     │
      │ аукцион        │  reload ►   │ management API │   │ кабинет     │
      └───┬────────┬───┘             └──┬─────────┬───┘   └─────────────┘
          │        │                    │         │
          │   rtb:events (stream)       │         │      ┌──────────────┐
          │        └────────────────────►         │      │ scheduler    │
          │           events_consumer   │         │◄─────│ python       │
          │                             │         │ /cron│ scheduler.py │
      ┌───▼──────────┐          ┌───────▼───────┐ │      └──────┬───────┘
      │ Redis 7      │          │ PostgreSQL 15 │◄┘             │
      │ бюджет-гейт, │          │ источник      │◄──────────────┘
      │ freq-cap,    │          │ истины,       │   scheduled_jobs
      │ кэш, stream  │          │ ledger        │
      └──────────────┘          └───────────────┘

      Prometheus (profile monitoring) ← /metrics backend, bidder-go, scheduler
```

| Сервис | Образ / код | Порт (dev) | Роль |
|---|---|---|---|
| `bidder-go` | `bidder-go/` (distroless, `/bidder -healthcheck`) | 8090 | OpenRTB endpoint, единственный auction runtime |
| `backend` | `backend/` (FastAPI, uvicorn) | 8000 | Management API, callbacks, ОРД, биллинг, статистика, feed |
| `scheduler` | образ backend, `python scheduler.py` | 8001 (metrics) | Планировщик `/cron/*` |
| `frontend` | `frontend/` (Next.js) | 3000 | Кабинет |
| `postgres` | `postgres:15-alpine` | — | Источник истины |
| `redis` | `redis:7-alpine`, AOF | — | Горячее состояние |
| `caddy` | только `compose.prod.yml` | 80/443 | TLS и маршрутизация |
| `prometheus` | profile `monitoring` | 127.0.0.1:9091 | Метрики и алерты |

`compose.external.yml` заменяет `postgres`/`redis` на managed-инстансы; всё
остальное без изменений (`docs/DEPLOYMENT.md`).

## 2. Go bidder (`bidder-go/`)

Единственный процесс, который отвечает SSP. В Postgres **не ходит** — латентность
торгов. Все данные для решения либо в памяти, либо в Redis.

- **Вход:** `POST /rtb/bid/{endpoint_key}` + `X-CentralDSP-Token` партнёра
  (в БД хранится только sha256-хэш). `GET|POST /rtb/loss` — lurl.
- **Feed кандидатов:** периодический `GET {BACKEND_INTERNAL_URL}/rtb-feed/campaigns`
  и `/rtb-feed/partners` (Bearer `RTB_API_TOKEN`) в `internal/feed`; backend
  дёргает `POST /internal/feed/reload` при изменениях (`ssp_check.py`).
- **Решение:** `internal/prefilter` (боты/UA), `internal/targeting`
  (гео, устройство, dayparting в UTC, домены/списки, ключевые слова, аудитории),
  `internal/rtb` (OpenRTB-совместимость: PMP, native, video; сборка adm/VAST,
  erid в креативе), `internal/pctr` и `internal/shading` (цена),
  `internal/fx` (курсы, торги в RUB).
- **Деньги:** копейки `int64`; резерв бюджета — Lua-скрипт в Redis
  (`internal/redisx`, атомарный гейт), freq-cap и счётчики бизнес-дня там же.
- **Callbacks:** в BidResponse уходят подписанные (HMAC, `EVENT_TOKEN_SECRET`)
  URL на **backend**: `${EVENT_BASE_URL}/api/rtb/win?bid=…&price=${AUCTION_PRICE}`,
  `/api/rtb/burl`, `/api/rtb/click`, imp/view/vevent-пиксели. `EVENT_BASE_URL`
  обязан быть публичным — в production loopback/внутренний адрес = fail-fast.
- **События:** каждый bid/no-bid/loss → Redis Stream `rtb:events`
  (`redisx.EmitEvent`), схема в `docs/CONTRACTS.md` §2.1.
- **Наблюдаемость:** `GET /healthz`, `GET /metrics` (Prometheus).

Parity с Python-референсом: `internal/targeting/parity_test.go`,
`internal/rtb/parity_r03_test.go`, фикстуры `bidder-go/testdata/openrtb/*.json`
(их же гоняет `backend/tests/test_openrtb_compat.py`). Матрица —
`docs/audit/parity_matrix.md`.

## 3. Backend (`backend/`, FastAPI)

Слушает без префикса (`/auth/me`, `/campaigns`, …); Caddy срезает `/api`.

| Область | Модули | Замечания |
|---|---|---|
| Auth, сессии, RBAC | `auth.py`, `rbac.py`, `csrf.py`, `routers/auth_router.py`, `users_router.py` | 9 ролей (`rbac.ALL_ROLES`), матрица — `docs/audit/rbac_matrix.md`; `REGISTRATION_MODE=closed` в проде |
| Кампании и настройки закупки | `routers/campaigns.py`, `line_items.py`, `creatives.py`, `audiences_router.py`, `rules.py`, `scoring.py`, `targeting_contract.py` | `line_items.targeting` — JSONB-контракт UI → Pydantic → Go (`docs/CONTRACTS.md` §1) |
| Feed для Go | `routers/bidder_feed.py` | `/rtb-feed/campaigns`, `/rtb-feed/partners`; ОРД-гейт `feed_gate_sql` не отдаёт непромаркированные креативы |
| Event callbacks | `routers/rtb.py`: `/rtb/win`, `/burl`, `/imp`, `/click`, `/view`, `/vevent` | Проверка HMAC-токена, clamp `${AUCTION_PRICE}` к нашей ставке, идемпотентный биллинг (`billing_core.bill_win`), R-07: партнёр с `billing_event='burl'` списывается по burl |
| Python reference | `routers/rtb.py::process_bid_request`, `rtb_compat.py`, `prefilter.py`, `shading.py`, `scoring.py` | Только эталон для parity-тестов. `POST /rtb/bid/{key}` в backend удаляется в C1d |
| Биллинг | `billing_core.py`, `money.py`, `routers/billing.py`, `business_day.py` | Ledger в Postgres — источник истины по деньгам; наценка/представление — `stats_math.py` (`CONTRACTS.md` §5) |
| События и статистика | `events_consumer.py` (фоновая задача backend), `routers/stats.py`, `routers/share.py` | Единственный писатель `bid_events` с `event_type IN ('bid','loss')` для Go-трафика; `stats_hourly` — `CONTRACTS.md` §3 |
| ОРД / ЕРИР | `ord/` (`service.py`, `chain.py`, `ozon.py`, `mock.py`, `invoices.py`, `notifications.py`), `routers/ord_router.py` | Регистрация креативов (erid), цепочки договоров, месячная статистика (`/cron/ord-export`), ошибки ЕРИР; `ORD_LAUNCH_GUARD=on` блокирует активацию без erid |
| Supply | `routers/supply_partners.py`, `ssp_tokens.py`, `ssp_check.py` | Endpoint key + одноразовый токен партнёра; публичный URL биддера из `GET /settings/public` |
| Cron-задачи | `routers/cron.py` | `GET /cron/<job>` под `Bearer CRON_SECRET`; вызываются только планировщиком |
| Планировщик API | `routers/scheduler_router.py` | `GET/PATCH /scheduler/jobs`, `POST /scheduler/jobs/{name}/run` (admin/superadmin) |
| Симулятор | `routers/simulator.py` | Диагностический прогон BidRequest через Go bidder; правило C1d в §0 |
| Прочее | `audit.py` (audit_log мутаций), `notify.py`, `obs_metrics.py` (`/metrics`), `startup_checks.py` (fail-fast на секретах в проде), `migrate.py` | |

## 4. Планировщик (`backend/scheduler.py`)

- Расписание — таблица `scheduled_jobs` (cron из 5 полей, UTC), сеется из
  `DEFAULT_JOBS` при первом старте, дальше правится через API/UI и переживает релизы.
- Тик 15 с: захват просроченных задач `FOR UPDATE SKIP LOCKED` + lease с
  heartbeat (20 с) и expiry (90 с) — несколько реплик без дублей, упавшая реплика
  не блокирует задачу; результат пишет только владелец lease
  (`scheduler_lease_lost_total` = 0 в норме).
- Пропущенные запуски схлопываются в один; concurrency 4; каждая задача —
  `GET http://backend:8000/cron/<path>` с общим бюджетом `timeout_seconds`.
- Метрики `:8001/metrics`, алерты `SchedulerDown` / `SchedulerJobsOverdue` /
  `SchedulerDispatchFailing` / `SchedulerLeaseLost`; runbook
  `docs/runbooks/rtb_alerts.md#scheduler`. Детали — `docs/DEPLOYMENT.md` §9.1.

## 5. Хранилища

**PostgreSQL** (миграции `backend/migrations/*.sql`, runner `migrate.py`,
только аддитивные): пользователи и роли, рекламодатели/кампании/line items/
креативы/аудитории, `supply_partners`, ledger и балансы, `bid_events`
(партиции по дню, ретеншн `/cron/retention`), `stats_hourly`, ОРД-сущности
(договоры, версии креативов, выгрузки, ошибки ЕРИР), `scheduled_jobs` +
`scheduled_job_runs`, `audit_log`, `schema_migrations`.

**Redis** (см. `docs/CONTRACTS.md` §2 — кто пишет, кто читает, TTL):
бюджет-гейт и резервы (Lua), freq-cap, счётчики бизнес-дня (МСК-дата в ключе),
кэш кандидатов, `bidmeta:{bid_id}` для callbacks, stream `rtb:events`.
Потеря Redis допустима — деньги восстанавливаются из ledger,
`/cron/reconcile-reserves` сверяет резервы с БД.

## 6. Потоки

**Аукцион.** SSP → Caddy `/rtb/bid/{key}` → bidder-go: токен партнёра →
prefilter → кандидаты из памяти (feed) → таргетинг → цена (pCTR, shading, FX)
→ Lua-резерв в Redis → BidResponse с erid и подписанными callback-URL →
событие в `rtb:events`.

**Показ и деньги.** Браузер/SSP → `/api/rtb/win|burl` (backend): проверка
HMAC, clamp цены, идемпотентное списание в ledger, снятие резерва;
`/imp`, `/click`, `/view`, `/vevent` — счётчики и атрибуция. `events_consumer`
переносит `rtb:events` в `bid_events`/`stats_hourly` — знаменатель win-rate/eCPM.

**Изменение в кабинете.** UI → backend → Postgres → (при необходимости)
`POST bidder-go/internal/feed/reload`; иначе Go подтянет feed по TTL. Пауза
кампании видна биддеру в пределах TTL кэша.

**ОРД.** Креатив → регистрация (erid) через адаптер (`ozon.py` / `mock.py` без
ключа) → без erid feed не отдаёт креатив и активация блокируется →
`ord_export` раз в месяц (`scheduled_jobs`, `0 3 2 * *` UTC) → ошибки ЕРИР
подтягивает `ord_notifications`.

## 7. Безопасность и эксплуатация

- Секреты только через `.env`/окружение (`.env.example` — полный список);
  `startup_checks.py` и Go `config` падают на старте в production без
  `SESSION_SECRET`, `EVENT_TOKEN_SECRET`, `CRON_SECRET`, публичного `EVENT_BASE_URL`.
- Внутренние порты в production закрыты (`compose.prod.yml`, `!reset`), наружу
  только Caddy; backend доверяет `X-Forwarded-*` только из сети compose.
- Все мутации management API — в `audit_log`; финансовые операции — только роли
  `FINANCE_WRITE`.
- Тесты контрактов между слоями (`test_targeting_contract.py`,
  `test_redis_contracts.py`) парсят исходники соседних слоёв и падают при
  рассинхроне — правило «меняешь одну сторону — прогоняешь оба».
- Развёртывание, бэкапы, smoke-чеклист и пилот — `docs/DEPLOYMENT.md`;
  реакция на алерты — `docs/runbooks/rtb_alerts.md`; релиз — `docs/runbooks/release.md`.
