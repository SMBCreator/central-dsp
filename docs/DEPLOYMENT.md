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

# --- Уведомления (письма рекламодателям и технические алерты суперадмину) ---
SMTP_HOST=smtp.example.com               # без него письма копятся в очереди и не уходят
SMTP_PORT=587
SMTP_USER=alerts@ваш-домен.ру
SMTP_PASSWORD=<пароль приложения у провайдера>
SMTP_FROM=CentralDSP <alerts@ваш-домен.ру>
ALERT_WEBHOOK_SECRET=<openssl rand -hex 32>  # нужен для --profile monitoring (Alertmanager -> backend)
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
6. **Администрирование -> Алерты и письма**: укажите адреса для технических
   алертов (пусто = email всех суперадминов) и нажмите «Тестовое письмо» — оно
   идёт через ту же очередь и SMTP, что и настоящие; статус видно в таблице
   «Исходящие письма» на той же странице. Если карточка SMTP говорит
   «Не настроен» — вернитесь к `.env` (п. 3), письма никуда не уйдут.
   Адреса бизнес-уведомлений (бюджет, остановка, автоправила) — в карточке
   рекламодателя и в настройках кампании, поле «Email для уведомлений».

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

### 8.1. Non-root backend и существующий volume `uploads_data`

Начиная с C6 backend и scheduler работают **не от root**, а от системного
пользователя `app` (uid/gid `10001:10001`); frontend — от `node` (1000);
bidder-go — distroless `nonroot` (65532). Volume `uploads_data`, созданный
прежней root-версией образа, при этом переживает апгрейд **без ручных
действий**: entrypoint backend'а (`backend/docker-entrypoint.sh`) стартует
от root, работает только с точкой монтирования `/data/uploads`
(`UPLOAD_DIR`) и:

1. отказывает, если путь вне allowlist (`/data/<dir>`, `/app/uploads`) или
   если на месте каталога symlink (exit 78, приложение не стартует);
2. если владелец каталога уже `10001:10001` — ничего не делает;
3. если владелец другой (legacy root-owned volume) — меняет владельца каталога
   и одним проходом `find -xdev` только тех файлов поддерева, что принадлежат
   не `app`; никакого `chown -R /`, symlink не раскрываются, границы mount не
   пересекаются, системные каталоги и код образа не трогаются;
4. пишет в лог `entrypoint: uploads dir /data/uploads owned by 0:0 (legacy
   root-owned volume?) -> chown 10001:10001` и `fixed owner on N entries`;
5. `exec setpriv --reuid=10001 --regid=10001 --clear-groups --inh-caps=-all
   --bounding-set=-all --no-new-privs` — процесс Python не может вернуть root.

Проверка после апгрейда:

```bash
docker compose logs backend | grep entrypoint:
docker compose exec backend sh -c 'grep -E "^(Uid|NoNewPrivs):" /proc/1/status'   # Uid: 10001 …, NoNewPrivs: 1
docker compose exec backend stat -c '%u:%g %n' /data/uploads                        # 10001:10001
```

Для очень большого volume, который уже один раз обработан, обход поддерева
можно отключить: `UPLOADS_OWNERSHIP_SCAN=0` в `.env` (каталог всё равно
проверяется на каждом старте).

**Emergency fallback (только если entrypoint по какой-то причине не смог, и это
видно в логе):** ручной `chown` с хоста, строго по каталогу volume и без
следования по ссылкам:

```bash
docker compose stop backend scheduler
docker run --rm -v centraldsp_uploads_data:/data/uploads --entrypoint sh centraldsp/backend:local \
  -c 'chown -h 10001:10001 /data/uploads && find /data/uploads -xdev -mindepth 1 -exec chown -h 10001:10001 {} +'
docker compose up -d backend scheduler
```

Это не штатный путь миграции: штатный — сам деплой. CI проверяет сценарий
«root-owned volume -> апгрейд -> upload/чтение/удаление» на реальном Docker
(`ops/tests/uploads_owner_docker.sh`, `ops/tests/integration_smoke.sh`).

## 9. Бэкапы (обязательно с первого дня)

Целевые показатели: **RPO ≤ 24 ч** (ежедневный полный дамп), **RTO ≤ 1 ч**
(restore скриптом + переключение `DATABASE_URL`). Redis по умолчанию **не восстанавливается**
из бэкапа — источник истины Postgres, счётчики пересобираются (см. ниже); копия Redis
снимается только для полного DR (`--with-redis`).

Один бэкап = один файл `backup-<ts>.tar` (формат 2). Внутри: `postgres.dump` (custom format,
`pg_dump --snapshot` — дамп и контрольные снимки из **одного** снапшота), `ledger_snapshot.tsv`
(баланс каждого рекламодателя vs `SUM(transactions)`), `counts.tsv`, `schema_migrations.tsv`,
`pg_restore_toc.txt`, `uploads.tar` (volume `uploads_data`), опционально `redis/dump.rdb`,
`manifest.json` (метаданные, sha256 всех файлов, результат финансовой сверки на момент
бэкапа) и `SHA256SUMS`. `ops/restore.sh` принимает **только** этот формат.

| Скрипт | Что делает |
|---|---|
| `ops/backup.sh [--with-redis]` | артефакт в `$BACKUP_DIR` (default `/var/backups/centraldsp`), ротация `RETENTION_DAYS` (14), `VERIFY=weekly\|always\|never` — реальный `restore.sh --scratch` во временную базу + сверка, `OFFSITE_CMD` (получает путь к `.tar`), `ALERT_CMD` (получает текст ошибки). Exit 0 только если всё прошло; 4 — VERIFY не прошёл (артефакт сохранён), 5 — offsite не удался |
| `ops/restore.sh <tar>` | восстанавливает в **новую** базу `<db>_restore_<ts>`, сверяет с артефактом байт в байт, база остаётся для осмотра |
| `… --scratch` | то же во временную базу + DROP после сверки (учения) |
| `… --target-db centraldsp --yes` | боевое: DROP+CREATE базы, restore, сверка. Без `--yes` — exit 3 |
| `… --verify-only --target-db NAME` | только сверка существующей базы с артефактом |
| `… --restore-uploads --yes` | заменить содержимое volume `uploads_data` (backend должен быть остановлен) |
| `… --with-redis --yes-replace-redis` | полный DR Redis из `dump.rdb` через пересборку AOF (см. шапку скрипта) |

Порядок в `restore.sh` жёсткий: `sha256sum -c` (не сошлось → exit 5, к базе не прикасаемся)
→ `pg_restore --jobs` → «restore completed» → **verification**: из восстановленной базы снимаются
те же `ledger_snapshot`/`counts`/`schema_migrations` и сравниваются с артефактом. Любое
расхождение → «verification FAILED», exit 4: баланс **не** правится, проводки **не** дописываются,
rollback **не** делается — разбирается человек. Restore без «verification passed» успешным
не считается. Расхождение `balance ≠ SUM(transactions)`, которое было уже на момент бэкапа
(записано в manifest), сверку не валит, а печатается как «pre-existing».

```bash
# /etc/cron.d/centraldsp-backup  (03:30 UTC, до месячной выгрузки ОРД)
30 3 * * *  root  cd /opt/central-dsp && ops/backup.sh >> /var/log/centraldsp-backup.log 2>&1
```

Переменные (`/etc/default/centraldsp-backup` или окружение cron): `COMPOSE_DIR`, `BACKUP_DIR`,
`RETENTION_DAYS`, `POSTGRES_USER`/`POSTGRES_DB` (как в `.env`), `VERIFY`, `OFFSITE_CMD`, `ALERT_CMD`.
Хотя бы одна копия должна лежать **вне** VPS — `OFFSITE_CMD` намеренно провайдер-нейтрален
(`rclone copyto …`, `aws s3 cp …`); без него `backup.sh` пишет WARN в каждом запуске.
S3 Object Lock / write-only креды — отдельная задача (см. release plan, «immutable offsite»).

Учения по восстановлению — раз в месяц (дата в журнал), боевую базу не трогают:

```bash
ops/restore.sh /var/backups/centraldsp/backup-<ts>.tar --scratch     # restore -> сверка -> DROP
```

Тот же сценарий целиком гоняет CI job `backup-restore-drill` (`ops/tests/backup_restore_drill.sh`):
изолированный compose-проект → миграции → seed → `backup.sh --with-redis` → уничтожение
volume'ов → `restore.sh` → независимая сверка → подмена баланса → «verification FAILED».
Отчёт (логи, manifest, артефакт) — в artifacts прогона.

Реальное восстановление после потери базы:

```bash
docker compose stop backend scheduler bidder-go
ops/restore.sh /var/backups/centraldsp/backup-<ts>.tar --target-db centraldsp --yes --restore-uploads
#  -> «restore completed AND verification passed», иначе НЕ продолжать
docker compose start backend scheduler
docker compose exec backend python scripts/redis_rebuild_spend.py --apply   # spend:* из bid_events
curl -H "Authorization: Bearer $CRON_SECRET" http://127.0.0.1:8000/cron/reconcile-reserves   # резервы res:*
docker compose start bidder-go
```

Затем `GET /health` и фид биддера: `GET /rtb-feed/partners`, `GET /rtb-feed/campaigns`
(`Authorization: Bearer ${RTB_API_TOKEN}`). `freq:*` из Postgres не восстанавливаются
(`docs/runbooks/rtb_alerts.md`, раздел redis-down).

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

## 9.2. Партиционирование `bid_events` (миграции 033 и 057)

`bid_events` — сырой RTB-лог, при 35k QPS растёт на сотни миллионов строк в месяц.
Миграция 033 переводит его на RANGE-партиции по дням; ретенция = `DROP TABLE` партиции
(мгновенно, без bloat). Агрегаты `stats_hourly` / `domain_stats` не затрагиваются.
Миграция 057 делает создание партиций устойчивым к строкам в DEFAULT-партиции и
добавляет метрику горизонта партиций.

**Таблица маленькая (< ~50M строк, свежая установка):** просто `python migrate.py` —
перенос выполняется внутри миграции одной транзакцией.

**Таблица большая (прод с историей):** миграция в одну транзакцию заблокирует запись
на время копирования. **Не редактируйте файл миграции** — `migrate.py` сверяет sha256
применённых файлов, и изменённый 033 заблокирует все последующие деплои. Процедура
с подменой таблицы пустой копией и последующей доливкой истории при живом backend —
в [runbooks/partition_upgrade.md#big-table](runbooks/partition_upgrade.md#big-table)
(отрепетирована на копии данных, там же тайминги).

Настройки: `BID_EVENTS_RETENTION_DAYS` (90) — глубина сырого лога;
`BID_EVENTS_PARTITIONS_AHEAD_DAYS` (60) — на сколько дней вперёд `/cron/retention`
создаёт партиции. Контроль: ответ `/cron/retention` (`partition_horizon_days`,
`rows_in_default_partition`) и алерты `BidEventsPartitionHorizonShort`,
`BidEventsDefaultPartitionNonEmpty`; ручные операции —
`scripts/partition_repair.py` (см. runbook).

## 10. Что мониторить (минимум)

| Проверка | Как | Порог тревоги |
|---|---|---|
| Backend liveness | `GET /health` (его же смотрит Docker HEALTHCHECK) | нет ответа 2 мин — `TargetDown` |
| Backend readiness | `GET /ready` — PostgreSQL, Redis, consumer `rtb:events` | 503 3 мин — `BackendNotReady`; деплой не катить |
| Биддер liveness | `/bidder -healthcheck` → `GET /livez` (Docker) | контейнер unhealthy |
| Биддер readiness | `GET /healthz` — фид свежий, Redis | 503: `feed_age > FEED_MAX_AGE` или redis down |
| Диск / inode / память | `node-exporter` (profile monitoring), группа `centraldsp-node` | свободно < 15 % / < 5 % critical; inode < 10 %; MemAvailable < 10 % |
| p99 время ответа биддера | лог биддера | > 80 мс |
| Баланс рекламодателей | UI «Финансы» | близок к 0 — кампании встанут |

Prometheus с правилами `ops/prometheus/alerts.yml` и `node-exporter` (метрики
хоста; host mounts `/`, `/proc`, `/sys` — только read-only, порт не публикуется)
уже в стеке, но поднимаются только явно — `COMPOSE_PROFILES=monitoring` в `.env`
(или `--profile monitoring`). Prometheus слушает `127.0.0.1:9091`; наружу — только
SSH-туннель (`ssh -L 9091:127.0.0.1:9091`) или VPN, **никогда** не публиковать в
интернет без auth. Правила покрыты unit-тестами `ops/prometheus/alerts_test.yml`
(`promtool test rules`, CI) — при правке PromQL добавить кейс туда. Runbook
алертов — `docs/runbooks/rtb_alerts.md` (liveness/readiness — во вводной части).

Доставка алертов людям — тем же профилем `monitoring`: Alertmanager
(`ops/alertmanager/alertmanager.yml`, `127.0.0.1:9093`) шлёт каждый firing/resolved
в backend `POST /alerts/webhook` (Bearer `ALERT_WEBHOOK_SECRET`; контейнер
Alertmanager не стартует с пустым секретом). Backend создаёт in-app уведомление
каждому суперадмину и письмо через `notification_outbox` → SMTP (`SMTP_*`).
Адреса, порог важности для писем и тестовое письмо — UI **Администрирование →
Алерты и письма**; там же видно, приходит ли `Watchdog` (Alertmanager раз в 10 мин)
и очередь писем. Тишина в этом контуре — тоже алерт (`AlertPipelineDead`,
`NotifyOutboxStuck`), см. runbook `#notifications`. Grafana — следующий этап.

### 10.1. Smoke-чеклист после каждого деплоя

1. `GET https://<domain>/api/health` → 200; `docker compose ps` — все сервисы `healthy`
   (bidder-go — `/bidder -healthcheck` → `/livez`, scheduler — `python scheduler.py --healthcheck`).
   Readiness отдельно: `ops/preprod_check.sh` проверяет `backend /ready`
   (postgres/redis/events_consumer = `ok`) и `bidder /healthz` (200); в
   Prometheus нет `BackendNotReady`/`TargetDown`.
2. `GET /scheduler/jobs` (admin): все задачи `enabled`, `next_run_at` в будущем,
   `last_status` не `failed`; в Prometheus нет `SchedulerDown`.
3. Создать SSP-партнёра в UI «Supply» → получить `endpoint_key` и токен
   (показывается один раз). Адрес для SSP — `${PUBLIC_BIDDER_BASE_URL}/rtb/bid/{key}`
   (Go bidder за Caddy `/rtb/*`), не `/api/rtb/...`.
4. Тестовый BidRequest с валидным `X-CentralDSP-Token` → 200 с `adm`;
   без токена → 401; чужой токен → 403.
5. В `adm` присутствует `erid` (кампания с зарегистрированным креативом);
   кампания без erid → активация блокируется с 409 (`ORD_LAUNCH_GUARD=on`).
6. Win notice по подписанному `nurl` (`/api/rtb/win`) → списание в ledger ровно
   один раз; повторный nurl → duplicate без списания. Для партнёра с
   `billing_event=burl` списание — по `/api/rtb/burl`.
7. `imp`/`click` пиксели → счётчики в статистике растут; через несколько минут
   `events_consumer` перенёс события Go в `bid_events` (win-rate в отчёте не пустой).
8. Роль `manager` не может `POST /billing/deposit` (403); мутация видна в `audit_log`.

### 10.2. Пилот (после smoke)

1. Один SSP, QPS-лимит партнёра 10; одна кампания с маленьким дневным бюджетом,
   все креативы с erid.
2. Неделя наблюдения: списания vs статистика SSP, ledger сходится
   (`/cron/reconcile-reserves` без drift), p99 биддера со стороны SSP ≤ 80 мс.
3. Ramp-up QPS 10 → 50 → 200; масштабировать `bidder-go` репликами
   (`docker compose up -d --scale bidder-go=N`, Caddy балансирует).
4. По получении ключа ОРД: `ORD_OZON_API_KEY` в `.env`, сверить пути API со
   спекой (`docs/audit/ord_spec.md`), перерегистрировать пилотные креативы,
   проверить месячную выгрузку 2-го числа (`ord_export`).

## 11. Типичные проблемы

| Симптом | Причина | Решение |
|---|---|---|
| Биддер NOBID на всё | пустой фид (нет активных кампаний/креативов с erid) | проверьте `GET /healthz` биддера и статусы кампаний |
| 401 на фиде | `RTB_API_TOKEN` не совпадает у backend и биддера | одинаковое значение в `.env`, перезапуск |
| Кампания не стартует | нет одобренного креатива или баланс 0 | одобрить креатив, пополнить баланс |
| ОРД-ошибки при создании креатива | неверный API-ключ или прод вместо песочницы | «Настройки -> ОРД Озон -> Проверить связь» |
| 429 на API | сработал rate limit (300 req/min на пользователя) | это защита; для интеграций поднять лимит в `main.py` |
| После апгрейда upload креатива падает, в логе backend `PermissionError`/`EACCES` на `/data/uploads` | entrypoint не смог исправить владельца legacy-volume (см. `docker compose logs backend \| grep entrypoint:`) | п. 8.1: разобрать причину по логу (symlink? путь вне allowlist?), при необходимости emergency fallback |
| backend/bidder-go перезапускается с `OOMKilled` | подключён `compose.limits.yml` с лимитом ниже реального пика | п. 12: снять лимит (убрать overlay), перемерить под нагрузкой, вернуть с headroom |

## 12. Ресурсы контейнеров: reservations сейчас, limits после benchmark

В `compose.yml` для всех сервисов заданы **только soft reservations**
(`deploy.resources.reservations.memory`): при нехватке памяти ядро сначала
отбирает у тех, кто выше своей брони, но никого не убивает и не троттлит.

**Hard limits по умолчанию выключены — осознанно.** В DSP неверный лимит
опаснее его отсутствия:

| Лимит занижен у | Последствие |
|---|---|
| bidder-go (memory) | OOM-kill во время RTB burst -> потерянные impressions |
| bidder-go (cpu) | throttling -> рост p95/p99 -> auction timeout / no-bid |
| Redis | eviction/OOM -> потеря резервов и бюджетных счётчиков |
| PostgreSQL | медленные запросы, connection exhaustion, лаг scheduler, сбои биллинга/ОРД |
| backend | падение callback'ов win/burl/click |

Лимиты живут в отдельном overlay `compose.limits.yml`, все значения — через
переменные `.env` **без default** (`LIMIT_<SERVICE>_MEM/CPUS`): подключили
overlay без значений — compose падает на interpolation, а не молча ставит
чей-то «разумный» лимит. Одинаковые лимиты для dev/CI/prod не используются:
dev и CI работают без overlay.

Порядок включения лимитов в production:

1. Benchmark на реальном профиле: `backend/loadtest/rtb_load.py` до целевого
   QPS (+ ramp 10 -> 50 -> 200 из п. 10.2), параллельно снять: CPU и memory
   профиль каждого контейнера (`docker stats`, node-exporter/cAdvisor), Go
   heap/GC (`/metrics` биддера: `go_memstats_*`, `go_gc_duration_seconds`),
   p50/p95/p99 биддера, error/no-bid/timeout rate, Redis `used_memory`/
   `mem_fragmentation_ratio`, PostgreSQL connections/cache hit, поведение при
   рестарте и overlap при `up -d` (два экземпляра одновременно).
2. Определить: `reservation` = нормальная ожидаемая нагрузка; `limit` =
   измеренный peak + headroom (RTB burst, Go GC, PG shared_buffers/work_mem,
   Redis fragmentation + AOF rewrite, rolling deployment). Лимит Redis обязан
   быть выше `maxmemory` из `compose.yml`.
3. Записать `LIMIT_*` в `.env`, подключить overlay:
   `COMPOSE_FILE=compose.yml:compose.prod.yml:compose.limits.yml`,
   `docker compose up -d`.
4. **Проверить реальный cgroup, а не намерение:** `ops/check_cgroup_limits.sh`
   сверяет `memory.max`/`cpu.max` внутри контейнеров и `HostConfig` с
   отрендеренным compose. `FAIL` = Compose в этом режиме лимит не применил —
   задача не закрыта.
5. Алерты (Prometheus, п. 10): container memory/CPU usage к лимиту, CPU
   throttling, `OOMKilled` count, Go heap/GC pause, Redis
   `maxmemory`/evictions, PG connections, bidder p95/p99, backend callback
   errors, scheduler lag. Без них лимит — слепой выключатель.

## 13. Базовые образы: pin по digest и контролируемое обновление

Все внешние образы закреплены **tag с patch-версией + immutable digest**
(`postgres:15.19-alpine@sha256:…`, `redis:7.4.11-alpine@…`,
`python:3.12.14-slim-bookworm@…`, `node:22.23.2-alpine@…`,
`golang:1.25.14-alpine@…`, `distroless/static-debian12:nonroot@…`,
`caddy:2.11.4-alpine@…`, `prom/*@…`). Плавающие теги (`python:3.12-slim`,
`postgres:15-alpine`) не используются: они меняют содержимое между двумя
сборками, и CI, локальная среда и VPS собирают разные образы при неизменном
Dockerfile. Тест `backend/tests/test_c6_images_compose.py` не даст
вернуть плавающий тег.

Зависимости backend ставятся из `backend/requirements.lock` с
`--require-hashes --only-binary=:all:` (hash mismatch = ошибка сборки), frontend
— `pnpm install --frozen-lockfile` с pinned pnpm, bidder-go — `go.sum`.
PostgreSQL major = **15** в CI, compose и production; смена major PostgreSQL,
Node или Python — отдельная задача совместимости, не часть обновления digest.

**Digest pin не значит «никогда не обновлять».** Регламент security-обновления
базовых образов (отдельный PR, не чаще/реже чем нужно по CVE, минимум раз в
месяц проверка):

1. Проверить новые patch-версии и security-фиксы:
   `docker buildx imagetools inspect python:3.12-slim-bookworm` (и остальные) —
   актуальный tag и digest; сверить с release notes / CVE.
2. Обновить tag+digest в `backend/Dockerfile`, `frontend/Dockerfile`,
   `bidder-go/Dockerfile`, `compose.yml`, `compose.prod.yml`,
   `.github/workflows/ci.yml` (postgres service, caddy validate, promtool) — все
   вхождения одного образа синхронно.
3. Обновление `requirements.lock`, если нужно: команда в его заголовке; только в
   том же или отдельном PR, никогда «неявно» при сборке.
4. PR обязан пройти полный CI: backend tests, frontend typecheck/build, Go
   tests, `docker-build` всех трёх образов, `container-smoke`, `integration`,
   `compose`, `security` (pip-audit по lock, pnpm audit, govulncheck).
5. В описании PR — changelog: что обновлено, старый и новый digest, ссылка на
   CVE/release notes. Rollback = revert PR и `docker compose up -d --build`.
6. На VPS: обычный п. 8. Образы с digest не обновятся сами при `pull`/`build` —
   это и есть цель.
