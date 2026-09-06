# Central DSP — оставшийся план (A8 → C6), оценка готовности модулей и эксплуатационный мануал

**Дата:** 2026-09-06. **Ветка:** `feat/avito-ui-kktu`, HEAD `819e5df`.
**Основание:** `full_release_audit_2026-09-05.md`, `remediation_plan_2026-09-06.md`, фактическое состояние кода после закрытия A1–A7.

Документ из трёх частей:

1. Что осталось сделать (A8 → C6): что, зачем, как, чем доказывается.
2. Оценка готовности модулей 1–10 — взгляд тестировщика и fullstack-разработчика adtech.
3. Мануал эксплуатации: как сделать, чтобы суперадмин видел все ошибки сразу, как защищать и мониторить систему такого класса.

---

## Часть 0. Где мы сейчас

Закрыто кодом + регрессионным тестом + зелёным CI:

| Блок | Находка | Коммит | Суть |
|---|---|---|---|
| A1 | M-01/M-02 | `b6333ad` | Баланс только через финоперации; deposit/adjust атомарны и идемпотентны |
| A2 | M-04 | `1f21bc0` | Единая граница бизнес-дня (полночь МСК) в Python и Go |
| A3 | M-03 | `bc60466` | Redis как критическое хранилище: AOF, noeviction, fail-fast, rebuild-скрипт |
| A4 | M-05/M-06 | `b075037` | Per-bid финансовый резерв с terminal state, sweeper, идемпотентный refund |
| A5 | L-01 | `507fe14` | Mock-ОРД запрещён в production; mock erid не проходит feed gate |
| A6 | S-01 | `5709181`+`8f9caa0` | Origin/Referer-гейт против CSRF; SameSite=Lax |
| A7 | S-04 | `5b3ca1d` | Bootstrap под advisory lock; REGISTRATION_MODE enum; dev-флаги фатальны в production; пароли 72 байта; отзыв сессий; Go fail-fast на секретах |

Открыто: A8, A9 (безопасность), B1–B14 (сквозной RTB-контур), C1–C6 (эксплуатация). Порядок не меняется: A → B → C.

---

## Часть 1. Детальный план A8 → C6

Формат каждого пункта: **Проблема → Почему это важно → Что сделать → Чем доказать → Что нельзя доказать без сервера `[V]`**.

### A8. S-05 — AI endpoints (`frontend/app/ai-api/creative`, `/insights`)

**Проблема.** Два Next route handler вызывают платный AI Gateway. Проверки сессии на сервере нет — она есть только в клиентском `AppShell`. Любой, кто знает URL, тратит деньги проекта и получает системный промпт.

**Почему важно.** Прямая финансовая утечка + surface для prompt-injection на публичном домене. Не блокирует RTB, но блокирует выкладку фронта в интернет.

**Что сделать.**
- `frontend/lib/server-auth.ts`: `requireUser(req)` — берёт cookie `cdsp_session`, делает `GET ${BACKEND_URL}/auth/me` с этой cookie, кэширует ответ 30 с в памяти процесса по хэшу токена (не сам токен). Возвращает `{id, role, permissions}` или бросает 401.
- Оба handler'a: `const user = await requireUser(req)` первой строкой; при отсутствии `AI_GATEWAY_API_KEY`/gateway недоступности — `503 {error: "AI недоступен: не настроен ключ"}`, а не 500 со стеком.
- Лимит тела: `request.text()` → отказ 413 при `> 32 КБ` до JSON.parse; валидация полей zod-схемой (длина `brief`, allowlist `format`).
- Rate limit per user: token bucket в памяти процесса (`Map<userId, {tokens, ts}>`, 20/мин) + если есть `REDIS_URL` — `INCR`/`EXPIRE` ключа `ai:rl:<user>:<minute>`. Ответ 429 с `Retry-After`.
- Логирование: `user_id`, `route`, `tokens_in/out`, `latency_ms`, `status` — в stdout JSON, чтобы попасть в общий лог-пайплайн (см. Часть 3).
- Убрать из ответа клиенту любые внутренние детали ошибки gateway.

**Чем доказать.** `frontend/tests/ai-api.test.ts` (vitest, добавить в CI): без cookie → 401; с cookie, но `/auth/me` вернул 401 → 401; body 40 КБ → 413; 21-й запрос за минуту → 429; отсутствие ключа → 503. Мокаем `fetch` на backend и gateway.

**[V]** Реальный ответ gateway в production не проверяется до выкладки.

### A9. S-06 — SSP auth и IP-фильтр

**Проблема.** `revoke_token` обнуляет hash, и Go после этого пускает партнёра без авторизации (пустой hash = «auth не требуется»). IP-whitelist доверяет `X-Forwarded-For` от кого угодно; невалидный CIDR молча отключает фильтр.

**Почему важно.** SSP endpoint — единственная публичная точка, которая тратит бюджет рекламодателей. Отзыв токена должен закрывать дверь, а не открывать.

**Что сделать.**
- Backend `supply_partners`: `status IN (draft, testing, active, paused)`; переход в `testing/active` при пустом `token_hash` → 422 «сначала выпустите токен»; `revoke_token` → `status='paused'`, `token_revoked_at=now()`, hash обнуляется; ротация — новый hash и старый в `token_prev_hash` с `token_prev_valid_until = now()+15 min` (безразрывная смена на стороне SSP).
- Валидация `ip_whitelist` на сохранении: каждый элемент парсится `ipaddress.ip_network(strict=False)`; ошибка → 422 с указанием элемента.
- Go `partner_auth.go`: партнёр с пустым hash → 403 всегда, метрика `bidder_auth_reject_total{reason="no_hash"}`; `TRUSTED_PROXIES` (CIDR list из env) — `X-Forwarded-For`/`X-Real-IP` читаются только если `RemoteAddr` входит в этот список, иначе берётся `RemoteAddr`; `ParseIPWhitelist` возвращает `error`, при ошибке партнёр помечается `whitelist_invalid` → no-bid + метрика (fail-closed).
- Проверка `token_prev_hash` до `token_prev_valid_until`.
- Compose: `ports: "127.0.0.1:8000:8000"`, `"127.0.0.1:8090:8090"`, `"127.0.0.1:3000:3000"` — публикует только Caddy (C1).

**Чем доказать.** Go: таблица кейсов «RemoteAddr доверенный/нет × XFF задан/нет × whitelist валиден/нет × hash есть/нет» → ожидаемый статус и метрика. Python: revoke → статус paused; activate без hash → 422; невалидный CIDR → 422; ротация — оба токена валидны 15 минут, после — только новый.

**[V]** Реальный `RemoteAddr` за Caddy на VPS (что Caddy пишет XFF корректно и слушает только 80/443).

**Коммит и push после A9. На этом очередь A закрыта.**

### B1. R-01 — SSP URL ведёт в Go

**Проблема.** Кабинет показывает партнёру `${API}/rtb/bid/{key}` — Python-биддер, который в production выключен (A7). Партнёр получит 404/503.

**Что сделать.** Env `PUBLIC_BIDDER_BASE_URL` (обязателен в production, `startup_checks`); backend отдаёт его в `/settings/public`; `frontend/app/supply/page.tsx` копирует `${PUBLIC_BIDDER_BASE_URL}/rtb/bid/{key}`; runbook, `preprod_check.sh`, `rtb_load.py` используют его же. Python `/rtb/bid` в production отвечает 410 Gone с указанием правильного URL.

**Чем доказать.** Тест settings/public; тест 410 при `APP_ENV=production`; preprod_check проверяет, что URL из кабинета отвечает на OpenRTB ping.

### B2. E-01 — единая семантика spend и inventory dimensions

**Проблема.** Расход в raw-отчётах и SSP-срезах считается из разных событий (bid vs win), `publisher/tagid/bundle/domain` для imp/click не заполняются → в отчётах «—».

**Что сделать.** Договор в `docs/audit/definitions.md`: **spend = clearing price billable win**. Go пишет в `bidmeta` `publisher, tagid, bundle, domain, deal_id`; `_log_event` копирует их для impression/click/view; `stats.report`, `supply_partners._METRIC_COLS`, `convergence_check.py` считают расход по `event_type='win'` и `clear_price`.

**Чем доказать.** Тест через реальные handlers: bid → win → imp → click, затем `summary`, `report`, SSP card и convergence сходятся до копейки; ни одно измерение не пустое.

### B3. E-02 — атомарность win/impression

**Проблема.** `win_notice` сначала ставит Redis `mark_once`, потом пишет деньги и события. Падение между шагами = показ потерян навсегда, деньги могут списаться без события или наоборот.

**Что сделать.** Одна DB-транзакция: `bill_win` + `INSERT bid_events` + `UPSERT stats_hourly`; dedup через `UNIQUE (bid_id, event_type)` на `bid_events`; Redis `mark_once` — fast-path **после** коммита. Аналогично impression/click/view/video: `INSERT ... ON CONFLICT DO NOTHING RETURNING id` и bump агрегатов только при RETURNING.

**Чем доказать.** Failure-injection тесты: исключение после каждого шага → повтор вызова даёт ровно одно списание и одну строку события.

### B4. E-03 — надёжный Redis Stream consumer

**Проблема.** Consumer читает `>` без обработки pending, при падении между XACK и записью в БД события теряются или дублируют агрегаты; новый HOSTNAME оставляет чужие pending навсегда.

**Что сделать.** `bid_events.stream_id UNIQUE` (или таблица `event_inbox`), INSERT `ON CONFLICT DO NOTHING`, агрегаты только по вставленным; при старте — чтение `0` до пустого; `XAUTOCLAIM` pending старше 60 с; `_ensure_group` в retry-loop; poison event после N попыток → `dead_letter` таблица + метрика. Метрики `events_consumer_pending`, `events_stream_length`, `events_consumer_lag_seconds`.

**Чем доказать.** Тесты: crash до/после commit, смена HOSTNAME, poison event → ровно одна запись каждого события, dead-letter одна строка.

### B5. E-04 — сквозной test-флаг

**Проблема.** `is_test` теряется после win: клики, конверсии, ORD export и ML видят тестовые события как боевые. UI-симулятор тестирует Python, а не Go.

**Что сделать.** `is_test` из `bidmeta` копируется во все события и в pixel conversions; тест-события в `stats_hourly_test` (или колонка с фильтром по умолчанию); ORD export и ML исключают test; `/simulator/run` вызывает Go по HTTP с `test=1` и подписью, UI подписывает «через production Go».

**Чем доказать.** Полный test-funnel → баланс, `stats_hourly`, conversions, ord export не меняются; `stats_hourly_test` заполнена.

### B6. E-05 — pixel routing и ingestion

**Проблема.** Скрипт пикселя ходит на относительный URL — в рекомендуемой топологии (frontend отдельно) он попадает в Next, а не в backend. Тело парсится до лимита размера, нет ownership clid ↔ pixel.

**Что сделать.** `PUBLIC_API_BASE_URL` вшивается в скрипт; endpoint `/api/pixel/collect` (Caddy route → backend); лимит 16 КБ до парсинга (413), Pydantic-схема, 204 на мусор; rate limit по IP/pixel до обращения к БД; проверка, что `clid` принадлежит владельцу пикселя; test exclusion.

**Чем доказать.** Тесты: 17 КБ → 413 без обращения к БД; чужой clid → 204 и ничего не записано; ownership; rate limit.

**[V]** Реальная страница рекламодателя с пикселем на VPS.

### B7. R-02 — bounded stale feed и durable bookkeeping

**Проблема.** Go торгует по фиду любого возраста (backend упал час назад — Go продолжает по устаревшим бюджетам). Ошибка записи `bidmeta`/`XADD` не останавливает отправку оплачиваемого бида.

**Что сделать.** `FEED_MAX_AGE` (default 120 с) → no-bid + метрика `bidder_feed_stale=1`; `/healthz` → 503 при stale/Redis down; `FeedReload` возвращает реальный результат; ошибка `SET bidmeta` или `XADD` → no-bid (без bookkeeping нельзя платить).

**Чем доказать.** Go-тесты: feed возрастом 121 с → no-bid; Redis SET error → no-bid и метрика; `/healthz` 503.

### B8. R-03 — паритет настроек кабинета ↔ Go

**Проблема.** Кабинет позволяет выбрать CPC, pacing even, валюту, optimizer, clean traffic, scoring — Go исполняет только CPM без pacing. Рекламодатель платит за то, чего нет.

**Что сделать.**
- **CPC**: bid = `bid_cpc × pCTR × 1000`; резерв = ожидаемая стоимость клика; списание по клику (`bill_click`), win не списывает.
- **Pacing even**: Lua `pacing:<camp>:<date>` с allowance по минутам бизнес-дня, вероятностный throttle.
- **Валюта**: feed отдаёт `currency`, `fx_rate`; Go матчится по `cur`/`bidfloorcur`, конвертирует, отвечает в валюте запроса.
- **Optimizer/scoring/clean_traffic/prefilter**: feed переносит `blocked_domains/publishers/tagids`, `scoring_min`, `clean_traffic_only`; Go применяет в `matchCandidate`; списки датацентров/ботов — shared Redis SET.
- **pCTR**: логистическая модель с коэффициентами из feed, fallback на CTR кампании.
- `docs/audit/parity_matrix.md` становится исполняемым: каждая строка — Go-тест «изменил настройку → изменился BidResponse».

**Чем доказать.** Go parity tests по матрице; Python: CPC-кампания — win не списывает, click списывает ровно `bid_cpc`.

### B9. R-04 — OpenRTB совместимость

**Что сделать.** `bidfloorcur`, `deal.bidfloor/bidfloorcur`, `private_auction`, `at`; `seat_id` из onboarding; native assets по `request.assets` (id/required/len); video MIME/protocol/duration; неподдерживаемое → no-bid с причиной в метрике. Фикстуры `bidder-go/testdata/openrtb/*.json` (banner, native, video, PMP, multi-imp).

**Чем доказать.** Contract-тесты на фикстурах: валидный BidResponse по спецификации 2.5/2.6; невалидный запрос → корректный no-bid.

**[V]** Реальные запросы конкретного SSP.

### B10. R-05 — adm/VAST/escaping/viewability

**Что сделать.** URL builder через `url.QueryEscape`; HTML/XML/JS escaping через `html/template`/`encoding/xml`; allowlist схем `https`; VAST через `encoding/xml`; duration `HH:MM:SS`; трекеры для каждого формата; viewability-beacon (IntersectionObserver ≥50% / 1 с) для banner/native; video start — отдельная метрика.

**Чем доказать.** Golden-файлы adm для каждого формата; fuzz-тест на инъекцию `"'<>` в click URL/title; VAST валидируется по XSD.

### B11. R-06 — frequency и audience

**Что сделать.** In-flight frequency reservation в Lua (`freq:<user>:<camp>` INCR на bid, DECR на loss/expire); per-member expiry в Redis (ZSET score = expires_at); goal-segment hook; `ops/redis_rebuild_audiences.py`.

**Чем доказать.** Go: 3 параллельных bid при cap=2 → 2 бида; loss возвращает слот. Python: истёкший member не матчится.

### B12. S-02 + S-03 — ownership и scopes

**Проблема.** Ссылочные поля (`advertiser_id`, `creative_id`, `audience_id` и т.д.) проверяются на ownership не везде; scopes для list/detail/finance/share различаются.

**Что сделать.** `assert_owned(pool, table, id, user_scope)` во всех create/patch; `rbac.scope_for(user)` → единый SQL-фрагмент для всех GET/list/finance/stats; viewer — advertiser boundary везде.

**Чем доказать.** Матрица тестов роль × endpoint × операция (расширить `test_rbac_routes_matrix.py`, `test_idor_isolation_db.py`): любая попытка сослаться на чужой id → 404, не 403 (не раскрывать существование).

### B13. L-02 + L-03 — versioned erid и acceptance

**Что сделать.** `ord_registrations (creative_id, version, erid, status, provider, payload_hash, registered_at)`; `bid_events.creative_version`; юридически значимые поля → `_CONTENT_FIELDS`, изменение → новая версия + re-moderation; Ozon: per-row acceptance, `pg_advisory_lock` на экспорт, статус `partial`, корректирующий повтор.

**Чем доказать.** Смена текста → новая версия, старый erid сохранён у прошлых событий; экспорт с частичным отказом → `partial` и повтор только отказанных.

**[V]** Sandbox реального ОРД.

### B14. F-02 — единые definitions отчётности

**Что сделать.** `stats_math.py`: gross/net/markup/fx применяются одинаково в summary/timeseries/breakdown/report/share/CSV; totals — отдельный агрегирующий запрос (не сумма страницы); conversions в raw report; лимиты интервалов (max 93 дня для raw).

**Чем доказать.** Property-test: сумма breakdown == totals == summary для случайных данных; CSV == JSON.

**Коммит и push после B14. Очередь B закрыта — платформа исполняет всё, что можно выбрать в кабинете.**

### C1. O-01 — production Compose

**Что сделать.** Удалить `worker/beat/flower` и `init.sql` mount; сервис `cron` (alpine + curl + crond с расписанием `/cron/*` с `CRON_SECRET`); Caddy в Compose с автоматическим TLS; все порты на `127.0.0.1`; `.env.example` для Compose-топологии + `.env.external.example` для внешних PG/Redis; `EVENT_BASE_URL`, `PUBLIC_*` обязательны; `docker compose config` и `docker compose up --wait` smoke в CI.

**Чем доказать.** CI: `compose config` валиден; `up --wait` → `/health`, `/ready`, `/healthz` 200.

### C2. O-02 — backup/restore

**Что сделать.** Единый артефакт `backup-<ts>.tar`: pg_dump custom format, redis RDB (после `BGSAVE`), uploads tar, `manifest.json` с sha256 и версией схемы; `restore.sh` принимает только его, проверяет checksum, `pg_restore --jobs`, SQL verify (`sum(transactions.amount)` по advertiser == `balance`), проверка `schema_migrations`. Offsite: `rclone` в S3-совместимое хранилище с object lock (immutable 30 дней).

**Чем доказать.** Shell-тест на локальном PG: backup → drop → restore → сверка балансов и count событий; restore чужого/битого архива → отказ.

**[V]** Восстановление на чистом VPS по runbook (обязательная репетиция до запуска).

### C3. O-03 + O-04 — честные preflight и load

**Что сделать.** `preprod_check.sh`: `set -euo pipefail`, каждая проверка возвращает PASS/FAIL/SKIP/NOT TESTED, JSON-парсинг через `jq`, HTTP-коды, `--apply` только проверяет. `rtb_load.py`: open-loop (asyncio по расписанию, не closed-loop), timeout = нарушение, gate по error-rate ≤ 0.1 %, achieved QPS ≥ 95 % target, bid-rate в ожидаемом коридоре, валидность BidResponse; отчёт p50/p95/p99, throughput, учёт; exit ≠ 0 при 403-only.

**Чем доказать.** Bash-тесты с подставными ответами; MockTransport 403 → FAIL.

### C4. O-05 — readiness и алерты

**Что сделать.** `/health` (liveness) vs `/ready` (PG ping, Redis ping, consumer lag < 30 с, feed age); Go `/healthz` 503 при stale/Redis down; `alerts.yml`: согласованные labels, `absent()` для cron/consumer/бидера, disk/memory через node_exporter; `promtool test rules` в CI с реалистичными labels; audit middleware исключает `/pixel/collect`.

**Чем доказать.** `promtool check rules` + `promtool test rules` зелёные в CI; `/ready` 503 при остановленном Redis (compose-тест).

### C5. O-06 — партиции

**Что сделать.** Runbook `docs/runbooks/partition_upgrade.md`; `scripts/partition_repair.py` (перенос строк из DEFAULT в целевые партиции батчами, advisory lock, dual-write окно, cutover, проверка count); cron создаёт партиции на 2 месяца вперёд.

**Чем доказать.** Тест на локальном PG: 10k строк в DEFAULT → repair → DEFAULT пуст, count совпал.

**[V]** Репетиция на копии боевых данных.

### C6. O-07 + F-01 — hardening и UX

**Что сделать.** `requirements.lock` (pip-compile), `go.sum` verify, non-root в Dockerfiles, `Cache-Control: no-store` для авторизованных ответов, lock на создание пула, graceful shutdown (SIGTERM → дождаться in-flight, закрыть consumer); AppShell: состояния loading/403/503/blocked с retry, drawer с focus trap/Escape/restore, один `main`, локализация ошибок Pydantic.

**Чем доказать.** Playwright smoke (login → dashboard → 503 mock → экран с retry); `docker run --user` проверка; tsc + eslint в CI.

**После C6 — раздел 15 аудита: серверная приёмка на VPS с реальным SSP.**

---

## Часть 2. Оценка готовности модулей (1–10)

Шкала: **10** — можно запускать без оговорок; **7–8** — запускать можно, риски известны и мониторятся; **5–6** — можно тестировать, запускать нельзя; **3–4** — тестировать можно только изолированно; **1–2** — заглушка.

Две оценки: **Запуск** (с реальными деньгами и SSP) и **Тест** (можно ли осмысленно тестировать сейчас на стенде).

| # | Модуль | Запуск | Тест | Что держит оценку | Что поднимет до 8+ |
|---|---|---|---|---|---|
| 1 | Auth / сессии / RBAC (`auth.py`, `rbac.py`, `auth_router`, `users_router`, `share.py`) | **7** | **8** | После A6/A7: CSRF-гейт, bootstrap lock, отзыв сессий, fail-closed registration. Остались неравные scopes (S-03) и ownership не на всех ссылочных полях (S-02) | B12 |
| 2 | Финансовое ядро (`billing.py`, `billing_core`, `cache.py` Lua, `redisx.go`, `business_day.py`) | **6** | **8** | A1–A4 закрыли атомарность, идемпотентность, бизнес-день, резерв. Держит: win-commit неатомарен с событием (E-02), spend считается из разных событий (E-01). Redis persistence проверен только локально | B2, B3, `[V]` Redis AOF на VPS |
| 3 | Go-биддер: ядро аукциона (`handler.go`, `targeting.go`, `feed.go`) | **5** | **7** | Стабилен для CPM banner; latency-профиль хороший. Не исполняет CPC/pacing/валюту/optimizer (R-03), торгует по stale feed (R-02), нет bookkeeping-гарантии перед бидом | B7, B8 |
| 4 | Go-биддер: OpenRTB контракт и adm (`models.go`, `adm.go`) | **4** | **6** | PMP/native/video частично; нет escaping в click URL/title; VAST строкой | B9, B10 |
| 5 | Events pipeline (`rtb.py` win/imp/click, stream consumer, `stats_hourly`) | **4** | **6** | Самый большой оставшийся риск для сходимости цифр: dedup до commit, consumer теряет pending, test-флаг теряется | B3, B4, B5 |
| 6 | Pixel / conversions (`pixel.py`) | **4** | **5** | Routing сломан в рекомендуемой топологии, нет лимита тела до парсинга, нет ownership clid | B6 |
| 7 | ОРД (`ord/`, `ord_router`, export) | **5** | **6** | L-01 закрыт: production без mock. Нет версионирования erid, acceptance реального провайдера не подтверждён | B13, `[V]` sandbox |
| 8 | Кабинет: кампании / креативы / line items / модерация | **7** | **8** | Зрелый CRUD, валидация targeting, модерация. Держит только S-02 | B12 |
| 9 | Отчётность (`stats.py`, `share.py`, CSV) | **5** | **6** | Разные формулы gross/net/markup в разных срезах, totals как сумма страницы | B2, B14 |
| 10 | Supply partners / SSP onboarding | **5** | **6** | Revoke открывает endpoint, XFF без trusted proxies, URL Python-биддера в кабинете | A9, B1 |
| 11 | Frontend shell / UX (`AppShell`, proxy, страницы) | **7** | **8** | Работает, RBAC на клиенте, Cyrillic UI. Пустой экран при 503, drawer без focus trap | C6 |
| 12 | AI-инструменты (`ai-api/*`) | **3** | **4** | Нет серверной авторизации, лимитов, обработки отсутствия ключа | A8 |
| 13 | Compose / деплой / топология | **4** | **5** | Мёртвые worker/beat/flower, порты наружу, cron вручную, нет Caddy в Compose | C1 |
| 14 | Backup / restore | **3** | **4** | backup и restore несовместимы по формату, uploads не бэкапятся, нет offsite | C2 |
| 15 | Мониторинг / алерты (`ops/prometheus/alerts.yml`, 18 правил, `/metrics`) | **5** | **6** | Правила есть, но labels не согласованы, нет `absent()`, нет `/ready`, нет доставки алертов человеку, нет error tracking | C4 + Часть 3 |
| 16 | Preflight / нагрузочные инструменты | **3** | **4** | preprod_check не fail-fast, load даёт PASS на 403 | C3 |
| 17 | Партиции `bid_events` | **5** | **6** | Работают, но online-upgrade и repair DEFAULT не доказаны | C5 |
| 18 | Безопасность периметра в целом | **6** | **7** | A6/A7 закрыли основное в приложении. Нет WAF/fail2ban/IDS/автообновлений на хосте, нет ротации секретов | A8, A9, Часть 3.2 |
| 19 | Тесты и CI | **7** | — | 50 файлов Python-тестов с реальным PG, 16 Go-тестов, FFFD-гейт, зелёный CI. Нет ни одного frontend-теста, нет e2e, нет compose smoke | A8 (vitest), C1, C6 (Playwright) |

**Интегральная оценка.**

- **Готовность к тестированию на стенде с симулированным SSP сейчас: 6/10.** Всё ядро тестируемо; самые ценные тесты сейчас — сходимость денег и событий (B2–B4), они найдут реальные баги.
- **Готовность к тестированию с реальным SSP (тестовый трафик, тестовый бюджет): 5/10 → 7/10 после A9 + B1 + B7.** До этого партнёр получит неправильный URL и может торговать по устаревшему фиду.
- **Готовность к запуску с реальными деньгами: 5/10 → 8/10 после очереди B → 9/10 после C и серверной приёмки.**

Взгляд тестировщика: главный пробел — не количество тестов, а отсутствие **сквозного** сценария на реальных компонентах (Go → Redis Stream → consumer → PG → отчёт) и отсутствие тестов фронта. Взгляд adtech-разработчика: платформа честно решила денежный слой (редко бывает на этом этапе), но пока обещает в кабинете больше, чем исполняет в биддере — это самый быстрый способ потерять доверие первого рекламодателя.

---

## Часть 3. Эксплуатационный мануал: ошибки, защита, мониторинг

Всё ниже — то, что делают в системах с сотнями–тысячами QPS и реальными деньгами. Разделено на «автоматически» (машина делает и уведомляет) и «полуавтоматически» (машина готовит, человек подтверждает).

### 3.1. Чтобы суперадмин видел все ошибки сразу

Принцип: **ошибка — это событие с владельцем, а не строка в логе.** Три канала с разной срочностью, все они питаются одним потоком.

#### Уровень 1. Единый структурированный поток

| Что | Как | Режим |
|---|---|---|
| JSON-логи везде | Backend: `structlog`/`python-json-logger`, поля `ts, level, service, request_id, user_id, route, status, latency_ms, err_type, err_msg`. Go: `slog` JSON (уже есть). Next: `pino` в route handlers | Авто |
| `request_id` сквозной | Caddy генерирует `X-Request-ID`, backend/Go/Next пробрасывают в логи и в ответ клиенту; UI показывает его на экране ошибки («сообщите код») | Авто |
| Сбор | `promtail`/`vector` → **Loki** (лёгкий, в Compose) или **VictoriaLogs**. Retention 30 дней, ошибки — 180 | Авто |
| Ошибки как исключения | **GlitchTip** (self-hosted, Sentry-совместимый) или Sentry SaaS: `sentry-sdk` в FastAPI, `sentry-go` в биддере, `@sentry/nextjs` во фронте. Группировка по стеку, first-seen/regression, ссылка на request_id | Авто |

#### Уровень 2. Доставка человеку по срочности

| Срочность | Что | Куда | Время |
|---|---|---|---|
| **P0 — деньги/торговля остановлены** | Redis down, feed stale > 2 мин, `/ready` 503, Backend5xxShare > 5 %, spend anomaly, consumer lag > 5 мин, ORD export failed в РФ | Alertmanager → **Telegram-бот** суперадмина + звонок через **ntfy** с priority urgent / Zvonok / любой call-провайдер | ≤ 1 мин |
| **P1 — деградация** | p99 > SLO, no-bid spike, login-brute-force, CSRF rejects spike, disk > 80 %, backup не выполнился, cert < 14 дней | Telegram (без звонка) + in-app уведомление суперадмину | ≤ 5 мин |
| **P2 — нужно посмотреть** | Новая группа ошибок в GlitchTip, регрессия старой ошибки, deprecation, cron slow | GlitchTip → Telegram digest каждые 30 мин + in-app | ≤ 30 мин |
| **Ежедневно** | Сводка: spend/бюджет, win-rate, ошибки по группам, бэкап OK/размер, свободное место, число заблокированных IP | Telegram-сообщение 09:00 МСК + страница `/admin/health` в кабинете | 1 раз/день |

**In-app для суперадмина (полуавтоматически, кодом в проекте).** Таблица `notifications` уже есть. Добавить:
- Тип `system_alert` с `severity`, `fingerprint`, `first_seen`, `count`, `link` (в GlitchTip/Grafana).
- Webhook-роут `/internal/alerts` (подпись `ALERT_WEBHOOK_SECRET`), в который Alertmanager и GlitchTip шлют события; роут upsert'ит уведомление всем `superadmin` и дедуплицирует по fingerprint.
- В `AppShell` — красная плашка сверху при непрочитанном P0/P1; страница `/admin/alerts` с фильтрами и кнопкой «подтверждено» (ack уходит обратно в Alertmanager silence на 1 час).
- Страница `/admin/health`: live-статус `/ready`, `/healthz`, feed age, consumer lag, Redis memory/policy, последний backup, версия деплоя (git sha), число активных сессий.

#### Уровень 3. Ошибки, которых не видно в логах

- **Сходимость денег (auto, каждые 10 мин)**: cron `convergence_check`: `sum(transactions) == balance` по каждому рекламодателю; `sum(stats_hourly.spend) == sum(bid_events win clear_price)` за сутки; `spend:*` в Redis vs PG. Расхождение > 1 копейки → P0.
- **Синтетический бид (auto, каждую минуту)**: blackbox-проба отправляет тестовый OpenRTB в Go с `test=1` → ждёт валидный BidResponse → шлёт win/imp с тестовым флагом → проверяет, что событие появилось в PG за 30 с. Это единственный тест, который проверяет весь контур, а не отдельные процессы.
- **Тихие падения**: `absent(up{job="bidder"})`, `absent(cron_last_success_timestamp)`, `time() - cron_last_success > 2×interval` — для каждого cron-эндпоинта.
- **Бизнес-аномалии**: win-rate за 15 мин отклонился > 3σ от того же окна вчера; CTR > 5 % (бот-трафик); один publisher > 40 % расхода кампании; бид с ценой > 10× средней.

### 3.2. Чтобы сервер оставался безопасным, даже если его атакуют

Модель: **предположить, что периметр прорвут; сделать так, чтобы это было заметно за минуты и бесполезно для атакующего.**

#### Уменьшить поверхность (полуавтоматически при установке, затем авто)

| Мера | Реализация |
|---|---|
| Только 80/443 наружу | `ufw default deny`, allow 22 только с bastion/VPN-IP (Tailscale/WireGuard), 80/443 для Caddy. Все контейнеры на `127.0.0.1` (A9/C1) |
| SSH | ключи только, `PasswordAuthentication no`, `PermitRootLogin no`, нестандартный порт не защита — но `fail2ban` обязателен |
| Автообновления ОС | `unattended-upgrades` для security-репозитория, перезагрузка в окно 04:00 МСК с уведомлением |
| Контейнеры | non-root (C6), `read_only: true` + `tmpfs` где можно, `cap_drop: ALL`, `no-new-privileges`, pinned образы по digest, **Trivy** в CI на образах и `requirements.lock`/`go.sum` |
| Секреты | Не в `.env` на диске в открытом виде: `docker secrets`/`sops`+`age`; ротация `EVENT_TOKEN_SECRET`, `RTB_API_TOKEN`, `CRON_SECRET` раз в 90 дней с dual-key окном (как в A9 для SSP); ротация паролей PG/Redis; `PG` слушает только docker-сеть, `Redis` с `requirepass` и `rename-command FLUSHALL ""` |
| Caddy как WAF-lite | rate limit по IP на `/auth/login`, `/auth/register`, `/pixel/collect`, `/ai-api/*`; `request_body max_size`; блок `TRACE`; security headers (уже есть в Next — продублировать на Caddy); опционально **Coraza** (ModSecurity CRS для Caddy) в detect-only на месяц, затем блок |
| Frontend | CSP enforcing после report-only периода (сейчас report-only), `Cache-Control: no-store` для авторизованных (C6) |

#### Заметить взлом (авто)

| Сигнал | Инструмент | Реакция |
|---|---|---|
| Brute-force SSH/HTTP | **fail2ban** (sshd, caddy-auth jail) или **CrowdSec** (коллективные блок-листы, Caddy bouncer) | Автобан 1 ч → 24 ч, P1 при > 50 банов/час |
| Изменение файлов/бинарей/cron/ssh-ключей | **Wazuh agent** (или OSSEC/AIDE) — FIM на `/etc`, `/usr/bin`, `/root/.ssh`, `/opt/centraldsp`, docker socket | P0 немедленно |
| Подозрительные процессы/сеть в контейнере | **Falco** (eBPF): shell в контейнере, запись в `/etc`, исходящее соединение с backend-контейнера на неизвестный хост | P0 |
| Аудит ядра | `auditd` правила: exec от `www-data`/контейнерных uid, изменение `sudoers`, `setuid` | В Wazuh |
| Аномалии приложения | Метрики уже есть/появятся: `csrf_rejected_total`, `login_failures_total`, `bidder_auth_reject_total`, `event_token_rejects`. Alert на spike | P1 |
| Привилегированные действия | `audit_log` (есть): новый superadmin, смена роли, revoke/rotate SSP token, deposit/adjust > порога, смена `ip_whitelist`, экспорт данных | **Telegram суперадмину мгновенно**, независимо от алертов |
| Honeytokens | Фейковый рекламодатель с балансом и фейковый SSP-токен, которые никогда не используются легитимно; любое обращение — P0 | Cron-проверка + метрика |
| Сессии | `sessions` с `ip`, `user_agent`, `last_seen`; alert на вход superadmin с нового ASN/страны; страница «мои сессии» с revoke | Полуавто |

#### Пережить взлом (авто + репетиция)

- **Бэкапы immutable offsite**: C2 + S3 object lock 30 дней + отдельные креды только на запись (атакующий с сервера не может удалить историю). Ежемесячная **репетиция restore** на чистой VM — в календаре, результат в Telegram.
- **Отзыв всего одной командой**: `ops/incident_lockdown.sh` — ротирует все секреты, отзывает все сессии (`DELETE FROM sessions`), переводит всех SSP в `paused`, включает `LAUNCH_GATE` для новых кампаний, оставляет только read-only кабинет. Проверяется в CI как shell-тест на локальном стенде.
- **Изоляция ущерба**: PG-роль приложения без `SUPERUSER`/`CREATEDB`, без прав на `audit_log` DELETE/UPDATE (append-only через trigger); `audit_log` дублируется в Loki (вторая копия вне БД).
- **Runbook инцидента** `docs/runbooks/security_incident.md`: кто звонит, что снимаем (docker logs, auditd, netstat), в каком порядке — lockdown → снимок → восстановление из бэкапа до времени компрометации → ротация → постмортем.

### 3.3. Как отслеживают высоконагруженные системы этого класса

Индустриальный стандарт — **SLO-based monitoring**, а не «зелёные лампочки».

#### Четыре золотых сигнала на каждый сервис

| Сигнал | Backend | Go-биддер | Consumer | Frontend |
|---|---|---|---|---|
| Latency | p50/p95/p99 по route | p99 `/rtb/bid` (SLO ≤ 60 мс на сервере при tmax 100–150) | время от XADD до commit | TTFB, INP по страницам |
| Traffic | RPS по route/status | bid requests/s, bids/s, по партнёру | events/s | page views, API calls |
| Errors | 5xx share, exceptions | no-bid by reason, auth rejects, timeout share | dead-letter rate, retries | JS errors, failed fetches |
| Saturation | PG pool wait, CPU, memory | goroutines, GC pause, Redis RTT | pending, lag | — |

**SLO для старта (записать в `docs/slo.md` и в alerts):**
- Биддер: 99.9 % ответов ≤ 80 мс за 30 дней; availability 99.95 %.
- События: 99.9 % win/imp записаны в PG ≤ 30 с.
- Кабинет: 99.5 % запросов ≤ 500 мс; availability 99.9 %.
- Деньги: 100 % сходимость `transactions == balance` каждые 10 мин; расхождение — инцидент.

Алерты строятся на **error budget burn rate** (multi-window: 5 мин + 1 ч быстрое сгорание → P0; 6 ч + 3 дня медленное → P1), а не на фиксированных порогах — так нет усталости от шума.

#### Стек (всё self-hosted в Compose или отдельной VM)

| Слой | Инструмент | Зачем |
|---|---|---|
| Метрики | **Prometheus** (есть `/metrics`) + `node_exporter`, `postgres_exporter`, `redis_exporter`, `cadvisor`, `blackbox_exporter` | Хост, БД, Redis, контейнеры, синтетика |
| Дашборды | **Grafana**: «Торговля» (QPS, bid-rate, win-rate, p99, spend/min по кампаниям), «Деньги» (balance vs reserve vs spend, сходимость), «Инфра» (CPU/mem/disk/pool), «Безопасность» (auth rejects, bans, CSRF, audit events) | Одна страница на вопрос «всё ли в порядке» |
| Алерты | **Alertmanager** → Telegram + ntfy (звонок) + in-app webhook; `promtool test rules` в CI (C4) | Доставка и тестируемость правил |
| Логи | **Loki** + Grafana Explore; derived field `request_id` → переход из лога в трейс | Поиск по инциденту за секунды |
| Трейсинг | **OpenTelemetry** SDK (FastAPI auto-instrument, `otelhttp` в Go) → **Tempo**; sampling 1 % + 100 % для ошибок и для запросов > p99 | Понять, где именно тормозит win → bill → insert |
| Ошибки | **GlitchTip** | Группировка исключений, регрессии, release tracking по git sha |
| Uptime снаружи | **Uptime Kuma** или внешний сервис с другой сети/страны: `/health`, `/ready`, `/healthz`, TLS expiry, синтетический бид | Видеть, что видит пользователь/SSP |
| Статус-страница | Uptime Kuma public page для SSP-партнёров | Меньше вопросов «у вас лежит?» |

#### Процессы (полуавтоматически)

- **Релиз**: CI зелёный → build по digest → `docker compose pull && up -d` через `ops/deploy.sh` → пост-деплой синтетический бид + `/ready` → если error budget burn за 10 мин > 2× → автоматический откат на предыдущий digest (`ops/rollback.sh`). Canary на старте — `bidder-go` вторым инстансом с 10 % веса в Caddy.
- **Дежурство**: один человек = один канал (Telegram), ack обязателен за 5 мин для P0, иначе эскалация звонком второму контакту.
- **Постмортем** по каждому P0 в `docs/postmortems/YYYY-MM-DD.md`: таймлайн, root cause, что сработало, что нет, action items с владельцами — без поиска виноватых.
- **Game day** раз в квартал: выключить Redis / убить consumer / подменить feed на стенде — проверить, что алерты пришли, runbook работает, восстановление уложилось в SLO.
- **Capacity review** ежемесячно: p99 vs QPS, disk growth `bid_events`, Redis memory; правило — при 60 % утилизации планируем расширение.

### 3.4. Порядок внедрения мануала (что за чем)

| Этап | Когда | Что | Режим |
|---|---|---|---|
| 1 | Вместе с C1/C4 | JSON-логи + request_id; Prometheus/Alertmanager/Grafana/Loki в Compose; Telegram-доставка; `/ready`; `absent()`-алерты; блок 3.1 уровень 1–2 | Авто |
| 2 | До первого реального SSP | GlitchTip; in-app `system_alert` для суперадмина; синтетический бид раз в минуту; convergence-check каждые 10 мин; audit → Telegram для привилегированных действий | Авто |
| 3 | До запуска с деньгами | ufw/fail2ban или CrowdSec, unattended-upgrades, non-root контейнеры, secrets вне `.env`, immutable offsite backup + первая репетиция restore, `incident_lockdown.sh`, runbook инцидента | Полуавто |
| 4 | Первый месяц продакшена | Wazuh FIM, Falco, OpenTelemetry + Tempo, SLO и burn-rate алерты вместо порогов, Uptime Kuma снаружи, Coraza detect-only | Авто |
| 5 | Постоянно | Ротация секретов 90 дней, репетиция restore ежемесячно, game day ежеквартально, постмортемы, capacity review | Полуавто |

---

## Заключение

Очередь A закрыла денежный слой и основные дыры авторизации — это была правильная последовательность: сначала «нельзя потерять или украсть деньги», потом «торговать тем, что обещали», потом «эксплуатировать без героизма». Оставшиеся A8–A9 — два дня работы и закрывают публичный периметр. Очередь B — самая длинная и самая ценная: после неё цифры в кабинете, в биддере и в отчётах партнёру — одни и те же цифры. Очередь C и мануал из Части 3 превращают запуск из события в рутину.

Честная оценка сегодня: **тестировать — можно и нужно (6/10), запускать с деньгами — нет (5/10)**. После B — 8/10, после C и серверной приёмки — 9/10. Десятку в adtech не даёт никто: она появляется только после первого месяца реального трафика и первого постмортема.
