# План исправлений по Full Release Audit 2026-09-05

**Источник:** `docs/audit/full_release_audit_2026-09-05.md` (ревизия `4a42e95`).
**Ветка:** `feat/avito-ui-kktu`, коммит и push после каждого блока.
**Цель:** платформа торгует только через Go-биддер, исполняет все режимы закупки и форматы, которые может выбрать рекламодатель в кабинете, и готова к выгрузке на сервер и тестированию с реальным SSP.

## Принятые решения

| Вопрос | Решение | Следствие |
|---|---|---|
| Scope закупки | Полный паритет UI ↔ Go: CPM, CPC, pacing, валюта, все форматы, скоринг/оптимизатор | R-03/R-04/F-02 закрываются реализацией в Go, не запретами в UI |
| Business day | Полночь Europe/Moscow | Дата бизнес-дня в ключе `spend:*`, TTL до следующей полуночи МСК, cron reset перестаёт быть источником истины |
| Celery в Compose | Убрать мёртвые `worker/beat/flower`; добавить сервис `cron` (curl `/cron/*` по расписанию) | Сервер поднимается одной командой без ручного crontab |
| Порядок | A → B → C | Деньги/безопасность → сквозной RTB-контур → эксплуатация |

## Соглашения по каждому пункту

- Каждая находка закрывается **кодом + регрессионным тестом**, который воспроизводит исходную проблему (из раздела 14 аудита) и падает на старом коде.
- Всё, что нельзя доказать без VPS/реального SSP, помечается `[V]` и уходит в план серверной приёмки (раздел 15 аудита), а не считается закрытым.
- Не трогаем production данных. Все прогоны — на локальном Postgres/Redis.

---

## Очередь A — деньги и публичный риск

### A1. M-01 + M-02 — баланс только через финансовые операции, атомарно и идемпотентно
- `schemas.py`: убрать `balance` из `AdvertiserIn`/`AdvertiserPatch`; начальный баланс 0.
- `routers/advertisers.py`: игнорировать/отклонять `balance` (422), убрать из INSERT/UPDATE.
- `routers/billing.py`: `deposit`/`adjust` в одной DB-транзакции (`SELECT ... FOR UPDATE` → UPDATE → INSERT transactions); `Decimal` с квантованием до копеек; заголовок/поле `idempotency_key`, уникальный индекс `transactions(idempotency_key)`; повтор возвращает исходную проводку без второго списания.
- Миграция: `transactions.idempotency_key TEXT UNIQUE`.
- Тесты: manager/advertiser/agency не могут задать balance; deposit с падением INSERT не меняет остаток; повтор с тем же ключом — одна проводка; сумма `transactions` == `balance`.

### A2. M-04 — единственная граница бизнес-дня (Europe/Moscow)
- Общий модуль `backend/business_day.py`: `business_date(now)`, `seconds_until_next_business_midnight(now)`.
- `cache.py`: ключи `spend:<id>:<YYYY-MM-DD>` с датой МСК; TTL до следующей полуночи МСК + запас.
- Go `redisx.go`/`handler.go`: тот же формат ключа и TTL (`time.LoadLocation("Europe/Moscow")`, fallback фиксированный +03:00), Lua-скрипт принимает ключ с датой.
- `cron.py:reset_daily_budgets`: становится no-op с предупреждением (ключи самоистекают), либо удаляет только ключи **вчерашней** даты — не текущей.
- `vercel.json`, `docs/DEPLOYMENT.md`: убрать reset-daily из расписания как обязательный.
- Тесты: Python/Go согласованность ключа на 20:59/21:00/23:59/00:00 UTC; повтор cron не стирает расход текущего дня; поздний loss после полуночи не уменьшает новый день (ключ с датой ставки).

### A3. M-03 — Redis как критическое хранилище
- Compose: `redis` с named volume, `appendonly yes`, `appendfsync everysec`, `maxmemory-policy noeviction`, healthcheck; один `REDIS_URL` для backend и Go (убрать `REDIS_URL_BIDDER` дефолт на localhost — обязателен и равен backend).
- `startup_checks.py`: при `APP_ENV=production` проверять `maxmemory-policy == noeviction` через `CONFIG GET`, иначе fail-fast; логировать `redis_version`, `aof_enabled`.
- Go старт: та же проверка politики (warning → refuse to serve если `REDIS_REQUIRE_NOEVICTION=1`).
- Скрипт `ops/redis_rebuild_spend.py`: восстановление `spend:*` за текущий бизнес-день из `bid_events`/`transactions` (для recovery после потери Redis).
- Тест: startup_checks падает при `allkeys-lru` в production.

### A4. M-05 + M-06 — финансовый reservation lifecycle
- Feed: добавить `advertiser_balance_kopecks`, `total_budget_remaining` в Candidate.
- Redis: ключи `adv:<id>:committed:<date>` и `camp:<id>:total_committed`; Lua `GateAndReserve` резервирует одновременно daily(campaign), daily(line item), advertiser available, total remaining; возврат по lurl/expire атомарно снимает все четыре.
- Per-bid резерв: `bidmeta` содержит `reserve_kopecks` и `business_date`; `ReleaseGate(bidID)` читает из meta, а не из аргументов; `lossdone` ставится **после** успешного refund; terminal state `win|loss|expired` в meta.
- Sweeper в Go: каждые N секунд `SCAN bidmeta` с истёкшим TTL-порогом без terminal state → expire → refund.
- `billing_core.bill_win`: корректировка `reserve - clearing` (возврат разницы) идемпотентно по `bid_id`.
- Тесты Go: duplicate/out-of-order loss, failed refund → retry, midnight, два инстанса; Python: малый баланс + большие daily + late wins → обязательства ≤ balance.

### A5. L-01 — mock-ОРД запрещён в production
- `ord/adapter.py`: `MockOrdAdapter` только при `APP_ENV in (dev, test)` или явном `ORD_ALLOW_MOCK=1` вне production; в production без валидных настроек — `OrdNotConfigured` (503 на регистрацию, feed gate закрыт для РФ).
- `ord_creatives.provider` + `provenance` (`mock|ozon|...`); `ERID_FEED_GATE_SQL` требует `provider <> 'mock'` в production.
- `startup_checks.py`: production требует настроенный provider либо явный `ORD_REQUIRED=0` с предупреждением (для не-РФ трафика).
- UI: бейдж «mock» → блокирующее предупреждение в production.
- Тест: production + пустые настройки → 503/не выдаётся erid; существующий mock erid не проходит feed gate.

### A6. S-01 — CSRF/Origin
- `main.py`: middleware для всех state-changing методов: `Origin`/`Referer` должен совпадать с `PUBLIC_ORIGINS` (env), иначе 403; исключения: `/rtb/*`, `/pixel/*`, `/cron/*`, `/bidder/*` (у них своя auth).
- Cookie: `SameSite=Lax` по умолчанию в production, `None` только при `AUTH_COOKIE_SAMESITE=none` (preview).
- Next proxy: пробрасывать `Origin`.
- Тест: POST без body с чужим Origin → 403 и нет UPDATE; с правильным Origin → 200.

### A7. S-04 — bootstrap/config/auth edges
- Bootstrap: `pg_advisory_xact_lock` + повторная проверка count внутри транзакции; `REGISTRATION_MODE` — enum, default `closed`, неизвестное значение → fail-fast.
- Production запрещает `RTB_ALLOW_UNSIGNED_EVENTS=1`, `RTB_PYTHON_SERVING=true`, отключённые gates.
- Go: fail-fast на секретах < 32 байт или с плейсхолдером.
- Пароль: max 72 байта в UTF-8 (валидатор), bcrypt в `run_in_threadpool`; смена пароля отзывает остальные сессии.
- Тесты: два конкурентных register на пустой БД → один superadmin; 73-байтовый пароль → 422 не 500.

### A8. S-05 — AI endpoints
- Next route handlers: проверка сессии через backend `/auth/me` с cookie; 401 без сессии; лимит размера body; простой rate limit per user (in-memory + Redis при наличии); отключение при отсутствии `AI_GATEWAY_API_KEY` → 503 с понятным сообщением.
- Тест (vitest или simple node test): без cookie → 401.

### A9. S-06 — SSP auth/IP
- `supply_partners`: статус `auth_required` явный; пустой hash ⇒ партнёр не может быть `testing/active` (422); `revoke_token` переводит в `paused` и ставит `token_revoked_at`; ротация атомарна.
- Go `partner_auth.go`: партнёр без hash → 403 всегда; XFF учитывается только если `remoteAddr ∈ TRUSTED_PROXIES`; невалидный whitelist → fail-closed (no-bid + метрика).
- `ParseIPWhitelist`: возвращает ошибку, backend валидирует CIDR при сохранении.
- Compose: порты только на `127.0.0.1` (публикует Caddy).
- Тесты Go: подставной XFF от недоверенного адреса игнорируется; партнёр без hash → 403.

**Коммит и push после A.**

---

## Очередь B — сквозной programmatic-контур

### B1. R-01 — SSP URL ведёт в Go
- Env `PUBLIC_BIDDER_BASE_URL`; backend отдаёт его в `/settings/public`; frontend `supply/page.tsx` копирует `${PUBLIC_BIDDER_BASE_URL}/rtb/bid/{key}`; runbook/preprod/load используют его же.
- `RTB_PYTHON_SERVING=false` в production обязателен (A7).

### B2. E-01 — единая семантика spend и inventory dimensions
- Договор: **spend = clearing price billable win**. `_log_event` для win пишет `clear_price`; impression/click получают `publisher/tagid/bundle/domain` из bidmeta (Go пишет их в meta).
- `stats.py:report`, `supply_partners._METRIC_COLS`, `convergence_check.py`: расход из `event_type='win'`.
- Тест реальных handlers: bid→win→imp→click → summary/report/SSP card/convergence сходятся, dimensions не «—».

### B3. E-02 — атомарность win/impression
- `win_notice`: одна DB-транзакция: bill_win + INSERT bid_events + UPSERT stats_hourly; dedup — `bid_events(bid_id,event_type)` UNIQUE, Redis `mark_once` только как fast-path **после** коммита.
- impression/click/view/video: INSERT с `ON CONFLICT DO NOTHING` + условный bump в одной транзакции; `mark_once` после.
- Тесты failure-injection после каждого шага.

### B4. E-03 — надёжный consumer
- Inbox: `bid_events.stream_id UNIQUE` (или отдельная таблица `event_inbox`); INSERT `ON CONFLICT DO NOTHING`, агрегаты считаются только по вставленным строкам.
- После ошибки — повторное чтение `0` (pending) до успеха; `XAUTOCLAIM` чужих pending старше 60 с; bootstrap группы с `0`, `_ensure_group` в retry-loop.
- Метрики: `events_consumer_pending`, `stream_length`, `lag`; alert при lag/trim.
- Тесты: crash before/after commit, новый HOSTNAME, poison event.

### B5. E-04 — сквозной test-флаг
- `is_test` из bidmeta проходит в click/view/video/pixel conversions/ORD export/ML; test → отдельная таблица/флаг, не влияет на hourly и деньги.
- `/simulator/run`: вызывает Go по HTTP с `test=1`, RUB; в UI надпись «через production Go».
- Тест: полный test funnel → нулевые изменения баланса, hourly, conversions.

### B6. E-05 — pixel routing и ingestion
- `PUBLIC_API_BASE_URL` в скрипте; endpoint `/api/pixel/collect`; лимит body 16 КБ до парсинга; схема Pydantic; 204 на мусор; rate limit до DB; ownership clid ↔ pixel owner; test exclusion.

### B7. R-02 — bounded stale feed и durable bookkeeping
- Go: `FEED_MAX_AGE` (default 120 с) → no-bid + метрика `bidder_feed_stale`; `/healthz` → 503 при stale feed или Redis down; `FeedReload` возвращает реальный результат.
- Ошибка SET bidmeta/XADD → no-bid (не отправлять оплачиваемый bid без bookkeeping).

### B8. R-03 — паритет настроек UI ↔ Go
- **CPC**: цена bid = `bid_cpc × pCTR × 1000`, резерв = ожидаемая стоимость; списание по клику (`bill_click`), win не списывает.
- **Pacing even**: per-campaign allowance по минутам бизнес-дня (Lua `pacing:<camp>:<date>`), throttle вероятностный.
- **Валюта**: feed отдаёт `currency` и `fx_rate`; Go матчится с `cur`/`bidfloorcur`, конвертирует; ответ в валюте запроса.
- **Optimizer/scoring/clean_traffic/prefilter**: feed переносит `blocked_domains/publishers/tagids`, `scoring_min`, `clean_traffic_only`; Go применяет в `matchCandidate`; datacenter/bot список — в Go через shared Redis SET.
- **pCTR**: лёгкая модель (логистическая, коэффициенты из feed) в Go или fallback на CTR кампании.
- Parity matrix `docs/audit/parity_matrix.md` — исполняемая через тесты Go: изменение настройки → изменение BidResponse.

### B9. R-04 — OpenRTB совместимость
- `bidfloorcur`, `deal.bidfloor/bidfloorcur`, `private_auction`, `at`; `seat_id` из onboarding; native assets по `request.assets` (id/required/len), video MIME/protocol/duration; неподдерживаемое → no-bid.
- Contract fixtures в `bidder-go/testdata/openrtb/*.json`.

### B10. R-05 — adm/VAST/escaping/viewability
- URL builder с `url.QueryEscape`, HTML/XML/JS escaping, allowlist схем; VAST через `encoding/xml`; duration `HH:MM:SS`; трекеры для каждого формата; viewability-beacon (IntersectionObserver ≥50%/1 с) для banner/native; video start — отдельная метрика, не viewable.

### B11. R-06 — frequency/audience
- In-flight frequency reservation в Lua (`freq:<user>:<camp>` INCR на bid, DECR на loss/expire); per-member expiry в Redis (ZSET score = expires_at); goal-segment hook; `ops/redis_rebuild_audiences.py`.

### B12. S-02 + S-03 — ownership и scopes
- Helper `assert_owned(pool, table, id, user_scope)` для всех ссылочных полей в create/patch.
- Единая `rbac.scope_for(user)` → SQL-фрагмент для list/detail/update/billing/stats; admin/finance — платформенный scope; viewer — advertiser boundary во всех GET.
- Матрица тестов роль × endpoint × операция.

### B13. L-02 + L-03 — versioned erid и acceptance
- `ord_registrations` (creative_id, version, erid, status, provider, payload_hash, registered_at); `bid_events.creative_version`; месячный экспорт по (creative_id, version).
- Юридически значимые поля → `_CONTENT_FIELDS`; смена → новая версия и re-moderation.
- Ozon: per-row acceptance, `pg_advisory_lock` в экспорте, `partial` статус, корректирующий повтор.
- `[V]` реальный sandbox smoke.

### B14. F-02 — единые definitions отчётности
- `stats_math.py`: gross/net/markup/fx применяются одинаково в summary/timeseries/breakdown/report/share/CSV; totals — отдельный агрегирующий запрос; conversions в raw report; лимиты интервалов.

**Коммит и push после B.**

---

## Очередь C — эксплуатация и приёмка

### C1. O-01 — production Compose
- Удалить worker/beat/flower и `init.sql` mount; добавить `cron` (alpine + curl + crond с расписанием `/cron/*`); Caddy в Compose; порты на 127.0.0.1; `.env.example` только для Compose-топологии, отдельный `.env.external.example`; `EVENT_BASE_URL`/`PUBLIC_*` обязательны.
- `docker compose config` в CI.

### C2. O-02 — backup/restore
- Единый артефакт `backup-<ts>.tar` (pg dump custom format файл, redis rdb, uploads tar, manifest.json с sha256); restore принимает только его, сравнивает checksum, использует `--jobs` с файлом; SQL verify по `transactions`; проверка `schema_migrations.filename`. Тест: shell-тест на локальном PG (backup → drop → restore → сверка балансов и count событий).

### C3. O-03 + O-04 — preflight и load честные
- `preprod_check.sh`: fail-fast chain, правильные дефолты/порты, JSON-парсинг `candidates`, HTTP status, FAIL/SKIP/NOT TESTED, CSP report-only допустим, 413 через файл; `--apply` без наката (только проверки), накат — runbook.
- `rtb_load.py`: open-loop генератор (asyncio scheduled), учёт timeout как нарушений, gate по error-rate/achieved QPS/bid-rate/валидности BidResponse; отчёт latency/throughput/учёт; exit≠0 на 403-only.
- Тесты: bash-тесты с подставными ответами; MockTransport 403 → FAIL.

### C4. O-05 — readiness и алерты
- `/health` (liveness) vs `/ready` (PG ping, Redis ping, consumer lag < N); Go `/healthz` 503 при stale/Redis down.
- `alerts.yml`: согласованные labels (`on() group_left`), `absent()` для cron/consumer, disk/memory через node_exporter; `promtool test rules` в CI с realistic labels.
- Audit middleware исключает `/pixel/collect`.

### C5. O-06 — партиции
- Runbook `docs/runbooks/partition_upgrade.md`: pre-check DEFAULT, advisory lock, копирование батчами, dual-write окно, cutover, проверка count; скрипт `scripts/partition_repair.py` (перенос строк DEFAULT в целевые партиции).
- `[V]` репетиция на копии данных.

### C6. O-07 + F-01 — hardening и UX
- Pinned `requirements.lock`, non-root в Dockerfiles, `Cache-Control: no-store` для авторизованных ответов, lock на создание пула, graceful shutdown.
- AppShell: состояния loading/403/503/blocked с retry; drawer с focus trap/Escape/restore; один `main`; локализация ошибок Pydantic.

**Коммит и push после C. Затем — раздел 15 аудита на VPS.**

---

## Прогресс

Отмечается по мере закрытия: `[x]` — код + тест + CI зелёный; `[V]` — требует VPS.

- [x] A1 M-01/M-02 — `b6333ad`
- [x] A2 M-04 — `1f21bc0`
- [x] A3 M-03 — `bc60466`
- [x] A4 M-05/M-06 — `b075037`
- [x] A5 L-01 — `507fe14`
- [x] A6 S-01 — `5709181`+`8f9caa0`
- [x] A7 S-04 — `5b3ca1d`
- [x] A8 S-05 — `frontend/lib/server-auth.ts` + `frontend/lib/ai-guard.ts`; vitest в CI (`frontend/tests/ai-api.test.ts`)
- [x] A9 S-06 — миграция 039 (token_prev_hash/valid_until, token_revoked_at); revoke → paused; CIDR-валидация 422; Go: fail-closed без хэша, TRUSTED_PROXIES для XFF, whitelist_invalid; метрики token_not_issued/ip_denied/whitelist_invalid
- [x] B1 R-01 — `GET /settings/public` (PUBLIC_BIDDER_BASE_URL → fallback PUBLIC_BASE_URL); UI /supply копирует URL Go-биддера, без переменной — предупреждение; startup warning
- [x] B2 E-01 — inventory dims (domain/bundle/publisher/tagid/os/browser/geo) наследуются win/imp/click/view из bidmeta через _event_dims; единый контракт spend = win.clear_price (9cb556c)
- [x] B3 E-02 — таблица `bid_event_once` (040) + `_once_txn`: заявка, bid_events и stats_hourly в одной транзакции; Redis `once:*` только после коммита; bill_win + win-событие + stats — одна транзакция; retention в cron
- [x] B4 E-03 — `rtb_events_inbox` (041) ON CONFLICT DO NOTHING RETURNING в одной транзакции с bid_events/stats_hourly/cursor; после ошибки чтение `0`; XAUTOCLAIM чужих pending > 60 с; poison → `rtb:events:dead` после 5 доставок; bootstrap с `0`/курсора в retry-loop; gauges stream_length/pending/lag + 5 алертов + runbook
- [x] B5 E-04 — test=1 сквозь всю воронку: Python bid path не резервирует и не считает bids; click/view/video журнал-only при is_test; conversions.is_test (042) наследуется через clid и отфильтрован в optimizer/scoring/pixel-витринах; симулятор шлёт test=1 через Go (fallback Python) и показывает режим в UI
- [x] B6 E-05 — pixel: канонический PUBLIC_API_BASE_URL (или PUBLIC_BASE_URL+/api) в скрипте и коде вставки (/settings/public), 404 без него; collect: Content-Length/16 КБ → Pydantic-схема → rate limit → БД, 204 на любой мусор; атрибуция clid только по кампаниям владельца пикселя (+ поддерево agency) и в окне PIXEL_ATTRIBUTION_WINDOW_DAYS; test exclusion из B5
- [ ] B7 R-02
- [ ] B8 R-03
- [ ] B9 R-04
- [ ] B10 R-05
- [ ] B11 R-06
- [ ] B12 S-02/S-03
- [ ] B13 L-02/L-03
- [ ] B14 F-02
- [ ] C1 O-01
- [ ] C2 O-02
- [ ] C3 O-03/O-04
- [ ] C4 O-05
- [ ] C5 O-06
- [ ] C6 O-07/F-01
