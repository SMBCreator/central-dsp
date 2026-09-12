# Релиз на VPS: порядок наката, проверки, откат, учения

Дата: 2026-09-05, обновлён 2026-09-06 (аудит O-01…O-05: убран `--apply`,
строгие exit-коды, честный вердикт нагрузки). Ветка `feat/avito-ui-kktu`. Дополняет
`docs/audit/preprod_checklist_2026-09.md` (что проверять) и `rtb_alerts.md`
(что делать по алерту). Здесь — **в каком порядке** и **какими командами**.

Инструменты, на которые опирается runbook (все в репозитории):

| Инструмент | Что делает | Мутирует |
|---|---|---|
| `ops/preprod_check.sh` | все серверные проверки чеклиста; по каждой PASS / FAIL / NOT TESTED / SKIP | нет, никогда (флага `--apply` нет — накат только руками по §2); `--load` — нагрузка |
| `backend/migrate.py --dry-run` | список файлов к накату, sha256-контроль применённых | нет (на пустой БД не создаёт даже `schema_migrations`) |
| `ops/backup.sh` / `ops/restore.sh` | pg_dump custom + Redis RDB + uploads; restore-check | backup — нет; restore — да |
| `backend/scripts/convergence_check.py` | `bid_events` ↔ `stats_hourly` (↔ `/stats/summary` платформенной сессией) по часам | нет |
| `backend/loadtest/rtb_load.py` | нагрузка на `/rtb/bid/<key>`, `--workers N` процессов; вердикт PASS / FAIL / NOT TESTED / CONFIG | создаёт события у партнёра (`test=1` по умолчанию) |
| `ops/prometheus/alerts.yml` | 18 правил; каждое ссылается на раздел `rtb_alerts.md` | — |

Exit-коды у `preprod_check.sh`, `rtb_load.py`, `convergence_check.py` одни и те же:

| exit | смысл | что делать |
|---|---|---|
| 0 | все обязательные проверки прошли | можно продолжать |
| 1 | проверка выполнена и есть явный FAIL (SLA, 5xx, 429, расхождение, невалидный ответ) | накат/релиз не продолжать |
| 2 | ошибка использования: аргументы, `.env`, окружение, недостижимый хост | исправить вызов; результата проверки нет |
| 3 | NOT TESTED: обязательный gate не доказан (зависимость упала, генератор насыщен, проверка пропущена) | **не** считать PASS; устранить причину и повторить |

## 1. Перед накатом (читает, ничего не меняет)

```bash
cd /opt/central-dsp            # каталог с docker-compose.yml и .env
git fetch && git log --oneline HEAD..origin/<branch> | cat   # что приедет
ops/preprod_check.sh           # ожидаем exit 0: 0 FAIL, 0 NOT TESTED, SKIP только у rtb_load (без --load)
ops/preprod_check.sh --json /tmp/preprod-$(date +%F).json   # то же + машинный отчёт для истории
```

Скрипт **ничего не меняет**: только `docker compose ps/exec/run`, `psql`
SELECT-ы, `migrate.py --dry-run`, GET/POST-пробы. Если `backend` не запущен,
пробы биддера идут из одноразового контейнера (`compose run --rm --no-deps`),
чтобы падение бэкенда не превращалось в ложный FAIL биддера.

Если `preprod_check.sh` показывает FAIL в разделах 1 (секреты), 2 (сервисы)
или 4 (партиции/cron) — **накат не начинать**: это состояние прода до релиза,
и его чинят отдельно. exit 3 (NOT TESTED) — тоже стоп: например, при
`backend` down не проверены фид, паритет и сходимость; сначала поднять, потом
перепроверить.

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

# 5) фид дошёл до биддера (иначе алерт BidderFeedStale через 3 мин).
#    Биддер — distroless без curl, в prod host-портов нет (compose.prod.yml:
#    ports: !reset []), поэтому пробы — из контейнера backend по сети compose:
set -a; . ./.env; set +a
docker compose exec -T backend python - <<'EOF'
import httpx, os
h = {"X-CentralDSP-Internal-Check": os.environ["RTB_API_TOKEN"]}
r = httpx.post("http://bidder-go:8090/internal/feed/reload", headers=h, timeout=10); print(r.status_code, r.text[:300])
r = httpx.get("http://bidder-go:8090/healthz", timeout=10); print(r.status_code, r.text[:300])   # campaigns > 0, feed_age_sec мал
EOF

# 6) партиция bid_events на завтра и ретеншен — то, что раньше делал --apply
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $CRON_SECRET" "$PUBLIC_BASE_URL/cron/retention"   # 200
```

Шаги 1–6 выполняются руками и по одному: у скрипта проверок нет режима
«применить», потому что флаг, который бэкапит, мигрирует и дёргает cron,
скрывал production change за именем проверки. Каждый шаг виден в shell
history и в логах, между шагами можно остановиться.

Миграция `033_bid_events_partitioned.sql` — единственная тяжёлая: она
переливает `bid_events` в партиции. На таблице > 50 млн строк запускать в
окно низкого трафика; биддер пишет в Redis Stream, поэтому события за время
миграции не теряются — консьюмер догонит.

## 3. После наката (5 минут)

```bash
ops/preprod_check.sh                      # снова exit 0 (0 FAIL, 0 NOT TESTED)
docker compose exec backend python scripts/convergence_check.py --hours 1   # уже входит в preprod_check; отдельно — если нужен вывод по часам
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
p99 покажет сеть, а не нас. `--load` гоняет `rtb_load.py` из одноразового
контейнера `backend` (`compose run --rm --no-deps`) по адресу
`http://bidder-go:8090/rtb/bid/<key>`. Нужен SSP-партнёр со статусом
`testing`/`active`; его открытый токен выдаётся один раз при создании/ротации
(в БД только hash). Без `LOAD_PARTNER_TOKEN` скрипт идёт через
`X-CentralDSP-Internal-Check: $RTB_API_TOKEN` + `Skip-Token` — это проверяет
путь биддера, но не auth партнёра.

```bash
export LOAD_ENDPOINT_KEY=<endpoint_key>  LOAD_PARTNER_TOKEN=<token>
# базовая: 6.9 «Базовая нагрузка на Go». workers — отдельные процессы генератора,
# один asyncio-процесс даёт ~2–5k QPS; под 35k нужно 8–16 workers и свободные ядра.
LOAD_QPS=35000 LOAD_SECONDS=120 LOAD_WORKERS=12 LOAD_P99_MS=30 ops/preprod_check.sh --load
# tmax: 6.4 — бюджет = tmax партнёра минус 20 мс
LOAD_QPS=500 LOAD_SECONDS=60 LOAD_P99_MS=$((TMAX-20)) ops/preprod_check.sh --load
# прямой вызов (тот же контейнер, полный JSON-отчёт в stdout, сводка в stderr):
docker compose run --rm --no-deps -T backend python loadtest/rtb_load.py \
  --url http://bidder-go:8090/rtb/bid/$LOAD_ENDPOINT_KEY --token "$LOAD_PARTNER_TOKEN" \
  --qps 35000 --seconds 120 --workers 12 --p99-budget-ms 30 > /tmp/load-$(date +%F).json
```

Как читать вердикт (`RESULT:` в stderr, `result`/`exit_code`/`reason` в JSON):

| result | exit | значит |
|---|---|---|
| PASS | 0 | achieved QPS ≥ 95 % от target (`--min-achieved-ratio`, default 0.95), 429 = 0, error rate ≤ 0.1 %, p99 в бюджете, все 200 — валидные BidResponse |
| FAIL | 1 | нагрузка выдана, но биддер не прошёл хотя бы один gate: 429 (отдельный gate, **не** часть error budget), 5xx/timeout сверх 0.1 %, p99 > бюджета, невалидные 200 |
| NOT TESTED | 3 | **генератор** не выдал target: `send_lag_p95` и `cpu_util` показывают насыщение. Это не результат биддера — добавить `--workers`, ядра или второй хост, повторить |
| CONFIG | 2 | все ответы 401/403/404 или хост недостижим — неверный URL/токен/ключ |

В отчёте всегда есть `target_qps`, `achieved_qps`, `accepted_qps`
(2xx без 429), разбивка `responses` (200/204/429/4xx/5xx/timeout/invalid_200),
`latency_ms` p50/p95/p99, `generator` (cpu_util, max_rss, send_lag, per-worker).
Все 204 при `test=1` — ожидаемо, если у партнёра нет тестовых кампаний; в
отчёте это warning, для проверки полного пути нужен `--min-bid-rate` и
кампании под этот трафик.

Если тест на **лимитер** (партнёрский QPS-гейт), это отдельный кейс, а не
тест ёмкости: `--expect-rate-limit <qps_limit>` — тогда 429 обязаны быть,
PASS означает «лимитер срабатывает», в warnings прямо написано, что capacity
этим не доказана.

Дополнительно во время теста не должны сработать `BidderLatencyP99High`,
`BidderGateErrorRate`, `BidderRedisDown`. После — `convergence_check.py --hours 1`:
нагрузка пишет реальные события (с `is_test`), агрегат должен сойтись.
Отчёт сохранить в `docs/audit/loadtest/<дата>-<сценарий>.md` вместе с
топологией генератора (хост, `--workers`, ядра) и `achieved_qps`.

Гонка за бюджет (6.5): кампания с `daily_budget = 100 ₽`, затем
`rtb_load.py --qps 2000 --workers 2 --seconds 20 --no-test-flag` (без `test=1`,
иначе резерв бюджета не проверяется). Критерий —
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
`ops/preprod_check.sh` и кейс в `ops/tests/preprod_check_test.sh` (подставные
`docker`/`curl`, гоняется в CI), чтобы чеклист оставался исполняемым, а его
вердикты — проверенными. Правило для новых проверок: недоказанное — `nt`
(NOT TESTED, exit 3), а не `pass`; мутирующих команд в скрипте не появляется.
