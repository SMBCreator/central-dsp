# Runbook: RTB-алерты CentralDSP

Часть D (GAP-011). Метрики: Go-биддер `:8090/metrics` (серии `bidder_*`),
Python backend `/metrics` (серии `rtb_*`). Правила: `ops/prometheus/alerts.yml`.

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
Freq cap занимает слот на win временно (`WIN_IMP_TTL`, сейчас 120 с) и
подтверждает показ на `/rtb/imp`. Высокая доля = пиксель не доходит: кап
недоучитывает показы, а статистика `impressions` занижена относительно `wins`.

**Диагностика:**
1. Разрез по SSP: `SELECT supply_partner_id, count(*) FILTER (WHERE event_type='win') wins,
   count(*) FILTER (WHERE event_type='impression') imps FROM bid_events WHERE created_at > now() - interval '1 hour'
   GROUP BY 1` — у какого партнёра imps/wins просел.
2. У просевшего партнёра: исполняется ли `<script>` внутри banner adm (пиксель
   `/rtb/imp` живёт там); для native — доходит ли `eventtrackers`/`imptrackers`.
3. Если SSP в принципе не даёт клиентского пикселя (in-app SDK) — включить
   `UPDATE supply_partners SET win_counts_as_imp = true WHERE id = ...` (кап
   считает показ на win; только по документации биржи).
4. Если пиксель есть, но опаздывает: p95/p99 `win→imp` —
   `SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM i.created_at - w.created_at))
   FROM bid_events w JOIN bid_events i ON i.bid_id = w.bid_id AND i.event_type='impression'
   WHERE w.event_type='win' AND w.created_at > now() - interval '1 day'` — если p95 > 100 с,
   поднять `WIN_IMP_TTL` (cache.WIN_IMP_TTL ↔ redisx.DefaultWinImpTTL, менять парой).

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

**Что значит:** throttling-предфильтр (`backend/prefilter.py`) отсекает более
50% входящих bid-запросов 15+ минут.

**Диагностика:**
1. Разбивка по причинам: `rtb_prefilter_drops_total{reason=...}`:
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
исключением/5xx (failing). Расписание — `crontab`/systemd timers на VPS или
Vercel Cron; ручки защищены `CRON_SECRET`.

**Диагностика:**
1. `crontab -l` / `systemctl list-timers` — расписание на месте, секрет в
   команде совпадает с `.env`.
2. Логи nginx по `/cron/<job>`: 401 = секрет не совпал; 503 = `CRON_SECRET`
   пустой в production (startup_checks не даст так стартовать, но проверить).
3. Логи backend: трейсбек задачи.

**Действия:** запустить руками `curl -H "Authorization: Bearer $CRON_SECRET"
https://<host>/cron/<job>` и прочитать JSON ответа. `reset-daily` идемпотентен и
безопасен в любое время: он обнуляет только счётчики прошлых бизнес-дней
(`spent_today_date < сегодня МСК`, ключи `spend:*:<вчера>`), текущий день не трогает.

## ord-export — OrdExportFailed / OrdExportLag / OrdExportSkippedWithoutErid

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
2. Если провайдер отклонил часть строк — по каждой найти креатив, проверить
   регистрацию (`POST /ord/creatives/{id}/register`), повторить выгрузку.
3. Убедиться, что `ord_export_last_success_timestamp_seconds` обновился.

**OrdExportSkippedWithoutErid (показы без маркировки):** выгрузка прошла, но
у части креативов были показы без зарегистрированного erid — такие строки в ОРД
отправить нельзя, и они перечислены в `skipped_without_erid` ответа и в
`ord_export_log.request`. Это не ошибка выгрузки, а нарушение на стороне
трафика: проверить, как креатив попал в фид без erid (гейт запуска
`ord_marking`, ручное снятие `LAUNCH_GATE`, показы до одобрения), немедленно
поставить кампанию на паузу, зарегистрировать креатив в ОРД и повторить выгрузку
за период после удаления строки `ok` из `ord_export_log` — либо согласовать с
ответственным за ОРД подачу вручную. Gauge обновляется на каждом прогоне
`/cron/ord-export`; после исправления и повтора он станет 0.

---

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
consumers.

**Redis пересоздан / flush.** Группа стартует с курсора в `rtb_events_cursor`
(пишется атомарно с каждым батчем); если курсора нет — с `0` (всё, что есть в
стриме). Ничего руками делать не нужно.

---

## Куда смотреть в первую очередь при любом алерте

1. `bidder :8090/metrics` и `backend /metrics` — снапшот всех счётчиков.
2. Логи биддера (stdout, JSON slog) и backend.
3. Таблица `bid_events` — источник истины по фактам доставки.
4. Кабинет: статусы кампаний, балансы, диагностика недоставки.
