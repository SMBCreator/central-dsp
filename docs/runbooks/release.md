# Релиз на VPS: порядок наката, проверки, откат, учения

Дата: 2026-09-05. Ветка `feat/avito-ui-kktu`. Дополняет
`docs/audit/preprod_checklist_2026-09.md` (что проверять) и `rtb_alerts.md`
(что делать по алерту). Здесь — **в каком порядке** и **какими командами**.

Инструменты, на которые опирается runbook (все в репозитории):

| Инструмент | Что делает | Мутирует |
|---|---|---|
| `ops/preprod_check.sh` | все серверные проверки чеклиста, PASS/FAIL по каждой | нет; `--apply` — backup+migrate+retention; `--load` — нагрузка |
| `backend/migrate.py --dry-run` | список файлов к накату, sha256-контроль применённых | нет |
| `ops/backup.sh` / `ops/restore.sh` | pg_dump custom + Redis RDB + uploads; restore-check | backup — нет; restore — да |
| `backend/scripts/convergence_check.py` | `bid_events` ↔ `stats_hourly` (↔ `/stats/summary`) по часам | нет |
| `backend/loadtest/rtb_load.py` | нагрузка на `/rtb/bid/<key>`, exit 1 при p99 > бюджета | создаёт события у партнёра |
| `ops/prometheus/alerts.yml` | 18 правил; каждое ссылается на раздел `rtb_alerts.md` | — |

## 1. Перед накатом (читает, ничего не меняет)

```bash
cd /opt/central-dsp            # каталог с docker-compose.yml и .env
git fetch && git log --oneline HEAD..origin/<branch> | cat   # что приедет
ops/preprod_check.sh           # ожидаем: 0 FAIL, SKIP только у --apply/--load
```

Если `preprod_check.sh` показывает FAIL в разделах 1 (секреты), 2 (сервисы)
или 4 (партиции/cron) — **накат не начинать**: это состояние прода до релиза,
и его чинят отдельно.

## 2. Накат

Порядок фиксированный. Каждый шаг идемпотентен; между шагами можно остановиться.

```bash
# 1) бэкап — restore-check встроен (backup.sh проверяет TOC дампа)
ops/backup.sh

# 2) код
git pull --ff-only

# 3) миграции: сначала посмотреть, потом применить
docker compose exec backend python migrate.py --dry-run
docker compose exec backend python migrate.py
docker compose exec backend python migrate.py --dry-run   # должно быть пусто

# 4) образы и перезапуск — сначала бэкенд (он валидирует .env при старте,
#    startup_checks.py), затем биддер, затем фронт
docker compose build backend bidder-go frontend
docker compose up -d backend
docker compose logs --tail=50 backend | grep -iE 'startup_checks|error|refus'   # без ERROR от startup_checks
docker compose up -d bidder-go
docker compose up -d frontend

# 5) фид дошёл до биддера (иначе алерт BidderFeedStale через 3 мин)
curl -s -X POST -H "X-CentralDSP-Internal-Check: $RTB_API_TOKEN" localhost:8080/internal/feed/reload
curl -s localhost:8080/metrics | grep -E '^bidder_feed_(age_seconds|campaigns)'
```

Или одной командой то же самое плюс проверки: `ops/preprod_check.sh --apply`.

Миграция `033_bid_events_partitioned.sql` — единственная тяжёлая: она
переливает `bid_events` в партиции. На таблице > 50 млн строк запускать в
окно низкого трафика; биддер пишет в Redis Stream, поэтому события за время
миграции не теряются — консьюмер догонит.

## 3. После наката (5 минут)

```bash
ops/preprod_check.sh                      # снова 0 FAIL
docker compose exec backend python scripts/convergence_check.py --hours 1
curl -s localhost:9090/api/v1/alerts | python3 -m json.tool | grep -E '"alertname"|"state"'
```

Смотреть в Grafana 15 минут: `bidder_bid_latency_seconds` p99, доля 204/200
(`bidder_bid_responses_total` / `bidder_no_bid_total`), `bidder_redis_up`,
`bidder_gate_errors_total`, `Backend5xxShare`. Список алертов и действий —
`rtb_alerts.md`.

## 4. Откат

Код и образы откатываются всегда; миграции — **только вперёд** (все файлы
`backend/migrations/*.sql` аддитивны: новые таблицы/колонки/индексы, старый
код с новой схемой работает).

```bash
git checkout <предыдущий тег или sha>
docker compose build backend bidder-go frontend && docker compose up -d
```

Откат схемы нужен только если миграция испортила данные — тогда
`ops/restore.sh <дамп из шага 2.1>` (полный restore, даунтайм ≈ время
pg_restore) и разбор причины до повторного наката.

## 5. Нагрузочный тест (6.9 чеклиста)

Запускать **с машины в той же сети**, что и биддер: с ноутбука через интернет
p99 покажет сеть, а не нас. Нужен SSP-партнёр со статусом `testing`/`active`
и его открытый токен (выдаётся один раз при создании/ротации, в БД хранится
только hash).

```bash
export LOAD_ENDPOINT_KEY=<endpoint_key>  LOAD_PARTNER_TOKEN=<token>
# базовая: 6.9 «Базовая нагрузка на Go»
LOAD_QPS=35000 LOAD_SECONDS=120 LOAD_P99_MS=30 ops/preprod_check.sh --load
# tmax: 6.4 — бюджет = tmax партнёра минус 20 мс
LOAD_QPS=500 LOAD_SECONDS=60 LOAD_P99_MS=$((TMAX-20)) ops/preprod_check.sh --load
```

Критерии: exit 0 (p99 в бюджете), в отчёте `statuses` только 200/204, за
время теста не сработали `BidderLatencyP99High`, `BidderGateErrorRate`,
`BidderRedisDown`. После теста — `convergence_check.py --hours 1`: нагрузка
пишет реальные события, и агрегат должен сойтись.

Гонка за бюджет (6.5): кампания с `daily_budget = 100 ₽`, затем
`rtb_load.py --qps 2000 --concurrency 200 --seconds 20`. Критерий —
`SELECT sum(clear_price)/1000 FROM bid_events WHERE campaign_id=... AND
event_type='impression' AND created_at::date = current_date` ≤ 100 + одна
ставка (резерв в Redis атомарный, `ReleaseGate` возвращает проигранное).

## 6. Учения на отказ (6.9)

Каждое учение — под нагрузкой ~5k QPS (`--load` с `LOAD_QPS=5000` в
соседнем терминале), чтобы видеть поведение, а не тишину.

**Redis недоступен 30 с**
```bash
docker compose stop redis; sleep 30; docker compose start redis
```
Ожидаем: биддер отвечает 204 (fail-open без резерва бюджета), алерт
`BidderRedisDown` загорается через 1 мин и гасится сам; после старта Redis —
200 возвращаются без рестарта; `bidder_redis_up` снова 1, `bidder_gate_errors_total`
перестаёт расти.
Пиксель (`/pixel`) в это время отдаёт 204 без 5xx (дедуп fail-open).

**Postgres недоступен 60 с**
```bash
docker compose stop postgres; sleep 60; docker compose start postgres
```
Ожидаем: биддер торгует на последнем фиде (он в памяти), `bidder_feed_age_seconds`
растёт, `BidderFeedStale` сработает только если простой > 3 мин; бэкенд отдаёт
503 на `/health` и 5xx на API — это нормально, `Backend5xxShare` загорится.
После старта: фид обновляется сам в течение интервала, `Backend5xxShare` гаснет,
консьюмер `events_consumer` дочитывает Redis Stream — проверить
`convergence_check.py --hours 1`: расхождений быть не должно.

**Что считается провалом учений:** рестарт контейнера биддера (`docker compose
ps` — RESTARTS > 0), 5xx от `/rtb/bid`, или расхождение в `convergence_check`
после восстановления.

## 7. Что менять в этом runbook

При добавлении миграции — ничего (порядок общий). При добавлении алерта —
раздел в `rtb_alerts.md` (тест `test_alert_rules.py` требует ссылку на
runbook). При добавлении серверной проверки в чеклист — шаг в
`ops/preprod_check.sh`, чтобы чеклист оставался исполняемым.
