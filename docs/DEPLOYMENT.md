# Развёртывание CentralDSP на сервере

Пошаговый гайд: от голого VPS до работающего стека (Postgres + Redis + Backend API +
Go-биддер + Frontend + планировщик) за Caddy с HTTPS — всё внутри Docker Compose.

Файлы стека (audit O-01):

| Файл | Назначение |
|---|---|
| `compose.yml` | база / локальная разработка: порты на `127.0.0.1`, hot reload backend |
| `compose.prod.yml` | production override: Caddy (80/443, TLS Let's Encrypt), внутренние порты закрыты, `restart: always` |
| `compose.external.yml` | managed Postgres/Redis вместо встроенных контейнеров |
| `Caddyfile` | публичная маршрутизация: `/api/*`→backend, `/rtb/*`→bidder-go, `/uploads/*`→backend, остальное→frontend |
| `ops/prometheus/` | Prometheus + правила алертов, только `--profile monitoring` |

Единственный боевой биддер — **bidder-go**. Python-ветка bid-запросов и legacy Java
RTB4FREE-биддер в стек не входят.

---

## 1. Минимальные требования (тестовый контур)

| Ресурс | Минимум (тест) | Рекомендация (пилот с 1 SSP, ~500 QPS) |
|---|---|---|
| CPU | 2 vCPU | 4–8 vCPU |
| RAM | 4 GB | 8–16 GB |
| Диск | 40 GB SSD | 100+ GB NVMe |
| ОС | Ubuntu 22.04/24.04 LTS | то же |
| Сеть | публичный IPv4, домен | то же + низкий RTT до SSP |

Для тестов всё умещается на одном VPS. Для боевого трафика биддер выносится
на отдельную машину рядом с дата-центром SSP.

## 2. Подготовка сервера

```bash
# Обновление и базовые пакеты
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ufw

# Docker + Compose plugin (нужен Compose >= 2.24.4: теги !override/!reset в compose.prod.yml)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker
docker compose version

# Firewall: только SSH, HTTP, HTTPS
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 3. Клонирование и конфигурация

```bash
git clone https://github.com/SMBCreator/central-dsp.git
cd central-dsp
cp .env.example .env
```

Заполните `.env` (все секреты генерируйте через `openssl rand -hex 32`; полный
список с комментариями — в `.env.example`):

```env
# --- Compose: production = база + Caddy/TLS ---
COMPOSE_FILE=compose.yml:compose.prod.yml
APP_ENV=production

# --- Публичные адреса ---
DOMAIN=ваш-домен.ру                     # хост для Caddy (сертификат Let's Encrypt)
ACME_EMAIL=ops@ваш-домен.ру
PUBLIC_BASE_URL=https://ваш-домен.ру    # из него строятся пиксель, /uploads, nurl/burl биддера
# PUBLIC_BIDDER_BASE_URL — не нужен: /rtb/* живёт на том же домене

# --- База данных (встроенный контейнер; DATABASE_URL compose соберёт сам) ---
POSTGRES_PASSWORD=<openssl rand -hex 16>

# --- Секреты (ОБЯЗАТЕЛЬНО уникальные, не из примеров!) ---
SESSION_SECRET=<openssl rand -hex 32>
RTB_API_TOKEN=<openssl rand -hex 32>
EVENT_TOKEN_SECRET=<openssl rand -hex 32>
CRON_SECRET=<openssl rand -hex 32>

# --- ОРД Озон (можно позже задать через UI настроек) ---
# ORD_OZON_API_KEY=<ключ из кабинета ОРД Озон>
```

Managed Postgres/Redis вместо встроенных: `COMPOSE_FILE=compose.yml:compose.external.yml:compose.prod.yml`
плюс `DATABASE_URL`/`REDIS_URL` — см. `.env.external.example` (требование к Redis:
`maxmemory-policy=noeviction`, иначе production не стартует).

В production backend и bidder-go **не поднимутся** с пустыми/плейсхолдерными
секретами, без `PUBLIC_BASE_URL`, с `EVENT_BASE_URL` на внутренний адрес и с
eviction-политикой Redis — это осознанные fail-fast проверки (audit S-04, M-03, O-01).

## 4. Запуск стека

```bash
# COMPOSE_FILE из .env уже включает compose.prod.yml -> поднимется и Caddy.
docker compose up -d --build

# Проверить статусы (все healthy; caddy ждёт healthy backend)
docker compose ps
docker compose logs backend --tail 20      # "startup_checks" без ERROR, миграции применены
docker compose logs bidder-go --tail 20
docker compose logs caddy --tail 20        # получен сертификат для DOMAIN
```

Миграции БД применяются автоматически при старте backend (`python migrate.py`).

В production внутренние порты с хоста сняты — проверяйте через Caddy или изнутри сети:

```bash
curl -s https://ваш-домен.ру/api/health                                   # -> {"status":"ok"}
docker compose exec bidder-go /bidder -healthcheck && echo bidder ok       # feed fresh, redis ok
curl -s https://ваш-домен.ру -o /dev/null -w "%{http_code}\n"              # frontend -> 200/307
```

Локальная разработка — только `compose.yml` (без `COMPOSE_FILE` в `.env`): порты
`127.0.0.1:8000/8090/3000`, каталог `backend/` смонтирован в контейнер (hot reload).

## 5. Реверс-прокси с HTTPS (Caddy внутри Compose)

Caddy — сервис `caddy` из `compose.prod.yml`, конфигурация — `Caddyfile` в корне
репозитория (монтируется read-only). Ничего ставить на хост не нужно; порты 80/443
публикует только он. Маршруты:

| Путь | Куда | Примечание |
|---|---|---|
| `/api/*` | `backend:8000` | префикс `/api` срезается (`handle_path`) |
| `/rtb/*` | `bidder-go:8090` | OpenRTB `POST /rtb/bid/{key}`, `lurl` `/rtb/loss`; без strip |
| `/uploads/*` | `backend:8000` | файлы креативов с volume `uploads_data` |
| остальное | `frontend:3000` | кабинет и `/ai-api/*` (route handlers Next) |

Лимит тела 60 МБ (видеокреативы), HSTS/nosniff, JSON-лог в stdout
(`docker compose logs caddy`). Сертификаты Let's Encrypt — в volume `caddy_data`,
продлеваются автоматически; `ACME_EMAIL` обязателен.

SSP-партнёру отдаёте URL вида `https://ваш-домен.ру/rtb/bid/<endpoint_key>` — это
маршрут на **bidder-go**. Кабинет (`/supply`, «скопировать URL») берёт базу из
`PUBLIC_BIDDER_BASE_URL` (по умолчанию = `PUBLIC_BASE_URL`), а не из origin браузера
(audit R-01). Отдельный RTB-хост (`rtb.ваш-домен.ру`) архитектурно зарезервирован —
закомментированный блок в конце `Caddyfile`; в первом релизе не включается.

Изменили `Caddyfile` — проверка и перезагрузка без даунтайма:

```bash
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
docker compose exec caddy caddy reload   --config /etc/caddy/Caddyfile
```

### 5.1. Файлы креативов (баннеры/видео)

Файлы хранятся на диске VPS в именованном Docker-volume `uploads_data`
(смонтирован в backend как `/data/uploads`, переменная `UPLOAD_DIR`) и
раздаются backend'ом по `/uploads/<hash>.<ext>`. Внешние хранилища (S3/Blob)
не используются — данные не покидают сервер.

Лимиты (переменные `.env`, по умолчанию): `MAX_IMAGE_UPLOAD_BYTES=2097152`
(2 МБ), `MAX_VIDEO_UPLOAD_BYTES=52428800` (50 МБ). Тип файла определяется по
сигнатуре (magic bytes), а не по расширению/Content-Type; разрешены
png/jpg/gif/webp/mp4/webm.

**Лимит тела на реверс-прокси должен быть >= MAX_VIDEO_UPLOAD_BYTES**, иначе
браузер получит 413 до того, как запрос дойдёт до backend. В репозиторном
`Caddyfile` уже стоит `request_body { max_size 60MB }`; подняли
`MAX_VIDEO_UPLOAD_BYTES` — поднимите и его. Для стороннего nginx —
`client_max_body_size 60m;` и `proxy_read_timeout 300s;` в `location /api/`.

Проверка: `docker compose exec backend ls -la /data/uploads`. Файлы, у которых
создание креатива не состоялось, удаляются фронтендом сразу (`DELETE
/creatives/upload/<name>`); ручная зачистка «висящих» файлов старше суток:

```bash
docker compose exec backend find /data/uploads -type f -mtime +1 -print
# перед удалением сверить с creatives.asset_url в БД
```

## 6. Первичная настройка через UI

1. Откройте `https://ваш-домен.ру` — вход супер-админа
   (создаётся сидом; сразу смените пароль).
2. **Настройки -> ОРД Озон**: вставьте API-ключ из кабинета ОРД Озон
   (Настройки -> API). Выберите «Песочница» для проверки, потом «Прод».
   Нажмите «Проверить связь».
3. **Финансы**: пополните баланс рекламодателя (кнопка «Пополнить» —
   ручная проводка супер-админа, п. биллинга ниже).
4. **SSP-партнёры**: создайте партнёра, скопируйте endpoint key, отдайте
   SSP URL эндпоинта.
5. Создайте рекламодателя -> кампанию -> креатив (загрузкой файла) ->
   line item. Креатив уйдёт на регистрацию в ОРД, erid появится в карточке.

## 7. Как работает биллинг (для супер-админа)

- **Деньги рекламодателей**: собираете по договору на счёт юрлица, затем
  вручную пополняете баланс кабинета («Финансы» -> «Пополнить»). Система
  списывает с баланса за каждый выигранный показ (по цене клиринга + маржа).
- **Долг перед SSP**: копится автоматически (отчёт «Финансы -> SSP»).
  Оплачиваете по договору с SSP (обычно постоплата раз в месяц по их отчёту).
- **Ваша маржа** = списано с рекламодателей - выплачено SSP.
- Расхождения (дискрепанс) с отчётом SSP — нормальны до 5–10%; сверяйте
  ежемесячно.

## 8. Обновление версии

```bash
cd central-dsp
git pull
docker compose up -d --build
docker compose logs backend --tail 10   # миграции применились?
```

## 9. Бэкапы (обязательно с первого дня)

Целевые показатели: **RPO ≤ 24 ч** (ежедневный полный дамп), **RTO ≤ 1 ч**
(восстановление скриптом в новую базу и переключение `DATABASE_URL`).
Redis не бэкапится: счётчики бюджетов/капов пересчитываются из Postgres при старте.

Скрипты в `ops/`:

| Скрипт | Что делает |
|---|---|
| `ops/backup.sh` | `pg_dump -Fc` + `uploads/` → `/var/backups/centraldsp/<ts>/`, `manifest.json` с sha256, ротация (`KEEP_DAYS`, по умолчанию 14), опционально `pg_restore --list` как проверка целостности и `rclone` в S3 (`BACKUP_REMOTE`) |
| `ops/restore.sh` | восстанавливает дамп в **новую** базу `centraldsp_restore_<ts>`, печатает контрольные счётчики, распаковывает `uploads` рядом; в существующую базу — только `--target-db NAME --yes` |

```bash
# /etc/cron.d/centraldsp-backup  (03:30 UTC, до месячной выгрузки ОРД)
30 3 * * *  root  cd /opt/central-dsp && ops/backup.sh >> /var/log/centraldsp-backup.log 2>&1
```

Переменные (`.env` или окружение cron): `PG_CONTAINER`, `PG_USER`, `PG_DB`,
`UPLOADS_DIR`, `BACKUP_ROOT`, `KEEP_DAYS`, `BACKUP_REMOTE` (например `s3:centraldsp-backups`).
Хотя бы одна копия должна лежать **вне** VPS — иначе бэкап не защищает от потери сервера.

Учения по восстановлению — раз в месяц (запишите дату в журнал):

```bash
ops/restore.sh /var/backups/centraldsp/<последний>   # в новую БД, боевую не трогает
docker compose exec -T postgres psql -U centraldsp -d centraldsp_restore_<ts> -c "select count(*) from campaigns"
docker compose exec -T postgres dropdb -U centraldsp centraldsp_restore_<ts>
```

Реальное восстановление после потери базы: `ops/restore.sh <бэкап>` → остановить
`backend`/`bidder-go` → сменить `DATABASE_URL` на новую базу (или `ALTER DATABASE ... RENAME`)
→ поднять сервисы → `redis-cli FLUSHDB` (счётчики пересчитаются) → проверить
`GET /health` и фид биддера: `GET /rtb-feed/partners` и `GET /rtb-feed/campaigns`
(заголовок `Authorization: Bearer ${RTB_API_TOKEN}`).

## 9.1. Планировщик `/cron/*` — сервис `scheduler`

Фоновые задачи (`/cron/*`) запускает сервис `scheduler` из `compose.yml`
(audit O-01, C1b): тот же образ, что backend, команда `python scheduler.py`.
Celery/beat, системный crontab и `vercel.json` для production **не нужны**
(`vercel.json` относится только к Vercel-preview и с production не синхронизируется).

Как работает:

- расписание — таблица `scheduled_jobs` (cron-выражение из 5 полей, **UTC**;
  МСК = UTC+3). При первом старте планировщик сеет 13 задач из
  `backend/scheduler.py::DEFAULT_JOBS` (`ON CONFLICT DO NOTHING`), правки
  админа переживают релизы;
- тик каждые `SCHEDULER_TICK` секунд (15): захват просроченных задач
  `FOR UPDATE SKIP LOCKED` + lease — несколько реплик планировщика не дублируют
  запуск. Пока задача выполняется, воркер продлевает lease каждые 20 с
  (heartbeat); lease без продления 90 с считается брошенным (упавший экземпляр)
  и задачу забирает другая реплика. Захватывается не больше свободных слотов
  (concurrency − выполняющиеся), результат записывается только владельцем lease
  (`scheduler_lease_lost_total` должен быть 0 — иначе задача выполнилась дважды);
- пропущенные запуски (простой планировщика, долгий backend) **схлопываются в
  один** — следующий `next_run_at` считается от момента завершения, «догона» нет;
- каждая задача = `GET http://backend:8000/cron/<path>` с
  `Authorization: Bearer $CRON_SECRET` (`BACKEND_INTERNAL_URL`); `timeout_seconds`
  — общий бюджет запроса (по умолчанию 600, у поминутных 50), connect ≤ 10 с.
  Сами задачи и их метрики
  `cron_job_runs_total` / `cron_job_last_success_timestamp_seconds` не изменились;
- параллельно до 4 задач.

Управление (роли admin/superadmin; матрица — `docs/audit/rbac_matrix.md`):

```bash
GET   /api/scheduler/jobs                # список: schedule, enabled, next_run_at, last_status/error/duration
PATCH /api/scheduler/jobs/{name}         # {"enabled": bool, "schedule": "m h dom mon dow", "timeout_seconds": 5..21600}
POST  /api/scheduler/jobs/{name}/run     # запустить на ближайшем тике (409 — выключена или уже выполняется)
```

Расписание по умолчанию (UTC):

| name | path | schedule | комментарий |
|---|---|---|---|
| `sweep_burl` | `/cron/sweep-burl` | `* * * * *` | биллинг по burl (R-07), таймаут 50 с |
| `reconcile_reserves` | `/cron/reconcile-reserves` | `*/5 * * * *` | сверка Redis-резервов с БД |
| `audience_sync` | `/cron/audience-sync` | `15 * * * *` | ретаргетинг-аудитории (R-06) |
| `shading` | `/cron/shading` | `0 * * * *` | bid shading |
| `ord_notifications` | `/cron/ord-notifications` | `45 * * * *` | ошибки ЕРИР |
| `retention` | `/cron/retention` | `30 2 * * *` | партиции `bid_events` |
| `rules` | `/cron/rules` | `0 6 * * *` | правила автоматизации |
| `optimize` | `/cron/optimize` | `0 7 * * *` | оптимизатор |
| `reset_daily` | `/cron/reset-daily` | `30 21 * * *` | housekeeping счётчиков прошлых дней (граница дня — МСК-дата в ключах, не этот cron) |
| `ord_export` | `/cron/ord-export` | `0 3 2 * *` | ОРД-статистика за прошлый месяц (06:00 МСК 2-го числа) |
| `fx_refresh` / `ml_train` / `domain_quality` | `/cron/fx-refresh` `10 0`, `/cron/ml-train` `0 4`, `/cron/domain-quality` `0 5` | — | **выключены** по умолчанию (включать PATCH-ом, когда нужны: курс ЦБ, ML-модель, domain quality) |

Наблюдаемость: `scheduler:8001/metrics` (`scheduler_last_tick_timestamp_seconds`,
`scheduler_jobs_overdue`, `scheduler_jobs_inflight`, `scheduler_job_failures_total`,
`scheduler_lease_lost_total`), healthcheck
`python scheduler.py --healthcheck`; алерты `SchedulerDown` /
`SchedulerJobsOverdue` / `SchedulerDispatchFailing`
(`docs/runbooks/rtb_alerts.md#scheduler`). Логи: `docker compose logs -f scheduler`.

Ручной запуск, если нужно немедленно (после `failed` в «Маркировка (ОРД) → Выгрузки»
и т.п.) — `POST /api/scheduler/jobs/ord_export/run` либо напрямую:

```bash
docker compose exec backend python -c "import urllib.request as u,os;print(u.urlopen(u.Request('http://127.0.0.1:8000/cron/ord-export?period=2026-08',headers={'Authorization':'Bearer '+os.environ['CRON_SECRET']})).read().decode())"
```

## 9.2. Партиционирование `bid_events` (миграция 033)

`bid_events` — сырой RTB-лог, при 35k QPS растёт на сотни миллионов строк в месяц.
Миграция 033 переводит его на RANGE-партиции по дням; ретенция = `DROP TABLE` партиции
(мгновенно, без bloat). Агрегаты `stats_hourly` / `domain_stats` не затрагиваются.

**Таблица маленькая (< ~50M строк, свежая установка):** просто `python migrate.py` —
перенос выполняется внутри миграции одной транзакцией.

**Таблица большая (прод с историей):** миграция в одну транзакцию заблокирует запись
на время копирования. Делайте вручную в окне низкого трафика:

```bash
# 1. Остановить приём событий (биддер продолжит торговать, события уйдут в Redis-очередь / потеряются
#    только записи лога, не деньги). Проще всего — на время remap'а поставить бэкенд в maintenance.
docker compose stop backend

# 2. Выполнить SQL из migrations/033_bid_events_partitioned.sql через psql —
#    он идемпотентен, но перенос данных (шаг 4 в DO-блоке) лучше сделать батчами:
#    закомментируйте INSERT ... SELECT и после swap'а перелейте bid_events_legacy -> bid_events
#    порциями по дню:  INSERT INTO bid_events (...) OVERRIDING SYSTEM VALUE SELECT ... FROM bid_events_legacy
#    WHERE created_at >= '2026-08-01' AND created_at < '2026-08-02';
psql "$DATABASE_URL" -f backend/migrations/033_bid_events_partitioned.sql

# 3. Пометить миграцию применённой без повторного выполнения
cd backend && python migrate.py --stamp 033_bid_events_partitioned.sql

docker compose start backend
```

После проверки отчётов удалить старую таблицу: `DROP TABLE bid_events_legacy;`.
Контроль: `/cron/retention` возвращает `rows_in_default_partition` — если > 0, партиции
создавались с опозданием (cron не отработал); строки в DEFAULT не теряются, но не удаляются
ретенцией — перелейте их в дневные партиции тем же `INSERT ... SELECT`.

## 10. Что мониторить (минимум)

| Проверка | Как | Порог тревоги |
|---|---|---|
| Backend health | `GET /health` раз в минуту | нет ответа 2 мин |
| Биддер health | `GET /healthz` | `feed_age > 120s` или redis down |
| Диск | `df -h` | > 80% |
| p99 время ответа биддера | лог биддера | > 80 мс |
| Баланс рекламодателей | UI «Финансы» | близок к 0 — кампании встанут |

Prometheus с правилами `ops/prometheus/alerts.yml` уже в стеке, но поднимается
только явно — `COMPOSE_PROFILES=monitoring` в `.env` (или `--profile monitoring`).
Слушает `127.0.0.1:9091`; наружу — только SSH-туннель (`ssh -L 9091:127.0.0.1:9091`)
или VPN, **никогда** не публиковать в интернет без auth. Grafana и Alertmanager —
тем же профилем на следующем этапе (C4); runbook алертов — `docs/runbooks/rtb_alerts.md`.

## 11. Типичные проблемы

| Симптом | Причина | Решение |
|---|---|---|
| Биддер NOBID на всё | пустой фид (нет активных кампаний/креативов с erid) | проверьте `GET /healthz` биддера и статусы кампаний |
| 401 на фиде | `RTB_API_TOKEN` не совпадает у backend и биддера | одинаковое значение в `.env`, перезапуск |
| Кампания не стартует | нет одобренного креатива или баланс 0 | одобрить креатив, пополнить баланс |
| ОРД-ошибки при создании креатива | неверный API-ключ или прод вместо песочницы | «Настройки -> ОРД Озон -> Проверить связь» |
| 429 на API | сработал rate limit (300 req/min на пользователя) | это защита; для интеграций поднять лимит в `main.py` |
