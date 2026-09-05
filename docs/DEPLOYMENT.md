# Развёртывание CentralDSP на сервере

Пошаговый гайд: от голого VPS до работающего стека (Postgres + Redis + Backend API +
Go-биддер + Celery + Frontend) за реверс-прокси с HTTPS.

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

# Docker + Compose plugin
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

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
cp .env.example .env 2>/dev/null || touch .env
```

Заполните `.env` (все секреты генерируйте через `openssl rand -hex 32`):

```env
# --- База данных ---
POSTGRES_DB=centraldsp
POSTGRES_USER=centraldsp
POSTGRES_PASSWORD=<openssl rand -hex 16>
DATABASE_URL=postgresql://centraldsp:<тот же пароль>@postgres:5432/centraldsp

# --- Redis ---
REDIS_URL=redis://redis:6379/0

# --- Секреты (ОБЯЗАТЕЛЬНО уникальные, не из примеров!) ---
SESSION_SECRET=<openssl rand -hex 32>
RTB_API_TOKEN=<openssl rand -hex 32>
EVENT_TOKEN_SECRET=<openssl rand -hex 32>

# --- Прод-флаги ---
APP_ENV=production
PUBLIC_BASE_URL=https://ваш-домен.ру

# --- Celery monitoring ---
FLOWER_PASSWORD=<пароль для Flower UI>

# --- ОРД Озон (можно позже задать через UI настроек) ---
# ORD_OZON_API_KEY=<ключ из кабинета ОРД Озон>
```

## 4. Запуск стека

```bash
# Собрать и поднять всё. Go-биддер (bidder-go) входит в набор по умолчанию;
# legacy-биддер `bidder` поднимается только явно: --profile legacy.
docker compose up -d --build postgres redis backend bidder-go worker beat frontend

# Проверить статусы
docker compose ps
docker compose logs backend --tail 20
docker compose logs bidder-go --tail 20
```

Миграции БД применяются автоматически при старте backend (`python migrate.py`).

Проверка изнутри сервера:

```bash
curl -s http://localhost:8000/health        # backend -> {"status":"ok"}
curl -s http://localhost:8090/healthz       # go-биддер -> feed fresh, redis ok
curl -s http://localhost:3000 -o /dev/null -w "%{http_code}\n"  # frontend -> 200
```

## 5. Реверс-прокси с HTTPS (Caddy — проще всего)

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile > /dev/null << 'EOF'
ваш-домен.ру {
    # API -> FastAPI backend
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy localhost:8000
    }
    # RTB endpoint для SSP -> Go-биддер
    handle /rtb/* {
        reverse_proxy localhost:8090
    }
    # Всё остальное -> Next.js UI
    handle {
        reverse_proxy localhost:3000
    }
}
EOF
sudo systemctl reload caddy
```

Caddy сам получит и будет продлевать TLS-сертификат Let's Encrypt.
SSP-партнёру отдаёте URL вида: `https://ваш-домен.ру/rtb/bid/<endpoint_key>`.

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
браузер получит 413 до того, как запрос дойдёт до backend:

```caddyfile
    handle /api/* {
        uri strip_prefix /api
        request_body {
            max_size 60MB
        }
        reverse_proxy localhost:8000 {
            transport http {
                read_timeout 5m
            }
        }
    }
```

Для nginx — `client_max_body_size 60m;` и `proxy_read_timeout 300s;` в
`location /api/`.

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

## 9.1. Планировщик `/cron/*` на VPS

На Vercel расписание берётся из `vercel.json` (`crons`). В docker-compose стеке
планировщика **нет** — эндпоинты `/cron/*` нужно дёргать системным crontab на хосте.
Все вызовы требуют `Authorization: Bearer $CRON_SECRET` (см. `.env`).

```bash
# /etc/cron.d/centraldsp  (время — UTC; МСК = UTC+3)
CRON_SECRET=<из .env>
API=https://api.example.com

0 6 * * *   root curl -fsS -H "Authorization: Bearer $CRON_SECRET" $API/cron/rules        >/dev/null
0 21 * * *  root curl -fsS -H "Authorization: Bearer $CRON_SECRET" $API/cron/reset-daily  >/dev/null
0 7 * * *   root curl -fsS -H "Authorization: Bearer $CRON_SECRET" $API/cron/optimize     >/dev/null
0 * * * *   root curl -fsS -H "Authorization: Bearer $CRON_SECRET" $API/cron/shading      >/dev/null
# ОРД: месячная статистика за предыдущий месяц — 2-е число, 06:00 МСК.
# Идемпотентно: повтор за уже выгруженный период — no-op (ord_export_log).
0 3 2 * *   root curl -fsS -H "Authorization: Bearer $CRON_SECRET" $API/cron/ord-export   >/dev/null
# Ретенция bid_events: партиции на неделю вперёд + DROP старше BID_EVENTS_RETENTION_DAYS (90).
30 2 * * *  root curl -fsS -H "Authorization: Bearer $CRON_SECRET" $API/cron/retention    >/dev/null
```

Проверка вручную (например, после `failed` в разделе «Маркировка (ОРД) → Выгрузки»):

```bash
curl -fsS -H "Authorization: Bearer $CRON_SECRET" "$API/cron/ord-export?period=2026-08"
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

Полноценный контур (Prometheus + Grafana + алерты) — следующий этап,
см. `docs/PROD_RUNBOOK.md`.

## 11. Типичные проблемы

| Симптом | Причина | Решение |
|---|---|---|
| Биддер NOBID на всё | пустой фид (нет активных кампаний/креативов с erid) | проверьте `GET /healthz` биддера и статусы кампаний |
| 401 на фиде | `RTB_API_TOKEN` не совпадает у backend и биддера | одинаковое значение в `.env`, перезапуск |
| Кампания не стартует | нет одобренного креатива или баланс 0 | одобрить креатив, пополнить баланс |
| ОРД-ошибки при создании креатива | неверный API-ключ или прод вместо песочницы | «Настройки -> ОРД Озон -> Проверить связь» |
| 429 на API | сработал rate limit (300 req/min на пользователя) | это защита; для интеграций поднять лимит в `main.py` |
