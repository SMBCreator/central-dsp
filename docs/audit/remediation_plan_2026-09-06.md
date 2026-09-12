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
- Production запрещает `RTB_ALLOW_UNSIGNED_EVENTS=1` и отключённые gates (флаг `RTB_PYTHON_SERVING` удалён вместе с Python-обслуживанием bid-запросов — C1d).
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
- Python-обслуживание bid-запросов удалено (C1d): SSP-URL всегда ведёт в Go-биддер, отдельного флага `RTB_PYTHON_SERVING` больше нет.

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
- Решения 2026-09-10: существующие партнёры остаются на `win` до сверки, новые — `burl`; `wins`/`impressions`/`billable_imps` — три разных числа, `ecpm` по billable; late burl после `win_expired` списывается один раз и помечается `late`; burl обрабатывает backend (Python), Go только отдаёт `burl` и делит Lua `luaSettleWin`.

### B12. S-02 + S-03 — ownership и scopes
- Helper `assert_owned(pool, table, id, user_scope)` для всех ссылочных полей в create/patch.
- Единая `rbac.scope_for(user)` → SQL-фрагмент для list/detail/update/billing/stats; admin/finance — платформенный scope; viewer — advertiser boundary во всех GET.
- Матрица тестов роль × endpoint × операция.
- [x] **B12a** (2026-09-10) `rbac.Scope` + `scope_for`/`report_scope_for`, `assert_in_scope` (главный объект, 404) и `assert_owned_by`/`assert_refs_owned` (ссылочные поля — владелец главного объекта, не «в scope»); один предикат `Scope.where` в list/detail/update/delete: campaigns, advertisers, groups, line_items, creatives, scoring (профили, блок-листы, recompute), ord (контрагенты, договоры, регистрация), billing (deposit/adjust FOR UPDATE по scope; transactions/summary по владельцу рекламодателя). Дочерний объект принадлежит владельцу родителя (admin/agency создают клиенту). Цепочка: `goal.pixel_id == campaign.pixel_id` (422), `line_item.campaign_id == creative.campaign_id`, `parent_contract` того же владельца. viewer в CRUD — пусто. Спецификация `docs/audit/scope_spec.md`; тесты `tests/test_scope_matrix_db.py` (45 кейсов: platform list→detail→write, billing, agency-дерево, viewer, 16 чужих ссылок × {owner, admin}).
- [x] **B12b** (2026-09-10) `stats._resolve_scope` → `rbac.report_scope_for`; отчётная форма scope одним параметром `Scope.owner_ids_param` (`NULL` = вся платформа, `[]` = никого) и предикат `Scope.owner_in` `($1::uuid[] IS NULL OR c.user_id = ANY($1::uuid[]))` во всех 15 запросах stats + `conversion_attribution.owner_breakdown`; платформенные роли видят статистику всех владельцев (×1), agency — клиентов. Граница viewer `advertiser_id` во всех ручках: счётчики summary (креативы через кампании рекламодателя, `active_partners`=0), `/domains`, `/domain-quality`, `POST /stats/report` (кампании режутся по рекламодателю до `campaign_ids`/`group_id`; чужой id → `rows=[]`). `/scoring/report`: `group_id` через `assert_in_scope` (404), отчёт/профиль в кабинете владельца кампании/папки, а не запрашивающего. Спека `scope_spec.md` §1a; тесты `tests/test_report_scope_db.py` (8 кейсов: NULL/[]-семантика, 4 платформенные роли, agency/manager, viewer по 6 ручкам + гость без границы, scoring report admin/agency/чужой).

### B13. L-02 + L-03 — versioned erid, acceptance, акты (спека `docs/audit/ord_spec.md`)
Решения 2026-09-10: версии в самой `ord_creatives` (не отдельная таблица), без `bid_events.creative_version` и без правок Go — показ относится к версии, действовавшей на конец часа; юр. поля (контрагент/договор/ККТУ) после erid **заморожены** (`409 ord_locked`), другой заказчик = новый креатив; коррекция = повторный upsert тем же external ID (как в API Ozon); подача актов и статистики — по образцу ВК (кампания → креативы → факт → «Отправить в ОРД»).
- [x] **B13a** (2026-09-10) миграция 047: `ord_creatives.version/superseded_at`, `UNIQUE(creative_id, version)`, одна открытая версия; `creatives.ord_locked_at`; `ord_export_rows` + `rows_accepted/rejected`. `ord.service`: `supersede_current`, `external_creative_id=<id>:v<n>`, `external_statistic_id=<id>:v<n>:<period>`. `ord/ozon_mapping.py` + клиент на реальные пути `/api/external/{organisation,contract,creative,platform,v2/statistic}` (договор с внешними ID сторон и `externalParentId`), `OrdDataError` (4xx) → построчное отклонение, батчи по 100. Экспорт: LATERAL-версия на конец часа, advisory-lock (409), статусы `ok|partial|failed|empty`, `force=1` → `stats_correction`, показы без erid при 0 строк → `failed`; `GET /ord/creatives/{id}/versions`, `GET /ord/exports/{id}/rows`; метрика `ord_export_rows_rejected` + алерт OrdExportRowsRejected, runbook. Тесты `test_ord_ozon_mapping.py`, `test_ord_versions_db.py`.
- [x] **B13b** (2026-09-11) акты (`ord_spec.md §6`): миграция 048 `ord_invoices`/`ord_invoice_items` (строка = креатив × версия erid × календарный месяц, `external_statistic_id` тот же, что у крона → отправка акта = коррекция, одна строка — один акт); `ord/invoices.py`: `GET /ord/invoices/preview` (договор + произвольный период [+ кампании] → строки по месяцам, факт. показы/клики/расход по цене клиента `rbac.spend_multiplier`, `unit_cost` CPM/CPC, предупреждения «уже в акте»/«без erid»), `POST/PUT/DELETE /ord/invoices` (черновик, всё редактируемо, факт хранится рядом, 404/409/422), `POST /ord/invoices/{id}/submit` (`v2/invoice` upsert → `v3/invoice/creative/unlink+link` → `v2/statistic` по месяцам; статусы `ok|partial|failed`, построчно; повтор = коррекция); роли RD/RA/RR/ORS из `counterparties.role`; `ord_invoices` в `rbac._OWNED_TABLES`; крон берёт `viewsCountByInvoice/moneySpent` из принятого акта. Тесты `test_ord_ozon_mapping.py` (+7), `test_ord_invoices_db.py`. Открыто `[V]`: сверка с документацией Ozon/логикой ВК и Excel-шаблонами (пришлёт заказчик).
- [x] **B13c** ОРС-модель, часть 1 (`ord_spec.md §7`): `counterparties.is_self/rs_url`, `isOpc/isPp/rsUrl` в организации; регистрация сводной площадки `central-dsp` (SITE, URL из настроек, владелец = мы) перед экспортом статистики; UI карточки контрагента и настроек ОРД.
- [x] **B13d** (2026-09-11) цепочка на кампании (`ord_spec.md §7`): миграция 050 `campaigns.ord_mode (self|own_client|partner_client)/counterparty_id/initial_contract_id/executive_contract_id/ord_confirmed_at` + бэкфилл из креативов; `ord/chain.py` (`validate_chain` — чистые проверки с кодами `*_missing/*_mismatch/*_unregistered`, `load_chain`, `confirm_chain`, `registration_contract`, `assert_not_locked`); `POST /campaigns/ord-chain/preview`, `GET/POST /campaigns/{id}/ord-chain[/confirm]`; `ord.service.resolve_contract` → договор кампании (self → исполнительский, иначе изначальный), контрагент креатива = снапшот рекламодателя кампании; регистрация erid без подтверждения / изменение цепочки после → `422 ord_chain_required` / `409 ord_locked`; блокер запуска `ord_chain` (только РФ-гео). UI: шаг 1 «Данные ОРД» в мастере `/campaigns/new` (`components/ord-chain-setup.tsx`: радио «кого рекламируете», рекламодатель + договоры с inline-добавлением, «Проверить данные» → сводка цепочки «Всё верно / Изменить»), read-only блок в карточке кампании. Тесты `test_ord_chain.py`, `test_ord_chain_db.py`, `test_launch_gate.py`.
- [x] **B13e** акты по ОРС-модели (2026-09-11): миграция 051 `vat_rate/amount_net/amount_gross`, `ord_invoice_contracts` + `ord_invoice_items.contract_id` (разбивка по изначальным договорам, явная или пропорциональная), preview/акт по исполнительскому договору + `statistics_amount_*` и `warnings.amount_diff` (> 1 %), `v2/invoice.contracts[]` и `link` по изначальным договорам — `ord_spec.md §7 B13e`.
- [x] **B13f** UI `/ord` вкладка «Акты» (2026-09-11): `components/ord-invoices.tsx` — месяц → таблица по исполнительским договорам (факт по preview, акт/статус) → модалка акта (номер, период, сумма с НДС, ставка НДС, дата, авто «без НДС», расхождение с фактом > 1 %, разбивка по изначальным договорам авто/вручную, строки креатив × версия erid × месяц с построчным статусом ОРД) → «Сохранить черновик» / «Отправить в ОРД» / «Отправить повторно» / удалить черновик; «Отправить все в ОРД (N)» для черновиков месяца; список всех актов. Модалка «Версии erid» на вкладке «Креативы / erid» (`GET /ord/creatives/{id}/versions`). `lib/api.ts::describeApiError` — единый текст на `409 ord_locked` (креативы, акты). Mock-API: preview/акты/submit/versions для демо.
- [x] **B13g** ошибки ЕРИР (2026-09-11): миграция 052 `ord_notifications` + статус `erir_error` у контрагентов/договоров/версий erid/актов; `ord/notifications.py` — крон `/cron/ord-notifications` (`error_list` постранично по курсору `platform_settings.ord_notification_cursor`), объект → `erir_error` с текстом, `ord_external_id` сохраняется; автозакрытие при успешной повторной регистрации (`resolve_for`); `GET /ord/notifications` (scope владельца), `POST /ord/notifications/sync`; UI `/ord` блок «Ошибки ЕРИР: N» + статусы в таблицах; метрики `ord_notifications_*`, алерты `OrdErirErrorsUnresolved` (warning) / `OrdErirErrorsStale` (critical >24 ч) / `OrdNotificationsLag`; runbook `#ord-notifications` — `ord_spec.md §7 B13g`.
- `[V]` sandbox Ozon с реальным ключом: `mediaData.file` (нужна ли предзагрузка `/api/external/file`), `advObjectType` для video, регистрация площадки, формат ответа `creative.marker`.

### B14. F-02 — единые definitions отчётности
Решения 2026-09-10: spend в БД — net RUB; в API — net × множитель получателя, одна точка применения (`present_spend`); наценка — товарная цепочка (каждый уровень добавляет к своей цене); конверсии — слой атрибуции со статусами, без распределения по догадке; `Not set`/`Other` строки, totals без LIMIT. Разбито на три коммита:
- [x] **B14a** `stats_math`: `present_spend/derive/clamp_*/parse_date_range`, `NOT_SET_LABEL/OTHER_LABEL`; markup во всех представлениях (summary/timeseries/domains/breakdown/report/share public); totals отдельным запросом, `meta.truncated` + строка «Прочее» (sum(rows)==totals); NULL-измерение → «Не указано»; конверсии в raw-отчёте из `conversions` для доступных измерений, иначе `null` + `meta.conversions_available=false`; hourly `losses=null`; 422 на `date_from>date_to`; clamp hours/limit везде; scoring/supply_partners через `derive`. Тесты `test_stats_presentation.py`; DB-тест `test_report_supply_dims_db` обновлён. UI analytics: строки other/not_set, подпись truncated. `CONTRACTS.md §5`.
- [x] **B14b** (2026-09-10) миграция 044: `share_links`/`guest_access` + `created_by_user_id`, `markup_extra_pct`, `markup_mult_snapshot`, `markup_mode`, `revoked_at/by`; snapshot = chain_mult(creator) × (1+extra); `extra>0` только superadmin/admin/agency (`rbac.MARKUP_ADD_ROLES`); backfill = текущая цена владельца; `public_stats`/viewer читают snapshot; admin отзывает чужие; блокировка владельца → отзыв ссылок и гостей; UI `/access` (поле наценки, превью «получатель увидит ×N»).
- [x] **B14c** (2026-09-10) миграция 045: `conversion_attributions` 1:1 (`attribution_status` attributed|out_of_window|unattributed, model last_click, все измерения события, `external_conversion_id`, `raw_payload`); backfill; отчёт — conversions по всем измерениям, на уровне рекламодателя строки Unattributed/Out of window; `coverage[dim]`, `<0.8` → `confidence=low`, запрет авто-stop/blacklist по conversion-KPI (`stats_math.conversion_kpi_trustworthy`); `loss_reason` только в funnel-режиме; метрики `conversions_unattributed_total{status}`, `conversion_attribution_coverage{dimension}` + алерт; UI бейдж покрытия, CPA не считается для Not set/Unattributed.

**Коммит и push после B.**

---

## Очередь C — эксплуатация и приёмка

### C1. O-01 — production Compose (решения 2026-09-11)
Перепроверка перед стартом: Celery в проекте отсутствует (worker/beat/flower в compose — мёртвые сервисы); отдельного
«Python bidder» нет — `bidder/` был legacy **Java** RTB4FREE-форк (удалён C1c), а Python bid-ветку (`POST /rtb/bid/{key}`
за флагом `RTB_PYTHON_SERVING`) удалил C1d: в `backend/routers/rtb.py` остались только событийные роуты.
`process_bid_request`/`_build_adm`/`rtb_compat.py` — исполняемый оракул паритета для 43 pytest-файлов и Go-тестов
(`parity_matrix.md`). Событийные роуты `/win /imp /click /burl /view /vevent` — production-биллинг, не биддер.

- **C1a — split Compose + Caddy + Prometheus.** `compose.yml` (dev: postgres, redis, backend, bidder-go, frontend,
  scheduler; порты 127.0.0.1) + `compose.prod.yml` (override: Caddy 80/443 + TLS, внутренние порты закрыты,
  `restart: always`, лимиты логов) + `Caddyfile` (`DOMAIN`: `/api/*`→backend со strip, `/rtb/*`→bidder-go,
  `/uploads/*`→backend, остальное→frontend, `request_body max_size 60MB`; `RTB_DOMAIN` зарезервирован для внешних
  SSP, по умолчанию = `DOMAIN`, в первом релизе не включается). Удалить worker/beat/flower, `celery flower` из
  Dockerfile, `init.sql` mount, сервис legacy `bidder` (profile legacy) и его env. Prometheus **только** под
  `--profile monitoring`, порт `127.0.0.1:9091`, scrape backend `/metrics` + bidder-go `:8090/metrics`, `ops/prometheus/alerts.yml`
  подключён; Grafana — тем же профилем в C4. `PUBLIC_BASE_URL`/`PUBLIC_BIDDER_BASE_URL`/`CRON_SECRET` обязательны (`:?`),
  Go fail-fast в production на пустом/`localhost` `EVENT_BASE_URL`. `.env.example` только для Compose-топологии
  (PG/Redis внутри), `.env.external.example` для managed PG/Redis. CI job `compose`: `docker compose config` для
  dev и dev+prod с `.env.example`, `caddy validate`. Docs: `DEPLOYMENT.md`, `README.md`.
- **C1b — scheduler в PostgreSQL (без Celery, без vercel.json).** Миграция 053 `scheduled_jobs` (name PK, path,
  schedule cron UTC, enabled, timeout_s, last_run_at/status/error/duration_ms, next_run_at) + `scheduled_job_runs`
  (история, ретеншн 30 д), seed = текущие 13 задач `/cron/*`. Сервис `scheduler` из образа backend
  (`python -m scheduler`): тик 15 с, `croniter`, `pg_try_advisory_lock` на job (несколько реплик без дублей),
  пропущенные тики схлопываются в один запуск, вызов существующих `GET /api/cron/<path>` по `BACKEND_INTERNAL_URL`
  с `Bearer CRON_SECRET` — сами задачи и метрики `cron_job_runs_total`/алерт `CronJobFailing` не меняются.
  Admin API (superadmin): `GET /admin/scheduler/jobs`, `PATCH /admin/scheduler/jobs/{name}` (enabled, schedule),
  `POST /admin/scheduler/jobs/{name}/run`. Метрика `scheduler_last_tick_timestamp_seconds`, алерт `SchedulerDown`,
  runbook. `vercel.json` — только Vercel-preview, с production не синхронизируется (решение пользователя).
  *Реализация (2026-09-11), отличия от эскиза:* парсер cron — свой `backend/cronexpr.py` (5 полей, Vixie-семантика
  dom/dow, без новой зависимости `croniter`); взаимоисключение реплик — `FOR UPDATE SKIP LOCKED` + lease
  `locked_at`/2×timeout вместо advisory lock (lease переживает падение экземпляра); истории `scheduled_job_runs` нет —
  `last_*`/`runs_total`/`errors_total` в строке + счётчики Prometheus + логи (таблицу истории добавить, если
  понадобится аудит запусков); API — `/scheduler/jobs*` с `require_admin` (admin+superadmin, как у остальных
  сервисных ручек), не `/admin/...`; `timeout_seconds` тоже правится PATCH-ом.
- **C1c — `chore: remove deprecated Java RTB4FREE bidder`.** Удалить `bidder/` целиком (Dockerfile, pom, src, README),
  `docs/rtb4free-setup.md`, все ссылки в Compose/CI/ENV/документации/схемах. Критерий: `grep -ri rtb4free` пуст вне
  audit-документов.
- **C1d — `refactor: remove Python RTB serving path and retain Go parity reference`.** Удалить `POST /rtb/bid/{key}`,
  флаг `RTB_PYTHON_SERVING`, связанные startup checks/env/docs и код, нужный только для обслуживания bid-запросов
  Python-бэкендом (входной prefilter/throttle). `process_bid_request` и оракул паритета остаются без HTTP-маршрута;
  parity suite (43 файла) и Go contract-тесты переводятся на прямой вызов функции там, где ходили через роут.
  Критерий готовности: ни один production route не принимает bid request через Python; Go bidder — единственный
  auction endpoint; parity suite зелёный; `/win /imp /click /burl /view /vevent` работают без изменений.
  *Follow-up (не блокирует C1d):* parity-метрики симулятора `simulator_engine_mismatch_total` /
  `simulator_parity_failures_total` — считать в parity-прогоне (Go vs `process_bid_request` на одном BidRequest),
  а не в `/simulator/run`; добавить вместе с алертом в C4.
  *Guard (закрыт):* `backend/tests/test_simulator_python_reference_guard.py` фиксирует инвариант целиком —
  production падает на старте при `SIMULATOR_ALLOW_PYTHON_REFERENCE=true`; в production `engine=python_reference`
  = 404 независимо от флага; вне production reference только при явном truthy-флаге (иначе 403); Go недоступен →
  503 с `fallback_used=false`; ни одна комбинация env/флага не переключает Go-unavailable на Python reference.
- **C1e-tech — GitHub Actions: Node 20 deprecation (не блокирует C2).** CI печатает предупреждение о Node 20 для
  `actions/checkout@v4` и соседних actions. В рамках C1d actions не обновляются. Перед обновлением — отдельный audit:
  список используемых actions и текущих версий, Node-совместимость целевых версий, требуемые `permissions`,
  потенциальные breaking changes (inputs/outputs, поведение checkout/setup-*), план проверки CI (все джобы зелёные
  на ветке до merge). Обновление — отдельным маленьким commit после audit.

### C2. O-02 — backup/restore
- Единый артефакт `backup-<ts>.tar` (pg dump custom format файл, redis rdb, uploads tar, manifest.json с sha256); restore принимает только его, сравнивает checksum, использует `--jobs` с файлом; SQL verify по `transactions`; проверка `schema_migrations.filename`. Тест: shell-тест на локальном PG (backup → drop → restore → сверка балансов и count событий).

### C3. O-03 + O-04 — preflight и load честные
- `preprod_check.sh`: fail-fast chain, правильные дефолты/порты, JSON-парсинг `candidates`, HTTP status, FAIL/SKIP/NOT TESTED, CSP report-only допустим, 413 через файл; ~~`--apply` без наката (только проверки)~~ **решение 2026-09-12: `--apply` убран целиком** — флаг с таким именем, который ничего не применяет (или применяет, маскируясь под проверку), вводит в заблуждение; накат — только runbook §2.
- Решения 2026-09-12: 429 во время offered-load теста — отдельный gate → FAIL (не часть error budget 0.1 %), проверка лимитера — отдельный кейс `--expect-rate-limit`, не доказывающий capacity; NOT TESTED без FAIL → exit 3 (gate не доказан), exit 0 только когда все обязательные gates прошли; exit-коды 0/1/2/3 едины для `preprod_check.sh` / `rtb_load.py` / `convergence_check.py`; offered load честно через `--workers N` (multiprocessing), в отчёте target/achieved/accepted QPS, генератор упёрся → NOT TESTED + топология генератора в отчёте.
- `rtb_load.py`: open-loop генератор (asyncio scheduled), учёт timeout как нарушений, gate по error-rate/achieved QPS/bid-rate/валидности BidResponse; отчёт latency/throughput/учёт; exit≠0 на 403-only.
- Тесты: bash-тесты с подставными ответами; MockTransport 403 → FAIL.

### C4. O-05 — readiness и алерты
- `/health` (liveness) vs `/ready` (PG ping, Redis ping, consumer lag < N); Go `/healthz` 503 при stale/Redis down.
- `alerts.yml`: согласованные labels (`on() group_left`), `absent()` для cron/consumer, disk/memory через node_exporter; `promtool test rules` в CI с realistic labels.
- Audit middleware исключает `/pixel/collect`.

### C4b. N-01…N-05 — доставка уведомлений (решение 2026-09-12)

Правила Prometheus без доставки бесполезны; автоправила применяют действие молча. Два контура, одно ядро доставки. Первая итерация — только email; Telegram — следующим шагом на том же outbox.

- **N-01 ядро доставки.** Таблица `notification_outbox` (channel, recipient, subject, body_text, body_html, kind, dedup_key, status queued/sent/failed, attempts, next_attempt_at, last_error). Продюсер только пишет строку — запрос пользователя никогда не ждёт SMTP. Задача scheduler `notify_dispatch` раз в минуту: `SKIP LOCKED`, отправка stdlib `smtplib` в `asyncio.to_thread` (`SMTP_HOST/PORT/USER/PASSWORD/FROM/STARTTLS/SSL`; отдельная async-библиотека ради фонового dispatcher не нужна), backoff 1→5→15→60 мин, после 8 попыток — `failed`. Без SMTP-настроек — строки остаются `queued`, предупреждение на старте. Метрики `notify_outbox_queued`, `notify_outbox_oldest_age_seconds`, `notify_sent_total{channel,status}`; правило `NotifyOutboxStuck` (застрявшие письма → технический алерт). Retention 30 дней в `/cron/retention`.
- **N-02 технические алерты → суперадмин.** Alertmanager в compose (profile `monitoring`, 127.0.0.1:9093), `ops/prometheus/alertmanager.yml`: один receiver — webhook `POST /alerts/webhook` в backend с bearer `ALERTMANAGER_WEBHOOK_TOKEN`; group_by alertname, `repeat_interval` 4h, `send_resolved`. Backend: каждый firing/resolved → in-app уведомление всем `superadmin` + письмо на адреса из `app_settings.alert_emails` (суперадмин задаёт в UI; пусто → предупреждение в UI и на старте). `POST /settings/alerts/test` — тестовое письмо. Все правила без исключения — никакой фильтрации по severity на этом этапе.
- **N-03 бизнес-события → адресаты сущности.** `notify_emails TEXT[]` у `advertisers` и `campaigns`; разрешение адресатов: campaign → advertiser → email владельца-пользователя (первый непустой уровень; `notify_inherit=false` на кампании отключает подъём). События: `autorule_fired` (правило, действие, метрика, значение, порог), `budget_exhausted` (daily/total), `campaign_stopped_by_system` (модерация/блок/баланс), `campaign_ending_soon` (24 ч) / `campaign_ended`, `campaign_no_impressions` (активна, бюджет есть, 0 показов ≥ `NOTIFY_NO_IMPS_HOURS`=3), `low_balance` (баланс < `NOTIFY_LOW_BALANCE_DAYS`=1 × средний дневной расход). Дедуп: один `dedup_key` (kind+entity+день/час) — одно письмо; проверки в задаче scheduler `notify_business_checks` каждые 15 мин, `autorule_fired` — из `rules_engine`. Параллельно — in-app уведомление владельцу.
- **N-04 UI.** Суперадмин: страница «Алерты» (адреса, тест, последние 50 из outbox со статусом). Рекламодатель/кампания: поле «Email для уведомлений» (список, валидация), чекбокс наследования на кампании.
- **N-05 документация.** Runbook `#notifications` (outbox stuck, SMTP diag, Alertmanager down), DEPLOYMENT (SMTP, ALERTMANAGER_WEBHOOK_TOKEN), `.env.example`.

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
- [x] B12 S-02/S-03 — done (2026-09-10): B12a `rbac.Scope`/`scope_for`/`assert_in_scope`/`assert_owned_by`, `docs/audit/scope_spec.md`, `test_scope_matrix_db.py`; B12b stats/scoring report на `report_scope_for`, viewer boundary во всех ручках, `test_report_scope_db.py`
- [x] B13 L-02/L-03 — B13a (2026-09-10) версии erid, заморозка юр. полей, реальный Ozon-клиент, построчный экспорт; B13b (2026-09-11) акты: preview/draft/submit, коррекция; B13c (2026-09-11) ОРС + сводная площадка; B13d (2026-09-11) цепочка ОРД на кампании + шаг мастера; B13e (2026-09-11) акты по ОРС-модели; B13f (2026-09-11) UI «Акты» + версии erid; B13g (2026-09-11) ошибки ЕРИР — **B13 закрыт**
- [x] B14 F-02 — B14a, B14b, B14c done (2026-09-10)
- [x] B15 R-07 (2026-09-10) — billing по burl: `bid.burl` (`${AUCTION_PRICE}`, Go + Python); `supply_partners.billing_event` ∈ {win, burl} (миграция 046: существующие — `win`, DEFAULT для новых — `burl`; API/UI /supply «Событие расхода» + чекбокс `freq_cap_on_win`); режим `burl`: nurl → событие `win` без денег, `wins`+1, шаг freq cap, резерв остаётся, бид в `pending:burl` (`cache.win_pending`, `BURL_TTL` 600 с, дедлайн в `pending:bids` +300 с grace); `/api/rtb/burl` → `bill_win` + событие `billable` (`clear_price`) + `stats_hourly.billable_imps` + `luaSettleWin` (won → convert / won без резерва → recharge / won_expired → recharge `late=true`); `/cron/sweep-burl` каждую минуту → `win_expired` (резерв возвращён, денег нет); режим `win` — как раньше, burl игнорируется (`rtb_burl_in_win_mode_total`); CPC — деньги на клике в обоих режимах. `stats_math`: `SPEND_FROM_EVENTS_SQL` = win|billable, `BILLABLE_FROM_EVENTS_SQL`, `billable_imps` в summary/breakdown/report, `ecpm = spend/billable_imps`; UI dashboard «N оплачено». Метрики `rtb_burl_*`, `rtb_win_without_burl_total`, `rtb_pending_burl_size/oldest_age_seconds`, `rtb_burl_outcomes_total{partner,outcome}`; алерты `WinWithoutBurlRateHigh`, `PendingBurlQueueStuck`; runbook `billing-burl` с чеклистом включения burl на реальной SSP. Тесты: Python `test_burl_billing.py` (fakeredis Lua), `test_burl_handlers.py`, `test_burl_billing_db.py`; Go `redisx/burl_test.go`; Lua байт-в-байт. `[V]` документация биржи о семантике burl — при онбординге, до переключения партнёра на `burl`
- [ ] C1 O-01 — **C1a done (2026-09-11)**: `compose.yml` + `compose.prod.yml` (Caddy 80/443, порты закрыты, `!reset`/`!override`) + `compose.external.yml` (managed PG/Redis) + `Caddyfile` + `ops/prometheus/prometheus.yml` (profile `monitoring`, 127.0.0.1:9091); worker/beat/flower/legacy `bidder`/`init.sql` удалены из compose, `celery flower` из Dockerfile; Go `EventBaseURLProblem` (fail-fast в production на пустом/loopback/внутреннем `EVENT_BASE_URL`) + `/bidder -healthcheck` для distroless; `.env.example` переписан, `.env.external.example`; CI job `compose` (config dev/prod/external, `caddy validate`, `promtool`). **C1b done (2026-09-11)**: миграция 053 `scheduled_jobs` (seed 13 задач из `scheduler.DEFAULT_JOBS`, `fx_refresh`/`ml_train`/`domain_quality` выключены), `backend/cronexpr.py`, сервис `scheduler` (`backend/scheduler.py`, тик 15 с, `SKIP LOCKED`+lease с heartbeat 20 с / expiry 90 с, claim ≤ свободных слотов, complete только владельцем lease, `timeout_seconds` = общий бюджет запроса, misfire → один запуск, concurrency 4, `:8001/metrics`+`/healthz`, `--healthcheck`) в `compose.yml`/`prod`/`external`; `GET/PATCH /scheduler/jobs`, `POST /scheduler/jobs/{name}/run`; метрики `scheduler_last_tick_timestamp_seconds`/`scheduler_jobs_overdue`/`scheduler_jobs_inflight`/`scheduler_job_failures_total`/`scheduler_lease_lost_total`, алерты SchedulerDown/SchedulerJobsOverdue/SchedulerDispatchFailing/SchedulerLeaseLost, runbook `#scheduler`, DEPLOYMENT §9.1 переписан (crontab убран); **C1c done (2026-09-11)**: `bidder/` (Java RTB4FREE: Dockerfile, pom, src, README), `docs/rtb4free-setup.md`, `docs/centralbidder.md` удалены; `/rtb-feed/*` остаётся (его читает `bidder-go/internal/feed`), Java/Maven-секция `.gitignore` убрана; GAP-018 закрыт; **C1d done (2026-09-11)**: удалён HTTP-роут `POST /rtb/bid/{key}` и флаг `RTB_PYTHON_SERVING` (нет в backend/go/compose/Caddy/.env); `process_bid_request`/`_build_adm`/`rtb_compat` остались оракулом паритета без маршрута; симулятор гейтит `engine=python_reference` (по умолчанию `engine=go`; Go 503 → отказ 503 без тихого фолбэка; `python_reference` — 404 в production, 403 без `SIMULATOR_ALLOW_PYTHON_REFERENCE`); `obs_metrics` больше не считает `rtb_bid_requests_total`/`rtb_bids_total`/гистограмму `bid_latency` (bid-запросы обслуживает только Go) — вместо них `simulator_requests_total{engine}`/summary `simulator_engine_latency_seconds{engine}`; тесты `test_obs_metrics.py`+`test_simulator.py`+`test_startup_checks.py` зелёные (58); событийные роуты без изменений; **C1e done (2026-09-11)**: `legacy/` (79 файлов: Python `rtb_bridge` к RTB4FREE, `celery_app`/`celery_beat_schedule`, старый Vue) удалён — consumers не найдены (grep по `legacy/`, `rtb_bridge`, `celery_*` в backend/frontend/bidder-go/compose/CI/Caddy/.env.example пуст); `vercel.json` без `crons`, `deploy/vercel.services.json` удалён (Vercel — preview only, не источник расписания); **C1f done (2026-09-11)**: удалены `ROADMAP.md`, `docs/ROADMAP.md`, `docs/DSP_MVP_PLAN.md`, `docs/mvp_plan.md`, `docs/STEP_LOG.md`, `docs/architecture.md`, `docs/PROD_RUNBOOK.md` (Vercel+Fly гибрид; smoke/пилот перенесены в DEPLOYMENT §10.1/10.2 под Go bidder + Compose); `README.md` и `docs/ARCHITECTURE.md` переписаны по текущему коду с блоком зафиксированных решений; этот план — единственный активный roadmap; попутно: gauge в `/metrics` рендерились через `:g` (unix-время теряло до ±5000 с) — исправлено. · C1c remove Java bidder · C1d remove Python serving
- [x] C2 O-02 (2026-09-11) — `ops/backup.sh`/`ops/restore.sh`/`ops/lib/backup_common.sh` переписаны: один артефакт `backup-<ts>.tar` формата 2 (`postgres.dump` custom + `ledger_snapshot.tsv`/`counts.tsv`/`schema_migrations.tsv` из **одного** `pg_export_snapshot`, `uploads.tar`, `redis/dump.rdb` по `--with-redis`, `manifest.json` с sha256 и результатом сверки, `SHA256SUMS`); restore принимает только его: checksum → exit 5 без касания базы; `pg_restore --jobs` из файла; «restore completed» отдельно от «verification passed» — сверка байт в байт тех же снимков, расхождение → exit 4, баланс не правится, проводки не дописываются, rollback не делается; pre-existing `balance≠SUM(transactions)` из manifest сверку не валит; `--scratch`/`--verify-only`/`--target-db --yes`/`--restore-uploads`/`--with-redis --yes-replace-redis` (Redis через пересборку AOF, DBSIZE == manifest). `VERIFY=weekly|always` в backup.sh = реальный restore --scratch; `OFFSITE_CMD`/`ALERT_CMD` провайдер-нейтральны. Тест: `ops/tests/backup_restore_drill.sh` (+`seed_drill.py`, `compose.ci-drill.yml`, `drill.env`) — изолированный compose-проект `centraldsp-ci-*` под guard `CENTRALDSP_TEST_MODE=1`, seed с тремя статусами сверки (rounding/ok/mismatch), backup → негативные тесты (corrupt→5, без --yes→3) → `down -v` → restore + uploads + redis → независимая сверка → tamper → exit 4; CI jobs `backup-restore-drill` и `compose` (render override + shellcheck ops/*). Локально прогнано против PG 15 через docker-shim (backup, scratch, negative ×5, tamper). `[V]` OFFSITE_CMD + S3 Object Lock на VPS — отдельно (release plan «immutable offsite»); отдельный `pg_stat_statements`/PITR не в scope.
- [x] C3 O-03/O-04 (2026-09-12) — `ops/preprod_check.sh` строго read-only, `--apply` убран (накат — `docs/runbooks/release.md` §2 по шагам), exit 0/1/2/3, цепочка зависимостей (bidder down → RTB-пробы NOT TESTED; backend down → пробы через `compose run --rm --no-deps`), пробы из контейнера backend через `ops/lib/http_probe.py` (bidder distroless, host-портов в prod нет), `--json`, `--load` пробрасывает вердикт `rtb_load` как есть; контракт-тесты `ops/tests/preprod_check_test.sh` (подставные docker/curl, 26 кейсов, CI). `backend/loadtest/rtb_load.py` переписан: `--workers N`, 429 отдельный gate, 200 валидируются как BidResponse, насыщение генератора → NOT TESTED exit 3, сплошные 401/403/404 → CONFIG exit 2, timeout в гистограмме как tmax, JSON в stdout / сводка в stderr; `backend/tests/test_rtb_load.py` (MockTransport, 19 кейсов). Попутно: `migrate.py --dry-run` не создаёт `schema_migrations` на пустой БД; `convergence_check.py --api-url` сравнивает по тому же `hours`-предикату, что и `/stats/summary`, требует платформенную роль. Коммиты 5967172, 25d7b96, 9bd1a73.
- [x] C4 O-05 (2026-09-12) — liveness/readiness разделены: backend `GET /health` (liveness, Docker HEALTHCHECK) vs `GET /ready` (`backend/readiness.py`: PostgreSQL `SELECT 1`, Redis PING, consumer `rtb:events` — жив/свежий/lag < `READY_MAX_EVENTS_LAG`; 503 + `checks` с кодом по каждой; кэш `READY_REFRESH_EVERY_S`), gauges `backend_ready`/`backend_postgres_up`/`backend_redis_up`/`rtb_events_consumer_up` из того же снимка; Go bidder `GET /livez` (liveness, `-healthcheck` для distroless) vs `GET /healthz` (readiness: feed fresh + Redis; 503 + `reasons`), `bidder_ready`/`bidder_redis_up` из одного `Readiness()` на скрейп; `redis_down` → no-bid без брони. `alerts.yml`: пять правил, которые никогда не срабатывали из-за label mismatch (`CronRetentionStale` `job=` → `cron_job=`, `BidderNoBidSpike` `sum without(reason)`, `BidderWinGapAnomaly`/`FreqWinWithoutImpHigh`/`RtbEventsConsumerSilent` через `sum()`), группы `centraldsp-availability` (`TargetDown`, `NodeExporterDown`, `BackendNotReady`, `BackendPostgresDown`, `BackendRedisDown`, `RtbEventsConsumerDown`) и `centraldsp-node` (disk/inode/read-only/memory/load); `node-exporter` в compose profile monitoring (host mounts ro, без портов, cap_drop ALL). `ops/prometheus/alerts_test.yml` — promtool unit-тесты на сериях с реальными job/instance (против HEAD-правил все пять падали), шаг CI `promtool test rules`; `backend/tests/test_monitoring_stack.py` (scrape targets ↔ compose, hardening node-exporter, HEALTHCHECK = liveness, покрытие правил тестами), `test_readiness.py`, Go `readiness_test.go`/`main_test.go`. `preprod_check.sh` — отдельная проверка `backend /ready` (FAIL с разбивкой `checks`), контракт-тест. Audit middleware исключает `/pixel/collect` и `/ready`. Runbook: `#target-down`, `#backend-ready`, `#node-resources`, вводная liveness/readiness; DEPLOYMENT §10. `[V]` Alertmanager/notification delivery — отдельно (профиль monitoring, следующий этап).
- [x] C4b N-01…N-05 (2026-09-12) — доставка уведомлений. N-01: `notification_outbox` (миграция 054) + scheduler `notify_dispatch` (раз в минуту, `SKIP LOCKED`, stdlib smtplib в `to_thread`, backoff 1→5→15→60 мин, после `NOTIFY_MAX_ATTEMPTS`=8 — `failed`), метрики `notify_outbox_queued`/`notify_outbox_oldest_age_seconds`/`notify_sent_total`, правила `NotifySmtpNotConfigured`/`NotifyOutboxStuck`/`NotifyEmailsFailing`, retention 30 дней. N-02: Alertmanager в compose (profile monitoring, 127.0.0.1:9093, fail-fast на пустом `ALERT_WEBHOOK_SECRET` в command контейнера) → `POST /alerts/webhook` (Bearer) → `alert_events` (дедуп fingerprint+status+startsAt) + in-app всем суперадминам + письма на адреса из `platform_settings.alerting` (пусто → email суперадминов; порог `email_min_severity` только для писем); `Watchdog`/`AlertPipelineDead` — сторож самого контура. N-03: `notify_emails` у advertisers/campaigns + `notify_inherit`, разрешение campaign → advertiser → владелец; события autorule_fired/budget_exhausted/campaign_stopped_by_system/campaign_ending_soon/campaign_ended/campaign_no_impressions/low_balance, дедуп по `dedup_key` на день/час, задача `notify_business_checks` каждые 15 мин. N-04: страница суперадмина `/admin/alerts` (адреса/порог/тест, pipeline, активные алерты, лента, outbox с ошибками), поле «Email для уведомлений» у рекламодателя и кампании, чекбокс наследования. N-05: runbook `#notifications` (+ разбор «письмо не пришло» для бизнес-уведомлений), DEPLOYMENT §3 (SMTP_*, ALERT_WEBHOOK_SECRET), §6 п.6 (первичная настройка адресов и тест), §10 (контур доставки), `.env.example`. `[V]` на VPS: реальный SMTP + Alertmanager под профилем monitoring — тестовое письмо и Watchdog в UI.
- [x] C5 O-06 (2026-09-12) — миграция 057: `bid_events_ensure_partitions` создаёт партицию как standalone (`LIKE` без `INCLUDING IDENTITY` — совместимо с PG 17), под `LOCK TABLE bid_events_default` переносит строки дня из DEFAULT и делает `ATTACH PARTITION` в одной транзакции (раньше `CREATE TABLE … PARTITION OF` падал на строках в DEFAULT, и cron не мог создать ни одной партиции); `bid_events_partition_horizon_days()`. `/cron/retention`: gauge DEFAULT публикуется ДО ensure, партиции на `BID_EVENTS_PARTITIONS_AHEAD_DAYS` (60) вперёд, gauge `bid_events_partition_horizon_days`, SQL вынесен в константы (`$1::int` — иначе `operator is not unique: date + unknown`, проверено на живом PG). Алерт `BidEventsPartitionHorizonShort` (<7 дней, состояние таблицы, не факт запуска cron) + promtool-тесты. `scripts/partition_repair.py` (status / ensure / repair-default [--dry-run|--batch|--purge-expired] / backfill-legacy / verify-legacy; advisory lock; сессия UTC + проверка выравнивания границ партиций; прошлые дни — staged-таблица батчами + ATTACH, возобновляемо; живые — атомарно через ensure; exit-коды 0/1/2/3/4). Runbook `partition_upgrade.md` (#horizon/#default/#big-table/#legacy/#tz), `rtb_alerts.md` #default-partition исправлен (было `ts` и `INSERT … SELECT` без `OVERRIDING SYSTEM VALUE`), DEPLOYMENT §9.2 — убран совет править 033 (ломал sha256-контроль `migrate.py`), процедура «подмена пустой таблицей → migrate.py → backfill-legacy при живом backend». Тесты: `test_partition_repair_db.py` (8, живой PG: 10k строк в DEFAULT → repair → 0, count совпал; гонка с незакоммиченной вставкой — без LOCK воспроизводит ошибку из аудита; misaligned bounds → rc 1; legacy backfill идемпотентен), `test_obs_gauges_cron.py` (порядок gauge/ensure). Репетиция на синтетической копии (PG 16, 300k строк / 120 дней): swap 10 мс, backfill 227,5k строк — 16 с, md5 содержимого совпал; repair-default 103k строк / 41 день — 1,5 с. `[V]` та же репетиция на копии прод-данных перед деплоем (порядок в runbook #big-table).
- [ ] C6 O-07/F-01
