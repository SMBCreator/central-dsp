# CentralDSP — Прод-runbook (гибрид: Vercel + always-on bid-сервис)

Актуален для ветки `feat/prod-hardening`. Архитектура: Vercel (UI + management
API + ОРД + cron) и отдельный always-on bid-сервис (Docker, Fly.io/VPS), общие
Neon Postgres и Upstash Redis.

## 1. Секреты и env

Полный список — в `.env.example` (корень репо). Генерация секретов:

```bash
openssl rand -hex 32   # SESSION_SECRET, EVENT_TOKEN_SECRET, CRON_SECRET
```

| Переменная | Vercel | Bid-сервис | Примечание |
|---|---|---|---|
| `DATABASE_URL` | да | да (pooled) | Neon, sslmode=require |
| `REDIS_URL` | да | да | Upstash, общий для всех реплик |
| `SESSION_SECRET` | да | — | JWT сессии UI |
| `EVENT_TOKEN_SECRET` | да | да | ДОЛЖЕН совпадать: подпись event-URL |
| `RTB_CURRENCY` | да | да | `RUB` |
| `RTB_CACHE_TTL` | — | да | 30 (сек) |
| `ORD_OZON_API_KEY` | да | — | без ключа = mock-адаптер |
| `ORD_LAUNCH_GUARD` | да | — | `on` в проде, всегда |
| `REGISTRATION_MODE` | да | — | `closed` в проде |
| `CRON_SECRET` | да | — | защита /cron/* |
| `RTB_ALLOW_UNSIGNED_EVENTS` | НЕТ | НЕТ | только dev/симулятор |

Ротация: `EVENT_TOKEN_SECRET` ротируется с даунтаймом event-трекинга ≤ TTL
токена — сначала деплой bid-сервиса с новым секретом, затем Vercel.

## 2. Миграции

Runner: `backend/migrate.py` (advisory lock, транзакция на миграцию,
`schema_migrations`).

```bash
# Существующая прод-БД (схема уже есть, зафиксировать baseline без выполнения):
python backend/migrate.py --stamp-all --up-to 000
python backend/migrate.py            # применит 007+ по порядку

# Чистая БД (staging/восстановление):
python backend/migrate.py            # baseline 000 + все по порядку

# Проверка статуса:
python backend/migrate.py --status
```

Правило: миграции только аддитивные. Перед миграцией прода — снапшот
(Neon branch с прода = мгновенный бэкап).

## 3. Деплой

### 3.1 Vercel (UI + management API)
1. Merge PR → main → авто-деплой Vercel.
2. Root directory: `frontend/`; API — serverless-функции backend/.
3. Прогнать миграции ДО деплоя кода, который их требует.

### 3.2 Bid-сервис (Fly.io или VPS)
```bash
cd backend
docker build -f Dockerfile.bid -t centraldsp-bidder .
# Fly.io:
fly launch --dockerfile Dockerfile.bid --name centraldsp-bidder
fly secrets set DATABASE_URL=... REDIS_URL=... EVENT_TOKEN_SECRET=... RTB_CURRENCY=RUB
fly scale count 2            # min 2 реплики, одна зона с Upstash-регионом
```
- Health: `GET /health` (liveness), `GET /ready` (DB+Redis, для деплой-гейта).
- Регион: тот же, что Upstash Redis (RTT < 1ms — иначе p99-бюджет не сходится).
- SSP получают URL: `https://bidder.<domain>/rtb/bid/{endpoint_key}`
  + заголовок `x-centraldsp-token: <auth_token партнёра>`.

## 4. Мониторинг и алерты
- Liveness/readiness выше; Fly.io checks или UptimeRobot на оба URL.
- Логи: bid-сервис пишет только ошибки (горячий путь без I/O-логов);
  `fly logs` / journald. Алерты на: error rate > 1%, `/ready` = 503,
  p99 > 80ms (метрика SSP-стороны), аномальный спад bid rate.
- Budget anomaly: дневной cron сверяет `SUM(ledger.spend)` с балансами
  (`/cron/*` уже считает агрегаты) — расхождение = алерт.

## 5. Бэкапы и восстановление
- Neon PITR: проверить retention (Business — 30 дней, Free — 24 ч) в консоли.
- Восстановление: Neon branch с точки во времени → smoke на branch →
  переключить `DATABASE_URL`. Схема воспроизводима с нуля миграциями
  (проверено в CI).
- Redis — эфемерные данные (кэши, счётчики периодов): потеря допустима,
  ledger в Postgres — источник истины по деньгам.

## 6. Smoke-чеклист прода (после каждого деплоя)
1. `GET /ready` bid-сервиса → 200, database=ok, redis=ok.
2. Создать SSP-партнёра в UI → получить `endpoint_key` + `auth_token`.
3. Тестовый bid (валидный токен) → 200 с adm; без токена → 401; чужой → 403.
4. В adm присутствует erid (кампания с зарегистрированным креативом).
5. Win notice по подписанному nurl → списание появилось в ledger ровно один раз
   (повторный nurl → duplicate, без списания).
6. Imp/click пиксели → счётчики в статистике растут.
7. Кампания без erid → активация блокируется с 409.
8. Manager не может `POST /billing/deposit` (403); мутация видна в audit_log.

## 7. Пилот (после smoke)
1. Подключить 1 SSP, QPS-лимит партнёра = 10.
2. 1 кампания, маленький дневной бюджет, все креативы с erid.
3. Неделя мониторинга: списания vs статистика SSP, ledger сходится,
   латентность p99 от SSP.
4. Ramp-up: QPS 10 → 50 → 200; масштабировать репликами bid-сервиса.
5. По приходу ключа ОРД Ozon: сверить пути API клиента со спекой,
   переключить mock → real, перерегистрировать пилотные креативы,
   проверить месячную выгрузку 1-го числа.

## 8. Известные ограничения MVP
- Java-биддер (`bidder/`) и `legacy/` — frozen, не деплоятся.
- Ozon ОРД клиент написан по публичной документации; финальная сверка
  эндпоинтов — при получении API-ключа.
- p99 ≤ 80ms подтверждён расчётно (2 Redis roundtrip + кэш кандидатов);
  контрольный замер — в прод-топологии на пилоте.
