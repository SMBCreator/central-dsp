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
- **CPC** ✅: цена bid = `min(bid_price, max_bid) × CTR × 1000` (CTR — pCTR-модель, без неё `CPC_FALLBACK_CTR` = 0.002; общие helper'ы `ml.pctr.expected_ctr/cpc_to_cpm` ↔ `pctr.ExpectedCTR/CPCToCPM`), резерв = ожидаемая стоимость; win — только журнал/wins (без clear_price), первый click списывает `bid_cpc` через `bill_win` (идемпотентно по bid_id), повторные — нет. Оба биддера пишут `bid_type`/`bid_cpc` в bidmeta. Договор расхода `stats_math.SPEND_FROM_EVENTS_SQL` = win/1000 + click; optimizer и scoring переведены на него (в scoring формула суммировала clear_price по impression — всегда 0).
- **Pacing even** ✅ (da933ac +): общий helper `business_day.even_pacing_budget` (доля бизнес-дня МСК + 10% burst) в rtb.py; Go — `redisx.EvenPacingBudget` подменяет дневной бюджет в Lua-резерве. Детерминированная формула вместо вероятностного throttle: обе стороны считают одинаково без общего состояния.
- **Валюта** ✅: внутри всё в RUB; валюта ответа — `fx.choose_currency(req.cur, валюта партнёра)` (партнёрская, если SSP принимает, иначе RUB, иначе no-bid — раньше Python отдавал `cur` из анкеты при рублёвых ценах, Go отказывал всем не-RUB). Один курс ЦБ × (1 + `fx_markup_percent` владельца) на бид: `imp.bidfloor` (`bidfloorcur` → `cur[0]` → валюта партнёра) и `bid_floor` партнёра → RUB, цена → валюта ответа, `${AUCTION_PRICE}` → RUB в `win()` тем же курсом из bidmeta (`cur`, `fx_rate`). Нет курса → кандидат пропускается (`*_nobid_fx_rate_missing_total`), не ставка «как будто в рублях». Feed: `partners[].currency`, `fx_rates{}`, `candidates[].fx_markup_percent`; Go `internal/fx` — порт `fx.py`. Тесты `TestFX_*` ↔ `test_fx_bidding.py` с одинаковыми числами.
- **Optimizer/scoring/clean_traffic/prefilter** ✅ (df2594f, f3ef583): feed переносит `optimizer_blocks[]`, `scoring_blocks[]`, `dirty_domains[]`, `clean_traffic_only`; Go применяет в `matchCandidate`; предфильтр — порт `backend/prefilter.py` в `internal/prefilter` (THROTTLE_*, shared Redis SET `prefilter:dc_subnets`).
- **pCTR** ✅: feed отдаёт активную модель `pctr_model{global_ctr, segments[]}`; Go — порт лог-odds `predict` в `internal/pctr`, множитель clamp(0.7, 1.3) только для traffic/conversions на CPM; `bid_events.pctr` через stream.
- Parity matrix `docs/audit/parity_matrix.md` — исполняемая через тесты Go: изменение настройки → изменение BidResponse.

### B9. R-04 — OpenRTB совместимость
- `bidfloorcur`, `deal.bidfloor/bidfloorcur`, `private_auction`, `at`; `seat_id` из onboarding; native assets по `request.assets` (id/required/len), video MIME/protocol/duration; неподдерживаемое → no-bid.
- Contract fixtures в `bidder-go/testdata/openrtb/*.json`.

### B10. R-05 — adm/VAST/escaping/viewability
- URL builder с `url.QueryEscape`, HTML/XML/JS escaping, allowlist схем; VAST через `encoding/xml`; duration `HH:MM:SS`; трекеры для каждого формата; viewability-beacon (IntersectionObserver ≥50%/1 с) для banner/native; video start — отдельная метрика, не viewable.

### B11. R-06 — frequency/audience
Решение 2026-09-10 (уточнено по документу «WIN, IMP, BURL и frequency cap»): win — не показ, а **временная бронь** лимита; показ подтверждает imp. Формула гейта: `confirmed_impressions + active_reservations < cap`, где `active_reservations` = биды в полёте + выигранные, ждущие imp.
- Статусная машина бида в freq cap: `bid → inflight → won_reserved → confirmed (imp)` / `lost` / `expired_no_imp`. Один ключ `freqinf:{camp}:{device}` (ZSET bid_id → deadline): на bid `ZADD deadline=now+reserve_ttl`; на win score переставляется на `now+WIN_IMP_TTL` (бронь), **без** INCR; на imp `ZREM` + `INCR freq:{camp}:{Nh}:{device}` (подтверждённый показ, TTL окна); на loss/expired/aborted `ZREM`. Гейт: `ZREMRANGEBYSCORE` просроченных → `ZCARD` = active_reservations; `GET freq:*` = confirmed.
- `bidmeta.fdev`/`fcaps` — единственный источник device/окон для win/imp/loss (Go `persistBidMeta` ↔ Python bid path); `bidfreq:{bid}` и bump на imp удалены; мёртвые `check_frequency_cap`/`bump_frequency` удалены.
- `WIN_IMP_TTL`: env (секунды, дефолт 120, одно значение для backend и bidder-go); runbook-SQL по `bid_events` для p95/p99 `win→imp`; per-format/per-partner — когда `rtb_imp_late_after_reservation_total` покажет, что нужно.
- Метрика `rtb_freq_win_without_imp_total` (Lua-prune возвращает число вычищенных выигранных) + алерт на аномальную долю (сломан пиксель SSP). Парная метрика `rtb_imp_late_after_reservation_total` (`luaConfirmImp` возвращает 3 = засчитано без брони): растёт вместе с первой → пиксель медленный, поднять TTL; иначе — сломанный.
- Per-SSP флаг `supply_partners.freq_cap_on_win` (default false; имя — про основание капа, не про delivery: отчётные wins/impressions не смешивает) — только для бирж без клиентского пикселя: INCR сразу на win (узкий случай, на уровне интеграции, не глобально).
- Отчётность: `wins` / `impressions` / `viewable_imps` / `spend` уже раздельны (stats_hourly, B2/B10) — проверить подписи в UI (win не называть «показом»); «reserved wins» — только диагностический gauge, не в отчёты клиенту.
- Audiences: `aud:` SET с TTL на весь ключ → `audz:` ZSET score = expires_at (per-member expiry), проверка `ZSCORE > now` в Python и Go, `ZREMRANGEBYSCORE` при добавлении; goal-сегменты (`rule_type='goal'`) подключить в `_match_goals`; cron `/cron/audience-sync` (чистка просроченных PG + Redis, автодосев пустого `audz:` из PG) + `backend/scripts/redis_rebuild_audiences.py` (dry-run/--apply) + runbook.
- Тесты: Go `redisx` (гейт с in-flight, win→бронь, imp→confirm, expire без imp, идемпотентность imp), Python `test_freq_caps.py` переписать под новую семантику, `test_reservation_lifecycle.py`, контракт Lua байт-в-байт; parity_matrix.
- Проверить связанные модули до правок: `bill_win`/`_once_txn` (B3) на imp — INCR только после коммита; `bid_event_once` даёт идемпотентность imp; Go sweeper `pending:bids` не трогает `freqinf` (просрочка через score); PG-fallback без bidmeta → показ не считается (безопасная сторона, лог).

### B15. R-07 (новый, из решения 2026-09-10) — billing по `burl`
- Сейчас spend списывается на `nurl` (`bill_win` + `settle_win`, контракт B2 «spend = win.clear_price»); `burl` в BidResponse не отправляем. По OpenRTB `nurl` — win notice, не billable impression.
- Добавить `burl` в BidResponse (Go + Python) с `${AUCTION_PRICE}`; per-SSP режим биллинга `partners.billing_event` ∈ {`nurl` (как сейчас, default до сверки), `burl`}; при `burl`: на nurl — только `wins` + бронь cap, деньги (`bill_win`, `settle_win`, `spend`) — на burl; `win_without_billing` по TTL; сверка с отчётами SSP.
- Затрагивает B2/B3 транзакционные контракты, `bid_event_once`, `rtb_events_inbox`, Go `Win`/новый `Burl` handler, stats/UI (метрика «Billable impressions»). Делать после B11 и B14 отдельным коммитом с миграцией; до включения на реальной SSP — `[V]` документация биржи о семантике burl.

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
Решения 2026-09-10: spend в БД — net RUB; в API — net × множитель получателя, одна точка применения (`present_spend`); наценка — товарная цепочка (каждый уровень добавляет к своей цене); конверсии — слой атрибуции со статусами, без распределения по догадке; `Not set`/`Other` строки, totals без LIMIT. Разбито на три коммита:
- [x] **B14a** `stats_math`: `present_spend/derive/clamp_*/parse_date_range`, `NOT_SET_LABEL/OTHER_LABEL`; markup во всех представлениях (summary/timeseries/domains/breakdown/report/share public); totals отдельным запросом, `meta.truncated` + строка «Прочее» (sum(rows)==totals); NULL-измерение → «Не указано»; конверсии в raw-отчёте из `conversions` для доступных измерений, иначе `null` + `meta.conversions_available=false`; hourly `losses=null`; 422 на `date_from>date_to`; clamp hours/limit везде; scoring/supply_partners через `derive`. Тесты `test_stats_presentation.py`; DB-тест `test_report_supply_dims_db` обновлён. UI analytics: строки other/not_set, подпись truncated. `CONTRACTS.md §5`.
- [ ] **B14b** миграция 044: `share_links`/`guest_access` + `created_by_user_id`, `markup_extra_pct`, `markup_mult_snapshot`, `markup_mode`, `revoked_at/by`; snapshot = chain_mult(creator) × (1+extra); `extra>0` только superadmin/admin/agency (`rbac.MARKUP_ADD_ROLES`); backfill = текущая цена владельца; `public_stats`/viewer читают snapshot; admin отзывает чужие; блокировка владельца → отзыв ссылок и гостей; UI `/access` (поле наценки, превью «получатель увидит ×N»).
- [ ] **B14c** миграция 045: `conversion_attributions` 1:1 (`attribution_status` attributed|out_of_window|unattributed, model last_click, все измерения события, `external_conversion_id`, `raw_payload`); backfill; отчёт — conversions по всем измерениям, на уровне рекламодателя строки Unattributed/Out of window; `coverage[dim]`, `<0.8` → `confidence=low`, запрет авто-stop/blacklist по conversion-KPI (`stats_math.conversion_kpi_trustworthy`); `loss_reason` только в funnel-режиме; метрики `conversions_unattributed_total{status}`, `conversion_attribution_coverage{dimension}` + алерт; UI бейдж покрытия, CPA не считается для Not set/Unattributed.

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
- [x] B7 R-02 — Go: `FEED_MAX_AGE` (120s) → no-bid `feed_stale` + 503; до первой загрузки `feed_not_loaded` 503; `/healthz` 503 при stale/not loaded (compose healthcheck выводит из ротации); `/internal/feed/reload` возвращает реальный результат (502 + error); bidmeta/XADD обязательны — ошибка → no-bid, резерв возвращён, `bidder_bookkeeping_errors_total`; gauges feed_stale/max_age/refresh_errors; алерты FeedAging/FeedStale/BookkeepingErrors + runbook; backend `request_feed_reload_detail` — неудачный рефреш показывается первой строкой в автопроверке SSP
- [x] B8 R-03
- [x] B9 R-04 — `rtb_compat.py` (эталон) ↔ `bidder-go/internal/rtb/openrtb.go`: PMP `private_auction` → только сделки, `deal.bidfloor/bidfloorcur/at` заменяют floor/тип аукциона; `seatbid.seat` = `seat_id` партнёра (feed); native assets строго по `imp.native.request` (id/required/len, строкой или объектом), обязательный без данных → отказ; video MIME/VAST-версия/длительность против `mimes/protocols/min-maxduration`; VAST `Duration` HH:MM:SS и реальный `MediaFile type`; несовместимо → `*_openrtb_incompat_total{reason}`, не невалидная ставка. Контрактные фикстуры `bidder-go/testdata/openrtb/*.json` (10) гоняются Go и Python; parity_matrix обновлена. `[V]` реальные запросы SSP — при онбординге
- [x] B10 R-05 — `rtb_compat.py` (`safe_url/adm_incompat/substitute_click_macros/js_string_escape/inject_vast_trackers`) ↔ `bidder-go/internal/rtb/adm.go` + `vast.go`: все URL креатива — только http(s) без кавычек/скобок/пробелов, иначе кандидат пропущен (`adm_url_invalid`); экранирование по контексту (атрибут / JS-строка / CDATA); `/api/rtb/click` без `&r=` (open redirect закрыт — цель только `click_url` креатива, `beacon=1` без перехода, `cdsp_clid` до `#fragment`); HTML5-макросы `${CLICK_URL}`/`%%CLICK_URL_*%%`, без макроса — capture click-beacon; Go получил viewability для banner (IO ≥50 %/1 с) и native `eventtrackers` event=2 (раньше `viewable_imps`=0 на Go-трафике); пользовательский VAST разбирается (`encoding/xml` / expat) и получает Impression/TrackingEvents/ClickTracking/erid во все InLine/Wrapper (в т.ч. Wrapper без Creatives, pod), невалидный → `adm_vast_malformed`; video `start` больше не viewable; Pydantic-валидаторы URL/VAST/длины в `CreativeIn/CreativePatch`. Тесты: Go `adm_r05_test.go` (14), Python `test_adm_safety.py` (46); parity_matrix дополнена. `[V]` SSP исполняет `<script>` в banner adm — при онбординге
- [x] B11 R-06 — frequency cap: win = бронь (`freqinf` `w:{bid}`, WIN_IMP_TTL 120 с), imp = подтверждение (`luaConfirmImp`), гейт confirmed + active reservations < cap; `bidmeta.fdev/fcaps/fwin`, `bidfreq` удалён; `supply_partners.freq_cap_on_win` (миграция 043; ранняя редакция `win_counts_as_imp` переименовывается в той же миграции); `WIN_IMP_TTL` через env (backend + bidder-go); метрики `rtb_freq_win_without_imp_total` + `rtb_imp_late_after_reservation_total` (медленный vs сломанный пиксель) + runbook `freq-win-without-imp`; audiences `audz:` ZSET per-member expiry (Python + Go), goal-сегменты через `_match_goals`, cron `/cron/audience-sync` (vercel.json, DEPLOYMENT.md) + `scripts/redis_rebuild_audiences.py`. Тесты: Go `redisx/freqcap_test.go` 11 + `audience_test.go` +1, Python `test_freq_caps.py` 12 (переписан), `test_audiences.py` +6, `test_reservation_lifecycle.py` расширен; Lua байт-в-байт. Не восстанавливается из БД после потери Redis: `freq:*` (нет per-device журнала) — зафиксировано в runbook redis-down.
- [ ] B12 S-02/S-03
- [ ] B13 L-02/L-03
- [~] B14 F-02 — B14a done (2026-09-10); B14b, B14c в работе
- [ ] B15 R-07 — billing по burl (новый пункт, после B11/B14)
- [ ] C1 O-01
- [ ] C2 O-02
- [ ] C3 O-03/O-04
- [ ] C4 O-05
- [ ] C5 O-06
- [ ] C6 O-07/F-01
