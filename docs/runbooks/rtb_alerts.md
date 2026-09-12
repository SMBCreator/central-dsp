# Runbook: RTB-алерты CentralDSP

Часть D (GAP-011). Метрики: Go-биддер `:8090/metrics` (серии `bidder_*`),
Python backend `/metrics` (серии `rtb_*`), хост `node-exporter:9100` (серии
`node_*`, profile monitoring). Правила: `ops/prometheus/alerts.yml`; их
unit-тесты — `ops/prometheus/alerts_test.yml` (`promtool test rules`, CI).

**Liveness vs readiness (audit O-05).** Docker HEALTHCHECK смотрит только
liveness: backend `GET /health`, bidder `/bidder -healthcheck` → `GET /livez`,
scheduler `--healthcheck`. Он отвечает на вопрос «процесс жив?», и отказ
Postgres/Redis его не роняет — иначе `restart: unless-stopped` бесконечно
перезапускал бы здоровый процесс. Readiness («можно ли на него слать трафик /
выкатывать?») — отдельные ручки: backend `GET /ready` (PostgreSQL, Redis,
consumer `rtb:events`; 503 + `checks` с кодом по каждой), bidder `GET /healthz`
(фид свежий + Redis; 503 + `reasons`). Их читают `ops/preprod_check.sh`
(gate релиза) и Prometheus (`backend_ready`, `backend_postgres_up`,
`backend_redis_up`, `rtb_events_consumer_up`, `bidder_ready`, `bidder_redis_up`).

Общий принцип: сначала остановить финансовый ущерб (пауза кампаний/кампании),
потом разбираться. Пауза кампании безопасна и обратима; недоставка догоняется
пейсингом.

---

## feed-stale — BidderFeedAging / BidderFeedStale

**Что значит (audit R-02):** биддер не получает свежий снапшот кампаний.
Поведение ограничено `FEED_MAX_AGE` (по умолчанию 120 с = 4 интервала
рефреша):
- **BidderFeedAging** (warning) — снапшот старше 60 с, но ещё в пределах
  `FEED_MAX_AGE`: биддер торгует по последней известной конфигурации.
- **BidderFeedStale** (critical) — снапшот старше `FEED_MAX_AGE`: биддер
  отвечает `503 feed_stale` на все bid-запросы (`bidder_nobid_total{reason="feed_stale"}`),
  `/healthz` отдаёт 503 — Docker/балансировщик выводит инстанс из ротации.
  Спенда по устаревшим бюджетам/статусам **нет**, доставка остановлена.
  До первой успешной загрузки после старта — `feed_not_loaded`, тоже 503.

`POST /internal/feed/reload` возвращает реальный результат (`ok:false` + `error`,
HTTP 502), если backend не отдал фид — backend (ssp_check) по этому понимает,
что кампания не попала в снапшот.

**Диагностика:**
1. `curl -s localhost:8090/metrics | grep bidder_feed` — возраст и размер фида.
2. Логи биддера: ошибки `feed refresh` (сеть, 5xx от backend, парсинг).
3. `curl -s $BACKEND/rtb-feed/bidder` от имени биддера — отвечает ли backend.
4. Проверить БД/пул соединений backend (фид собирается запросом к Postgres).

**Действия:**
- Если backend лежит — поднять его; фид восстановится в течение refresh-интервала.
- Если фид не собирается из-за данных (ошибка в кампании) — найти по логам
  backend и исправить/деактивировать сущность.
- Останавливать биддер руками не нужно: по истечении `FEED_MAX_AGE` он сам
  уходит в no-bid. Если для площадки допустим более долгий stale-режим
  (например, редко меняющиеся кампании) — поднять `FEED_MAX_AGE` (`300s`)
  в окружении биддера; ниже 60 с ставить не стоит (два пропущенных рефреша
  подряд остановят торговлю).

## redis-down — BidderRedisDown

**Что значит:** биддер не может достучаться до Redis. Budget/frequency-гейт
недоступен, биддер уходит в консервативный no-bid — доставка остановлена.

**Диагностика:**
1. `redis-cli -u $REDIS_URL ping` с хоста биддера.
2. Логи Redis: OOM, рестарт, maxclients.
3. Сетевая связность / DNS между биддером и Redis.

**Действия:**
- Поднять/восстановить Redis. Биддер восстановится сам, рестарт не нужен.
- После восстановления проверить дневные счётчики расхода: ключи
  `spend:camp:{id}:{YYYY-MM-DD}` / `spend:li:{id}:{YYYY-MM-DD}` (дата — бизнес-день
  Europe/Moscow, TTL до следующей полуночи МСК + 1 ч). Если Redis
  потерял состояние (flush/OOM-эвикция) — счётчики начинают с нуля, а не
  пересобираются из БД: до полуночи возможен перерасход дневного бюджета в
  размере уже потраченного до инцидента. Сверить спенд за окно инцидента с
  `bid_events`; штатное восстановление — из журнала списаний:
  `docker compose exec backend python scripts/redis_rebuild_spend.py` (dry-run),
  затем `--apply` (ключ только повышается до значения ledger; `--force` — перезапись).
  Compose-Redis: `noeviction` + AOF + volume `redis_data`; в production backend и
  bidder-go не стартуют с eviction-политикой.
- Ретаргетинг-аудитории (`audz:{segment}` ZSET, R-06): после потери Redis
  include-кампании молчат, пока ключи пусты. `/cron/audience-sync` (раз в час)
  досеет пустые ключи из `audience_members` сам; немедленно —
  `docker compose exec backend python scripts/redis_rebuild_audiences.py` (dry-run),
  затем `--apply` (только пустые ключи) или `--apply --force` (полная перезапись).
- Frequency cap после потери Redis: `freq:*` и `freqinf:*` начинают с нуля —
  до конца самого длинного окна возможен перепоказ в размере уже показанного.
  Из БД не восстанавливается (нет per-device журнала показов за окно); принять
  как известное ограничение инцидента.

## target-down — TargetDown / NodeExporterDown

**Что значит:** Prometheus не смог снять `/metrics` с цели (`up == 0`) —
процесс мёртв, контейнер в цикле рестартов, порт не слушает или нет сети.
Это **единственный** алерт, который сработает при полной смерти сервиса: все
остальные правила по его метрикам в это время молчат (серий нет; `absent()`
стоит только у cron/scheduler). Поэтому `TargetDown` critical с `for: 2m`.

**Диагностика:**
1. `docker compose ps` — `State`/`Health` сервиса из `{{ $labels.job }}`;
   `docker compose logs --tail=200 <svc>` — паника / OOM (`exit 137`) / ошибка
   конфигурации на старте (Go: `EventBaseURLProblem` в production).
2. Цель жива, но `up == 0` → Prometheus не в той сети или имя сервиса в
   `ops/prometheus/prometheus.yml` не совпадает с compose (`test_monitoring_stack.py`
   ловит это в CI). `docker compose exec prometheus wget -qO- http://backend:8000/metrics`.
3. `NodeExporterDown` (warning): profile monitoring поднят, а node-exporter
   не скрейпится — алерты по диску/памяти хоста слепы. Обычно упал из-за
   недоступного host mount (`/`, `/proc`, `/sys` — все `ro`) или SELinux.

**Что делать:** поднять сервис (`docker compose up -d <svc>`); если падает на
старте — читать лог до первого `ERROR`, не гонять рестарты. Пока backend
down — биддер торгует на последнем снимке фида (см. `#postgres-down`),
события копятся в `rtb:events`; пока bidder down — SSP получают timeout от
Caddy, спенда нет.

---

## backend-ready — BackendNotReady / BackendPostgresDown / BackendRedisDown / RtbEventsConsumerDown

**Что значит:** процесс backend жив (`/health` 200, Docker healthy), но
`GET /ready` отдаёт 503 — одна из зависимостей не проходит проверку.
`backend_ready == 0` — свёртка; причина видна в `checks` ответа и в
отдельных gauge:

| check | gauge | коды в `/ready` | что сломано |
|---|---|---|---|
| `postgres` | `backend_postgres_up` | `error`, `timeout` (`READY_PG_TIMEOUT_S`, 2 с) | `SELECT 1` не проходит → `#postgres-down` |
| `redis` | `backend_redis_up` | `error`, `timeout` (`READY_REDIS_TIMEOUT_S`, 1 с), `not_configured` | PING не проходит → `#redis-down`; биллинг win, кэш фида, rate limit |
| `events_consumer` | `rtb_events_consumer_up` | `not_running`, `starting`, `stale` (нет успешной итерации > `READY_CONSUMER_STALE_S`, 60 с), `lagging` (lag > `READY_MAX_EVENTS_LAG`, 150k), `disabled` (= ok: `RTB_EVENTS_CONSUMER=0`) | `#events-consumer` |

Ответ кэшируется `READY_REFRESH_EVERY_S` (15 с) — `/ready` под нагрузкой
не долбит PG/Redis. Gauges обновляются тем же снимком при каждом скрейпе,
поэтому `backend_ready` и `checks` не могут разойтись.

**Диагностика:** `curl -s backend:8000/ready | jq .checks` (из контейнера
или `ops/preprod_check.sh` — там это отдельная проверка `backend /ready`).
Дальше — по runbook соответствующей зависимости.

**Deploy gate:** `preprod_check.sh` даёт FAIL на `/ready != 200`; релиз при
`BackendNotReady` не катить (`docs/runbooks/release.md`). Docker при этом
показывает `healthy` — это ожидаемо, не «глюк healthcheck».

---

## no-traffic — BidderNoTraffic

**Что значит:** ноль bid-запросов 10+ минут — SSP перестали слать трафик.

**Диагностика:**
1. Здоровье эндпоинта: `curl -s localhost:8090/healthz`.
2. Не заблокировал ли партнёров QPS-гейт: `bidder_no_bid_total{reason="qps_exceeded"}`.
3. Логи балансировщика/ингресса: доходят ли запросы до биддера.
4. Статус партнёров в кабинете: не деактивированы ли supply partners.

**Действия:**
- Если биддер жив, а трафика нет — писать партнёру: они могли отключить
  эндпоинт из-за таймаутов/ошибок (см. latency-алерт за прошедшие часы).

## latency — BidderLatencyP99High

**Что значит:** p99 > 50ms. SSP обычно ждут 100–200ms на весь round-trip;
на нашей стороне закладываем < 50ms.

**Диагностика:**
1. Разбивка по бакетам `bidder_bid_latency_seconds_bucket` — хвост или сдвиг всей массы.
2. `bidder_gate_errors_total` — таймауты Redis удлиняют путь.
3. GC/CPU биддера (top, /debug/pprof если включён).
4. Совпадает ли с ростом QPS — возможно, упёрлись в CPU.

**Действия:**
- Redis медленный: проверить latency (`redis-cli --latency`), сеть, размер каплистов.
- CPU: увеличить ресурсы/реплики биддера.
- Некритично, но > 100ms устойчиво — снизить QPS-лимиты партнёров, чтобы не
  копить очередь.

## no-bid-spike — BidderNoBidSpike

**Что значит:** > 98% запросов заканчиваются no-bid при живом трафике.

**Диагностика — смотреть разбивку `bidder_no_bid_total` по reason:**
| reason | Причина | Действие |
|---|---|---|
| `feed_not_loaded` | Фид не загружен | См. feed-stale |
| `unknown_endpoint` | Ключи эндпоинтов не совпадают | Сверить endpoint key с партнёром |
| `bad_auth` | Токен партнёра неверный | Сверить X-SSP-Token |
| `qps_exceeded` | QPS-лимит режет трафик | Поднять лимит, если легитимный рост |
| `parse_error` | Партнёр шлёт невалидный JSON | Запросить примеры запросов у партнёра |
| `currency` | Запросы не в RUB | Обсудить с партнёром валюту трафика |
| `no_candidates` | Таргетинг/бюджеты не матчатся | Нормальное состояние или см. кабинет: диагностика недоставки |
| `gate_reject` | Бюджет/частота исчерпаны | Нормально при исчерпании дневных бюджетов |

**Действия:** это диагностический алерт — эскалировать только если причина
не объясняется состоянием кампаний.

## win-gap — BidderWinGapAnomaly

**Что значит:** биддер активно ставит, но win-notice практически не приходят.

**Диагностика:**
1. `rtb_event_token_rejects_total` — win-notice приходят, но отклоняются?
2. Логи backend `/rtb/win`: 404 Unknown bid — рассинхрон bid id.
3. Цены: возможно ставки стабильно ниже клиринга (посмотреть аукционные floor
   в запросах и наши bid CPM: `bidder_bid_price_cpm_rub_sum / bidder_bids_total`).
4. nurl-макросы в ответах: партнёр мог сменить формат.

**Действия:**
- Токены отклоняются — проверить синхронизацию EVENT_TOKEN_SECRET между
  инстансами backend.
- Ставки ниже floor — поднять bid cap кампаний или пересмотреть shading.

## spend-anomaly — SpendRateAnomaly

**Что значит:** скорость списания в 4+ раза выше 3-часового базлайна.
Главный финансовый алерт.

**Действия (в этом порядке):**
1. **Сначала стоп:** найти кампании с аномальным спендом
   (`SELECT campaign_id, SUM(cost) FROM bid_events WHERE type='win' AND ts > now() - interval '30 min' GROUP BY 1 ORDER BY 2 DESC LIMIT 10`)
   и приостановить их через кабинет.
2. Проверить дубли win-notice: одинаковые bid_id в `bid_events` за окно.
   Дубли = сломан mark_once/Redis. См. redis-down.
3. Проверить пейсинг: не сброшены ли дневные счётчики бюджета в Redis.
4. Если спенд легитимный (клиент поднял бюджеты/ставки) — снять паузу,
   задокументировать в инциденте.

**После восстановления:** сверить спенд за окно с балансами advertisers,
при дублях — компенсирующие транзакции с аудитом.

## token-rejects — EventTokenRejectsSpike

**Что значит:** всплеск win/imp/click с невалидными подписями.

**Диагностика:**
1. Источник (IP/UA в логах backend) — один источник = вероятный replay/скан.
2. Рассинхрон секрета: недавний деплой со сменой EVENT_TOKEN_SECRET
   инвалидирует токены, выданные до деплоя (легитимные события с in-flight
   аукционов будут отклоняться первые минуты).
3. Партнёр сломал макросы — токены обрезаны/перекодированы.

**Действия:**
- Replay-атака: заблокировать источник на уровне ингресса.
- Рассинхрон после деплоя: ожидаемо, затухнет за минуты; если нет — проверить
  что все инстансы читают один секрет.

## freq-win-without-imp — FreqWinWithoutImpHigh

**Что значит:** доля выигранных бидов, чья бронь frequency cap истекла без
imp-пикселя (`rtb_freq_win_without_imp_total` / `rtb_wins_total` за 15 мин)
выше нормы (ориентир — >15 %; нормальный фон 3–10 %: закрытая вкладка,
не отрисованный adm, серверный nurl до рендера).

**Почему важно:** win — это «мы победили», не «показ состоялся» (audit R-06).
Freq cap занимает слот на win временно (env `WIN_IMP_TTL`, дефолт 120 с) и
подтверждает показ на `/rtb/imp`. Высокая доля = пиксель не доходит: кап
недоучитывает показы, а статистика `impressions` занижена относительно `wins`.

**Диагностика:**
1. Разрез по SSP: `SELECT supply_partner_id, count(*) FILTER (WHERE event_type='win') wins,
   count(*) FILTER (WHERE event_type='impression') imps FROM bid_events WHERE created_at > now() - interval '1 hour'
   GROUP BY 1` — у какого партнёра imps/wins просел.
2. У просевшего партнёра: исполняется ли `<script>` внутри banner adm (пиксель
   `/rtb/imp` живёт там); для native — доходит ли `eventtrackers`/`imptrackers`.
3. Если SSP в принципе не даёт клиентского пикселя (in-app SDK) — включить
   `UPDATE supply_partners SET freq_cap_on_win = true WHERE id = ...` (основание
   капа — win; отчётные `impressions` флаг не трогает; только по документации биржи).
4. Медленный пиксель или сломанный? Смотреть пару метрик:
   `rtb_imp_late_after_reservation_total` (пиксель пришёл после истечения брони —
   показ засчитан, но слот успел освободиться) растёт вместе с
   `rtb_freq_win_without_imp_total` → пиксель опаздывает, не сломан. Подтвердить
   p95/p99 `win→imp`:
   `SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM i.created_at - w.created_at))
   FROM bid_events w JOIN bid_events i ON i.bid_id = w.bid_id AND i.event_type='impression'
   WHERE w.event_type='win' AND w.created_at > now() - interval '1 day'` — если p95 > 100 с,
   поднять env `WIN_IMP_TTL` (секунды) **одновременно** у backend и bidder-go: обе
   стороны пишут score брони в один `freqinf` ZSET. Только `win_without_imp` без
   `late` → пиксель не доходит вовсе (п. 2).

## imp-win-mismatch — ImpWithoutWinAnomaly

**Что значит:** импрессий в 3+ раза больше, чем win-ов, за 30 минут.

**Диагностика:**
1. Дубли пикселя: один bid_id с несколькими impression в `bid_events`
   (mark_once должен резать — проверить Redis).
2. Партнёр вызывает imp-пиксель без win-notice (некоторые SSP так делают) —
   тогда это ложный алерт, откалибровать порог для этого партнёра.
3. Replay imp-URL.

**Действия:** при дублях — см. redis-down; при replay — блок источника;
при особенностях партнёра — задокументировать и поднять порог.

## prefilter-drops — PrefilterDropRateHigh

**Что значит:** throttling-предфильтр Go-биддера (`bidder-go/internal/prefilter`)
отсекает более 50% входящих bid-запросов 15+ минут. Метрики — только у Go
(`bidder_*`): после C1d backend bid-запросы не принимает.

**Диагностика:**
1. Разбивка по причинам: `bidder_prefilter_drops_total{reason=...}`:
   - `bot_ua` доминирует — SSP шлёт бот-трафик; собрать примеры UA из
     семпла логов, эскалировать партнёру.
   - `geo_unserved` — партнёр шлёт чужое гео; проверить настройки
     endpoint'а у SSP и `THROTTLE_COUNTRIES`.
   - `no_device_id` — вероятно, партнёр перестал передавать ifa/ip;
     если инвентарь ценный — временно `THROTTLE_REQUIRE_DEVICE_ID=0`.
   - `datacenter_ip` — проверить последние изменения списка подсетей
     (`GET /settings/throttle/subnets`): не добавлена ли слишком широкая /16.
2. Совпал ли рост с деплоем/изменением env/изменением списка подсетей.

**Действия:**
- Ложное срабатывание после изменения конфига — откатить подсеть
  (`DELETE /settings/throttle/subnets`) или env; кэш обновится за ~60 с.
- Реальный мусорный трафик — фильтр работает как задумано; уведомить
  партнёра, приложив разбивку причин. Аварийное отключение всего слоя:
  `THROTTLE_ENABLED=0` + рестарт backend.

## gate-errors — BidderGateErrorRate

**Что значит:** `GateAndReserve` (Lua-резерв дневного бюджета) возвращает
ошибку более чем на 1% бидов. Биддер **не** торгует таким кандидатом
(fail-closed для денег, `handler.go` → `continue`, метрика
`bidder_gate_errors_total`) — доставка проседает пропорционально.

**Диагностика:**
1. `redis-cli -u $REDIS_URL --latency` и `INFO clients|memory` — таймауты,
   maxclients, OOM.
2. Совпал ли рост с деплоем Redis/сетевыми работами; смотрит ли биддер в тот
   же инстанс, что и backend (`REDIS_URL_BIDDER`).
3. Логи биддера: текст ошибки Lua (например, `NOSCRIPT` после рестарта Redis —
   лечится само на следующем вызове; `BUSY` — долгий скрипт/другой клиент).

**Действия:** как для redis-down. При частичной деградации (latency) —
увеличить таймаут Redis-клиента биддера только если p99 бидов остаётся в tmax.

## backend-5xx — Backend5xxShare

**Что значит:** более 2% HTTP-ответов backend — 5xx (считается на всех
ручках кроме `/metrics`: кабинет, `/pixel/collect`, win/imp/click-нотисы).

**Диагностика:**
1. Логи backend — трейсбек первого 5xx. Типичные: `asyncpg` connection refused /
   too many clients (→ postgres-down), Redis timeout (→ redis-down),
   `502` из `/cron/ord-export` (→ ord-export).
2. `GET /health` backend, `docker compose ps`, `pg_isready`.
3. Если 5xx только на `/pixel/collect` — это критично отдельно: пиксель стоит
   на сайтах клиентов; ручка обязана отвечать 204 всегда (см. чеклист 6.7).

**Действия:** устранить причину; сам счётчик сбросится с рестартом backend.

## postgres-down — Postgres недоступен

**Что значит:** backend не может выполнить запросы к БД (`asyncpg` ошибки,
5xx в кабинете, `/rtb-feed/*` отдаёт 500). Сигналы: `Backend5xxShare`,
затем `BidderFeedStale` (через 120 с — Go не смог обновить фид).

**Что делает Go-биддер:** торгует на **последнем успешном снимке фида**
(`internal/feed` — при ошибке refresh снимок не сбрасывается, срока
годности у снимка нет). Значит:
- дневные бюджеты и частотные капы **продолжают** соблюдаться (гейт в Redis);
- паузы кампаний, смена ставок, исчерпание `total_budget`, обнуление баланса
  рекламодателя, отзыв креатива **не доезжают** до биддера;
- win/imp/click-нотисы падают в backend → `bill_win` не выполняется →
  Redis-резерв остаётся, но `spent_total`/баланс в БД не списываются; события
  `rtb:events` копятся в Redis-стриме (consumer дочитает после восстановления).

**Сколько можно жить:** минуты — безболезненно. Часы — риск перерасхода
`total_budget` (не дневного) и показов по кампании, которую рекламодатель
поставил на паузу. **Правило:** если восстановление БД займёт более 30 минут —
остановить торги: `docker compose stop bidder-go` (SSP получат таймауты/ошибки
и снизят трафик — это лучше, чем неучтённые показы).

**Диагностика:**
1. `docker compose ps postgres`, `docker compose logs --tail=200 postgres`.
2. `pg_isready -h <host> -p 5432`; место на диске (`df -h`), OOM в `dmesg`.
3. `SELECT count(*) FROM pg_stat_activity` — не упёрлись ли в `max_connections`
   (backend-пул + worker + beat + миграции).

**Восстановление:**
1. Поднять Postgres; убедиться `pg_isready` → accepting.
2. `docker compose exec backend python migrate.py --dry-run` — список должен
   быть пустым (иначе БД восстановлена из старого бэкапа — см. `ops/restore.sh`).
3. `curl -H "Authorization: Bearer $RTB_API_TOKEN" /rtb-feed/campaigns | jq length`
   — фид отвечает; через ≤ 30 с `bidder_feed_age_seconds` падает к нулю.
4. `docker compose start bidder-go`, если останавливали.
5. Сверить окно инцидента: `bid_events` (после дочитки consumer'ом) против
   Redis `spend:camp:*` и `campaigns.spent_today`; расхождение занести в
   `gap_register.md`, при необходимости скорректировать `spent_total` руками.

## default-partition — BidEventsDefaultPartitionNonEmpty

**Что значит:** в `bid_events_default` есть строки — на момент вставки не было
дневной партиции для `ts`. Запросы по таким строкам медленнее, retention их не
удалит.

**Действия:**
1. `SELECT min(ts), max(ts), count(*) FROM bid_events_default;`
2. `SELECT bid_events_ensure_partitions(<min_date>, current_date + 7);`
3. Перенос: `WITH moved AS (DELETE FROM bid_events_default RETURNING *)
   INSERT INTO bid_events SELECT * FROM moved;` (партиционированная таблица
   сама разложит строки).
4. Проверить, почему не отработал `/cron/retention` (см. cron).

## cron — CronRetentionStale / CronJobFailing

**Что значит:** cron-ручка не запускалась успешно (stale) или упала с
исключением/5xx (failing). Запускает их сервис `scheduler` (compose,
расписание в `scheduled_jobs`, см. [scheduler](#scheduler)); ручки защищены
`CRON_SECRET`.

**Диагностика:**
1. `GET /scheduler/jobs` — задача `enabled`, `next_run_at` в будущем,
   `last_status`/`last_error`; если планировщик вообще не тикает — сработает
   `SchedulerDown`.
2. `last_http_status`: 401 = `CRON_SECRET` у scheduler и backend разошлись;
   503 = `CRON_SECRET` пустой в production (startup_checks не даст так
   стартовать, но проверить); `timeout` — поднять `timeout_seconds`.
3. Логи backend: трейсбек задачи.

**Действия:** запустить руками `curl -H "Authorization: Bearer $CRON_SECRET"
https://<host>/cron/<job>` и прочитать JSON ответа. `reset-daily` идемпотентен и
безопасен в любое время: он обнуляет только счётчики прошлых бизнес-дней
(`spent_today_date < сегодня МСК`, ключи `spend:*:<вчера>`), текущий день не трогает.

## scheduler — SchedulerDown / SchedulerJobsOverdue / SchedulerDispatchFailing / SchedulerLeaseLost

**Что это.** Фоновые задачи (`/cron/*`) запускает сервис `scheduler` из compose
(`backend/scheduler.py`, audit O-01 / C1b). Расписание — таблица
`scheduled_jobs`; смотреть и править — `GET/PATCH /scheduler/jobs` (admin) или
`docker compose exec postgres psql -c "select name, enabled, schedule, next_run_at, last_status, last_error from scheduled_jobs order by name"`.
Метрики планировщика — `scheduler:8001/metrics`; факт успешного выполнения
самой задачи по-прежнему в backend: `cron_job_last_success_timestamp_seconds{job}`.

**SchedulerDown.** `docker compose ps scheduler`, `docker compose logs --tail 200 scheduler`.
Типично: контейнер в рестарт-цикле из-за конфигурации (в логе `config: ...` —
не задан `CRON_SECRET`/`DATABASE_URL`), либо `tick failed` — БД недоступна
(см. [postgres-down](#postgres-down)). Планировщик не падает при сбое тика и
сам продолжит, когда БД вернётся; после длительного простоя пропущенные
запуски схлопываются в один (misfire policy), «догонять» ничего не нужно.
Если задачи критичны прямо сейчас — `POST /scheduler/jobs/{name}/run` или
временно вручную: `curl -H "Authorization: Bearer $CRON_SECRET" http://backend:8000/cron/<job>`.

**SchedulerJobsOverdue.** Задачи просрочены, но не захвачены. Причины: планировщик
жив, но все слоты заняты долгой задачей (`locked_at` не NULL у `ord_export`/
`retention`, concurrency 4) — дождаться или поднять `timeout_seconds`; либо
lease протух и висит (`locked_at` старше 2×timeout — захватится на следующем
тике сам). `next_run_at IS NULL` — планировщик ещё не пересчитал новое
расписание (1 тик).

**SchedulerDispatchFailing.** Смотреть `last_status`/`last_http_status`/`last_error`:
`401` — `CRON_SECRET` у scheduler и backend разошлись (оба берут его из `.env`,
пересоздать оба контейнера); `503` — backend в production без `CRON_SECRET`;
`timeout` — поднять `timeout_seconds` задачи (PATCH) или разбираться, почему
задача стала долгой; `unreachable` — backend не поднят / имя `backend` не
резолвится; `5xx` — исключение внутри задачи, дальше по
[cron](#cron) (лог backend по пути `/cron/<job>`).

**SchedulerLeaseLost (critical).** Выполняющаяся задача не продлевала lease
(heartbeat раз в 20 с) дольше 90 с, и её захватила другая реплика — задача
могла выполниться **дважды параллельно**. Причины: пауза процесса scheduler
(OOM/stop-the-world, `docker pause`), недоступность PostgreSQL с точки зрения
scheduler при живом backend. В логе scheduler — `lease taken over by another
worker` с именем задачи; у второй реплики — обычный запуск. Действия: убедиться,
что БД отвечала (`pg_isready`, логи PG), проверить идемпотентность конкретного
прогона (`ord_export` — `ord_export_log` за период, `reconcile_reserves` —
`reserve_reconcile_drift_total`; поминутные `sweep_burl`/`audience_sync`
идемпотентны по построению). Если метрика растёт без сбоев БД — scheduler
задыхается (CPU/лимиты контейнера): смотреть `scheduler_jobs_inflight` и
длительности в `GET /scheduler/jobs`.

## ord-export — OrdExportFailed / OrdExportLag / OrdExportSkippedWithoutErid / OrdExportRowsRejected

**Что значит:** месячная выгрузка статистики показов в ОРД (`/cron/ord-export`,
ФЗ-347) не удалась (`failed`) или не было успеха более 40 дней (`lag`).

**Дедлайн:** статистика за месяц N подаётся в ОРД до конца месяца N+1. Cron
идёт в начале месяца — запас на повтор есть, но не бесконечный. За пропуск
подачи отвечает рекламораспространитель — эскалация ответственному за ОРД
в тот же день.

**Диагностика:**
1. `SELECT period, status, rows_sent, error, started_at, finished_at FROM
   ord_export_log ORDER BY started_at DESC LIMIT 5;`
2. Причина в `error`: авторизация провайдера (`ORD_*` в `.env`), отклонённый
   erid (креатив с `ord_creatives.status` не `registered`), сеть.
3. Кабинет «Маркировка (ОРД)» — статусы контрагентов/креативов, `ord_error`.

**Действия:**
1. Устранить причину, повторить:
   `curl -H "Authorization: Bearer $CRON_SECRET" "https://<host>/cron/ord-export?period=YYYY-MM"`
   — идемпотентно: если за период уже есть `ok`, вернёт `skipped`.
2. Если провайдер отклонил часть строк (статус `partial`, alert
   OrdExportRowsRejected) — причины построчно: `SELECT creative_id, version, erid,
   status, error FROM ord_export_rows WHERE log_id = '<id>' AND status = 'rejected';`
   или `GET /ord/exports/{log_id}/rows`. Исправить регистрацию креатива
   (`POST /ord/creatives/{id}/register`, история версий —
   `GET /ord/creatives/{id}/versions`) и повторить выгрузку: `partial` не
   считается выгруженным, cron отправит период снова; `externalStatisticId`
   (`<creative_id>:v<version>:<YYYY-MM>`) тот же, в ОРД это upsert, дублей нет.
3. Коррекция уже принятого периода (поздние показы, исправленные суммы):
   `.../cron/ord-export?period=YYYY-MM&force=1` — запись `kind=stats_correction`,
   данные в ОРД перезаписываются тем же `externalStatisticId`.
4. Убедиться, что `ord_export_last_success_timestamp_seconds` обновился.

**Версии erid:** смена содержимого одобренного креатива закрывает текущую
версию (`ord_creatives.superseded_at`) и регистрирует новую с новым erid;
показы относятся к версии, действовавшей на конец часа. Контрагент, договор и
ККТУ после выдачи erid заморожены (`creatives.ord_locked_at`, API отвечает
`409 ord_locked`) — для другого заказчика/договора создаётся новый креатив.
Спека: `docs/audit/ord_spec.md`.

**OrdExportSkippedWithoutErid (показы без маркировки):** выгрузка прошла, но
у части креативов были показы без зарегистрированного erid — такие строки в ОРД
отправить нельзя, и они перечислены в `skipped_without_erid` ответа и в
`ord_export_log.request`. Это не ошибка выгрузки, а нарушение на стороне
трафика: проверить, как креатив попал в фид без erid (гейт запуска
`ord_marking`, ручное снятие `LAUNCH_GATE`, показы до одобрения), немедленно
поставить кампанию на паузу, зарегистрировать креатив в ОРД и повторить выгрузку
за период с `force=1` — либо согласовать с ответственным за ОРД подачу вручную.
Если за период были показы, но ни одной строки с erid, выгрузка получает статус
`failed` (не `ok`): немаркированная реклама — не успех. Gauge обновляется на каждом прогоне
`/cron/ord-export`; после исправления и повтора он станет 0.

---

## ord-notifications — OrdErirErrorsUnresolved / OrdErirErrorsStale / OrdNotificationsLag

**Что значит.** ОРД (Ozon) принимает объект синхронно и выдаёт `external_id`/erid,
а ЕРИР проверяет данные позже и присылает ошибку в ОРД. Мы забираем их раз в час
(`GET /cron/ord-notifications` → `POST /api/external/notification/error_list`,
курсор `platform_settings.ord_notification_cursor`) в таблицу `ord_notifications`
и переводим объект в статус `erir_error` с текстом в `ord_error`/`error`
(`counterparties`, `ord_contracts`, версия `ord_creatives`, `ord_invoices`).
`ord_external_id` при этом сохраняется: объект в ОРД есть, но ЕРИР его не принял.

**Что делать.**
1. `GET /ord/notifications` (или блок «Ошибки ЕРИР» на `/ord`) — тип объекта,
   наш id, текст ЕРИР, ссылка `url` в кабинет ОРД.
2. Исправить данные объекта (ИНН/ОГРН, реквизиты договора, описание/ККТУ
   креатива, суммы акта) и зарегистрировать/отправить его повторно из UI.
   Успешный ответ ОРД закрывает открытые уведомления объекта (`resolved_at`,
   `resolved_reason=reregistered`) и возвращает `registered`/`ok`.
3. `OrdNotificationsLag`: `GET /cron/ord-notifications` вручную (с `CRON_SECRET`),
   ответ содержит `cursor_from/cursor_to/fetched/new`; `502`/`OrdError` — ключ
   ОРД или сеть. Сдвигать курсор назад можно правкой
   `platform_settings.ord_notification_cursor.last_id` — уведомления идемпотентны
   по `(provider, notification_id)`.
4. Пока объект в `erir_error`, он продолжает участвовать в фиде/экспорте
   статистики (у него есть erid); исправление обязательно, но остановка показов
   не требуется.

**Метрики.** `ord_notifications_unresolved`, `ord_notifications_unresolved_oldest_age_seconds`,
`ord_notifications_last_success_timestamp_seconds`, `ord_notification_errors_total`.

## events-consumer — RtbEventsConsumerLagHigh / LagCritical / PendingStuck / Errors / Silent

**Что это.** Go-биддер не пишет в Postgres: события `bid`/`loss` он складывает в
Redis Stream `rtb:events` (MAXLEN ~200k). Backend-consumer (`events_consumer.py`,
группа `backend`) переносит их в `bid_events` и `stats_hourly.bids` — это
знаменатель win-rate и eCPM в кабинете. Надёжность (audit E-03): XACK только
после COMMIT; stream id каждого события пишется в `rtb_events_inbox` в той же
транзакции (повтор батча не задваивает агрегаты); после ошибки consumer
перечитывает свой pending (`0`) прежде чем брать новое; pending чужих
consumer-ов (старый HOSTNAME) забирается XAUTOCLAIM через 60 с; событие, не
записавшееся 5 раз, уходит в `rtb:events:dead` и подтверждается.

**Метрики.** `rtb_events_stream_length` (XLEN), `rtb_events_pending` (PEL),
`rtb_events_lag` (ещё не доставлено группе), `rtb_events_consumed_total`,
`rtb_events_claimed_total`, `rtb_events_consumer_errors_total`.

**LagHigh / LagCritical** — consumer не успевает. При lag → 200k Redis начнёт
обрезать стрим и события потеряются безвозвратно (win-rate в кабинете уедет
вверх, `bids` в отчётах занижены).
1. `rtb_events_consumer_errors_total` растёт? → смотреть логи backend
   `events consumer iteration failed` — обычно PG (latency, locks на
   `stats_hourly`, закончились соединения пула).
2. Ошибок нет, но lag растёт → чистая пропускная способность. Временно
   поднять `RTB_EVENTS_BATCH` (500 → 2000) или запустить второй backend с
   `RTB_EVENTS_CONSUMER=1` (группа общая, consumer-ы делят поток).
3. После ликвидации: сверить `SELECT sum(bids) FROM stats_hourly` за час с
   `bidder_bid_requests_total` — разница = потерянные события (если стрим
   обрезался).

**PendingStuck** — батчи читаются, но не подтверждаются > 5 мин.
1. `errors_total` растёт → запись в PG падает на каждом повторе. Смотреть
   текст ошибки: constraint/тип → битое событие (уйдёт в dead через
   MAX_DELIVERIES=5 доставок); connection → PG.
2. `XPENDING rtb:events backend - + 10` → если consumer-имя не совпадает с
   текущим HOSTNAME, ждать XAUTOCLAIM (60 с) или проверить, что процесс жив.
3. `XLEN rtb:events:dead` → poison-события. Разобрать (`XRANGE rtb:events:dead - +`),
   починить нормализацию, при необходимости переложить обратно `XADD rtb:events`.

**Errors** — > 20 падений итераций за 10 мин. См. пункт 1 выше; каждая ошибка
повторяется с backoff 1→30 с, pending не теряется.

**Silent** — биддер торгует, а consumer не пишет ничего. Проверить: backend
запущен с `REDIS_URL`; `RTB_EVENTS_CONSUMER` не `0`; в логах нет
`events consumer group bootstrap failed` (ретраится, но если Redis недоступен —
будет молчать); `XINFO GROUPS rtb:events` показывает группу `backend` с живыми
consumers. Правило сравнивает `rtb_events_consumed_total` (backend) с
`bidder_bid_requests_total` (bidder) через `sum()` — серии с разных
скрейпов, без агрегации оно молча не срабатывало (audit O-05).

**RtbEventsConsumerDown** (`rtb_events_consumer_up == 0`, из `/ready`) —
задача consumer'а в процессе backend не запущена (`not_running`), не
завершала успешную итерацию дольше `READY_CONSUMER_STALE_S` (`stale`) или lag
выше `READY_MAX_EVENTS_LAG` (`lagging`). В отличие от Silent не зависит от
трафика биддера — ловит мёртвый consumer и в тихие часы. `disabled`
(`RTB_EVENTS_CONSUMER=0`) считается ok: consumer сознательно живёт в другом
процессе; убедиться, что он там есть.

**Redis пересоздан / flush.** Группа стартует с курсора в `rtb_events_cursor`
(пишется атомарно с каждым батчем); если курсора нет — с `0` (всё, что есть в
стриме). Ничего руками делать не нужно.

---

## conversion-attribution — ConversionAttributionCoverageLow

**Что значит:** за 7 дней меньше 80 % конверсий пикселя удалось привязать к
биду (`conversion_attributions.attribution_status = 'attributed'`). Отчёты по
CPA/CVR по любым срезам занижают конверсии; backend это знает и **сам
отключает** авто-действия по conversion-KPI (`rules_engine` пропускает правила
по `cpa/cvr/conversions`, `optimizer` не блокирует по правилам 1 и 3) — до
восстановления покрытия кампании работают без этой защиты.

**Где смотреть:**
- `conversions_unattributed_total{status}` — `unattributed` растёт: clid не
  доходит до пикселя; `out_of_window` растёт: бид старше окна.
- `conversion_attribution_coverage{dimension}` — если низко только для
  `os/browser/tagid/...`, а `campaign` в норме, проблема не в атрибуции, а в
  том, что SSP не передаёт это поле в bid request (строки «Не указано»).
- В UI: /analytics → бейдж покрытия и строки «Без атрибуции» / «Вне окна
  атрибуции» на уровне рекламодателя.

**Действия:**
1. `SELECT attribution_status, count(*) FROM conversion_attributions
   WHERE converted_at >= now() - interval '7 days' GROUP BY 1;`
2. `unattributed` доминирует → проверить креативы: click_url должен содержать
   `${CLICK_URL}`-редирект через `/click`, который дописывает `cdsp_clid` на
   лендинг; лендинг не должен терять query на своих редиректах; пиксель должен
   стоять на странице приземления (clid читается из `location.search` и
   кладётся в localStorage).
3. `out_of_window` доминирует → длинный цикл покупки: поднять
   `PIXEL_ATTRIBUTION_WINDOW_DAYS` (backend), согласовав с рекламодателем.
4. `raw_payload` / `external_conversion_id` (`oid` в beacon) — сверить спорные
   конверсии с CRM рекламодателя.

## billing-burl — WinWithoutBurlRateHigh / PendingBurlQueueStuck

**Что значит:** у SSP с `supply_partners.billing_event = 'burl'` расход
списывается на billing notice (`/api/rtb/burl`, OpenRTB 2.5 §4.3.3), а не на
win notice (audit R-07). `WinWithoutBurlRateHigh` — по партнёру более 20 %
выигрышей за час так и не получили burl за `BURL_TTL` (600 с): резерв бюджета
возвращён, денег не списано, `bid_event_once` получил `win_expired`.
`PendingBurlQueueStuck` — очередь `pending:burl` не чистится: `/cron/sweep-burl`
(каждую минуту) не работает.

**Почему важно:** win — не показ и не деньги. Если биржа биллит по burl, а мы
списывали по nurl (режим `win`), при сверке SSP покажет меньше, чем наши
отчёты (win без рендера). Если биржа вовсе не шлёт burl, а партнёр в режиме
`burl`, мы недосписываем: `spend` и `billable_imps` занижены, а кампании
крутятся «бесплатно».

**Модель (одна на Python и Go, `luaSettleWin` байт в байт):**
`bidstate:{bid}`: `(nil, резерв) --nurl--> won --burl--> win` (резерв →
clearing, код 1); `won --sweep--> won_expired` (резерв возвращён, код 0/2 у
sweeper); `won_expired --burl--> win` (clearing досписан, код 3, событие
`billable` с `late = true`). Повторные nurl/burl/sweep ничего не двигают
(`rtb_burl_duplicate_total`). Режим `win`: burl игнорируется, деньги уже сняты
на nurl (`rtb_burl_in_win_mode_total` растёт → партнёр кандидат на `burl`).

**Диагностика:**
1. Разрез по партнёру: `rtb_burl_outcomes_total{partner, outcome}` — `won` без
   `billed`/`late` → burl не приходит вовсе; много `late` → burl приходит, но
   позже `BURL_TTL` (поднять `BURL_TTL` у backend; grace до sweeper Go
   `BURL_SWEEP_GRACE` = 300 с добавляется автоматически).
2. В БД: `SELECT supply_partner_id, count(*) FILTER (WHERE event_type='win') wins,
   count(*) FILTER (WHERE event_type='billable') billable,
   count(*) FILTER (WHERE event_type='billable' AND late) late
   FROM bid_events WHERE created_at > now() - interval '1 hour' GROUP BY 1;`
3. `rtb_burl_without_nurl_total` растёт → биржа шлёт только burl без nurl:
   допустимо (расход и `billable_imps` считаются, `wins` — нет); учесть при
   сверке win rate.
4. `rtb_burl_expire_reserve_missing_total` растёт → sweeper Go добрался до
   `pending:bids` раньше `/cron/sweep-burl`: cron не каждую минуту или
   `BURL_SWEEP_GRACE` мал. Деньги не теряются (burl → recharge), но метрика
   `rtb_win_without_burl_total` недосчитывает.
5. `PendingBurlQueueStuck`: `cron_job_runs_total{job="sweep_burl",result="error"}`,
   логи `/cron/sweep-burl`, Redis `ZCARD pending:burl`.

**Действия:**
- Биржа не шлёт burl (по документации или по факту) →
  `UPDATE supply_partners SET billing_event = 'win' WHERE id = ...` (или в UI
  /supply → «Событие расхода»). Кеш режима в backend — 60 с.
- Биржа шлёт burl, но партнёр ещё на `win` → включать `burl` только по
  чеклисту онбординга: (а) в документации биржи burl = billable event и
  содержит `${AUCTION_PRICE}`; (б) в тестовом трафике видны `billable` с
  `clear_price`; (в) сверка `billable_imps` с отчётом SSP за сутки сходится.
  Новые партнёры создаются сразу с `burl` (DEFAULT миграции 046).
- Сверка с SSP: наша billable-цифра — `stats_hourly.billable_imps` /
  `stats_math.BILLABLE_FROM_EVENTS_SQL`; `wins` и `impressions` для сверки
  денег не годятся.

## node-resources — NodeDiskSpaceLow / NodeDiskSpaceCritical / NodeInodesLow / NodeFilesystemReadOnly / NodeMemoryPressure / NodeHighLoad

**Что значит:** ресурсы хоста (VPS) на исходе — данные `node-exporter`
(profile monitoring, host mounts read-only). Всё живёт на одном диске:
PostgreSQL (WAL, партиции `bid_events`), Redis AOF, `uploads`, docker-образы
и логи. Docker-слои (`overlay`) и `tmpfs` из filesystem-алертов исключены.

**Диск (`Low` < 15 %, `Critical` < 5 %, `Inodes` < 10 %):**
1. `df -h /` и `df -i /`; `docker system df` — образы/кэш сборки часто первые.
2. Безопасно освободить: `docker image prune -a` (не трогает volumes),
   `docker builder prune`; старые бэкапы в `BACKUP_DIR` (ретенция
   `ops/backup.sh`, локальные копии есть offsite); `journalctl --vacuum-size=200M`.
3. Растёт Postgres → `SELECT pg_size_pretty(pg_database_size('centraldsp'))`,
   топ таблиц по `pg_total_relation_size`; проверить, что `/cron/retention`
   отрабатывает (`CronRetentionStale`) и партиции `bid_events` дропаются.
4. При 0 % Postgres останавливается (PANIC на WAL), Redis перестаёт писать
   AOF — сначала место, потом разбор. Не удалять файлы из
   `pg_wal` руками.

**`NodeFilesystemReadOnly` (critical):** ядро перемонтировало ФС read-only
после ошибок ввода-вывода — диск деградирует. `dmesg | tail`, `smartctl`.
Немедленно `ops/backup.sh` на другой носитель (offsite), планировать перенос
VPS; Postgres/Redis на такой ФС не пишут, кабинет и биллинг встанут.

**`NodeMemoryPressure` (< 10 % MemAvailable):** следующий — OOM-killer,
первым обычно погибает postgres или redis (самый большой RSS), Docker
рестартует, но AOF/WAL-recovery занимает минуты. `docker stats --no-stream`;
`maxmemory` Redis, `shared_buffers`/`work_mem` Postgres, `RTB_EVENTS_BATCH`.

**`NodeHighLoad` (load5 > 2× CPU, 15 мин):** CPU-голодание —
`bidder_bid_latency` p99 растёт, SSP режут по tmax (потерянные аукционы),
consumer и cron тормозят. `top` — кто ест: миграция/ретенция в неурочное
время, `stats_rollup`, ML-обучение (`ml_train` должен быть выключен в
`scheduled_jobs` по умолчанию).

---

## notifications — NotifySmtpNotConfigured / NotifyOutboxStuck / NotifyEmailsFailing

Сторожевой пёс самой системы оповещения (C4b). Все внешние письма — алерты
суперадмину от Alertmanager и бизнес-уведомления рекламодателям — проходят
через таблицу `notification_outbox`; отправляет их задача scheduler
`notify_dispatch` (`/cron/notify-dispatch`, раз в минуту) по SMTP из `SMTP_*`.
Если этот контур сломан, остальные алерты **тоже никуда не доходят** — поэтому
эти правила стоит смотреть первыми при любой «подозрительной тишине».

**NotifySmtpNotConfigured** (warning): в очереди есть письма, `SMTP_HOST`/`SMTP_FROM`
пустые. Ничего не потеряно — заполнить `SMTP_*` в `.env`, `docker compose up -d backend
scheduler`; dispatcher отправит хвост в течение минуты.

**NotifyOutboxStuck** (critical): SMTP настроен, но старейшее письмо ждёт > 30 мин.
1. Задача запускается? `cron_job_runs_total{cron_job="notify_dispatch"}` растёт,
   `scheduler_jobs_overdue` = 0. Нет — см. [#scheduler](#scheduler).
2. Что говорит SMTP:
   ```sql
   SELECT id, recipient, kind, attempts, next_attempt_at, left(last_error, 200)
   FROM notification_outbox WHERE status = 'queued' ORDER BY created_at LIMIT 20;
   ```
   `last_error` — текст исключения `smtplib`: `SMTPAuthenticationError` (логин/пароль
   или у провайдера нужен «пароль приложения»), `SMTPRecipientsRefused` (адрес
   отвергнут — исправить в настройках), `ConnectionRefused`/`timeout` (порт/файрвол:
   587 STARTTLS или 465 `SMTP_SSL=1`).
3. Проверить руками из контейнера backend: `docker compose exec backend python -c
   "import asyncio, notify_outbox as n; from email.message import EmailMessage;
   m=EmailMessage(); m['From']=n.smtp_config().sender; m['To']='you@example.com';
   m['Subject']='test'; m.set_content('ok'); asyncio.run(n.smtp_send(n.smtp_config(), m))"`.
4. После исправления ничего сбрасывать не нужно: backoff 1→5→15→60 мин, строки
   уйдут сами. Ускорить — `UPDATE notification_outbox SET next_attempt_at = now()
   WHERE status = 'queued'` и `POST /scheduler/jobs/notify_dispatch/run`.

**NotifyEmailsFailing** (warning): письма исчерпали `NOTIFY_MAX_ATTEMPTS` (8) и
переведены в `failed`. Обычно один-два адреса с опечаткой — `SELECT recipient,
last_error FROM notification_outbox WHERE status='failed' AND created_at > now() -
interval '1 day'`. Переотправить после исправления: `UPDATE ... SET status='queued',
attempts=0, next_attempt_at=now() WHERE id IN (...)`.

Retention: `sent`/`failed` старше 30 дней удаляет `/cron/retention`; `queued` не трогает.

## Куда смотреть в первую очередь при любом алерте

0. `docker compose ps` и `up` в Prometheus: если цель `TargetDown` — метрик
   нет и остальные алерты по ней молчат. `backend /ready`, `bidder /healthz` —
   какая зависимость сломана.
1. `bidder :8090/metrics` и `backend /metrics` — снапшот всех счётчиков.
2. Логи биддера (stdout, JSON slog) и backend.
3. Таблица `bid_events` — источник истины по фактам доставки.
4. Кабинет: статусы кампаний, балансы, диагностика недоставки.
