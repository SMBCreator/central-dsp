# Технический аудит готовности к продакшену — Central DSP

Дата: 2026-09-03. Ветка: `feat/avito-ui-kktu` (после блоков 1–5 текущего этапа).
Метод: чтение кода и миграций, прогон `tsc --noEmit`, `pnpm audit`, `pytest` (ограниченно — без Postgres в песочнице),
ручной прогон UI (390px и 1280px). Go-тесты и нагрузочный прогон в песочнице недоступны (нет `go`/docker) — отмечено как «не проверено».

Статусы: **OK** — можно в прод; **Риск** — работает, но требует доработки в 1–3 спринта; **Критично** — блокер релиза.
Оценка трудозатрат: S (≤ 1 день), M (2–4 дня), L (≥ 1 неделя).

---

## Статус исправлений (обновлено 03.09.2026, ветка `feat/avito-ui-kktu`)

| # | Статус | Что сделано | Коммит |
|---|---|---|---|
| R1 | Закрыт | `032_ssp_token_hash.sql`: `auth_token_hash` (sha256) + `auth_token_hint`; `ssp_tokens.py` с `compare_digest`; Go `partner_auth.go` — `subtle.ConstantTimeCompare`, заголовок `X-CentralDSP-Token` (+legacy `X-SSP-Token`); токен показывается один раз при выдаче | `5d8aab3` |
| R3 | Закрыт | Go: метрика `gate_error`, `tmax` → `context.WithTimeout` (`bidDeadline`: резерв 20 мс, min/default бюджет) | `5efe61f` |
| R4 | Закрыт (код) | `033_bid_events_partitioned.sql`: RANGE по дням + DEFAULT, функции `ensure_partitions` / `drop_old_partitions`; `/cron/retention` (02:30 UTC, `BID_EVENTS_RETENTION_DAYS`=90); `migrate.py --stamp <file>`; накатка на большой таблице — DEPLOYMENT §9.2. **На проде выполнять в окне низкого трафика** | `634731c` |
| R5 | Закрыт | `ops/backup.sh` (pg_dump custom + Redis RDB + uploads, ротация, restore-check в temp-БД), `ops/restore.sh`, RPO 24 ч / RTO 2 ч, ежемесячный drill — DEPLOYMENT §9 | `23f9373`, `bb1202c` |
| R6 | Закрыт | `next.config.ts`: CSP report-only, `X-Frame-Options SAMEORIGIN`, nosniff, Referrer-Policy, HSTS; снят `ignoreBuildErrors` (tsc чист). Enforce CSP — после 2 недель без репортов | `1ca6a38` |
| R9 | Закрыт | Go: `http.MaxBytesReader` 256 KiB → 413 до парсинга; тесты `guard_test.go`. **Прогнать `go test ./...` на сервере** — в песочнице нет тулчейна | `5efe61f` |
| R10 | Закрыт | `pixel_guard.py`: дедуп (visitor, event, минута) через `SET NX`, лимит на сессию и IP, fail-open при недоступном Redis; тест на fakeredis | `23ef447` |
| R12 | Закрыт | Оси и подписи дашборда/аналитики — UTC с явной пометкой «время UTC» | `bb1202c` |
| R2, R7, R8, R11, R13 | Открыты | См. дорожную карту; R11 (152-ФЗ) требует решения по механизму согласия | — |

Нагрузка: прежние прогоны Go-биддера (≈35k QPS, p99 ≈ 28 мс) в репо как артефакт не сохранены. После деплоя R3/R9 — повторный прогон `backend/loadtest/rtb_load.py` против Go-биддера с сохранением отчёта в `docs/audit/loadtest/`.

## Сводная таблица рисков (исходная, на момент аудита)

| # | Раздел | Находка | Статус | Оценка |
|---|--------|---------|--------|--------|
| R1 | 6.3 | `supply_partners.auth_token` хранится в открытом виде; сравнение без constant-time | Критично | S |
| R2 | 6.3 | HTML5-креативы: `html_snippet` вставляется в `adm` без санитайза, в кабинете рендерится в iframe `sandbox="allow-scripts"` без `allow-same-origin` (ок), но на стороне SSP — как есть | Риск | M |
| R3 | 6.2 | Freq caps / бюджет-гейт в Go: при ошибке Redis кандидат пропускается (`continue`) — fail-close по деньгам, корректно; но нет метрики доли `gate_error`→ тихая потеря трафика | Риск | S |
| R4 | 6.4 | `bid_events` без партиционирования и ретенции; при 1k QPS ≈ 86M строк/сутки | Критично | M |
| R5 | 6.5 | Бэкап — только ручная строка `pg_dump` в DEPLOYMENT.md; нет RPO/RTO, нет проверки восстановления | Критично | S |
| R6 | 6.3 | Нет CSP (даже report-only); `ignoreBuildErrors: true` в `next.config.ts` | Риск | S |
| R7 | 6.1 | Дубли бизнес-логики Python↔Go (`rtb.py` 1240 строк vs `bidder-go/internal/rtb`, `prefilter.py` vs `targeting.go`, shading) | Риск | L |
| R8 | 6.8 | 6 тестов бэкенда зависят от живого Redis (без маркера `integration`); нет e2e | Риск | M |
| R9 | 6.2 | Нет ограничения размера тела запроса на SSP-эндпоинте (Go `http.Server` без `MaxBytesReader`) | Риск | S |
| R10 | 6.4 | Пиксель: `Access-Control-Allow-Origin: *`, нет дедупа событий по (visitor, event, минута) — накрутка целей | Риск | M |
| R11 | 6.7 | 152-ФЗ: пиксель собирает device/visitor id без фиксации согласия; нет политики хранения ПДн | Риск | M |
| R12 | 6.5 | Часовой пояс: вся агрегация в UTC, в UI не указано; для ОРД/финансов нужен MSK | Риск | S |
| R13 | 6.1 | 355 TODO/FIXME; `mypy` объявлен в `pyproject.toml`, но не в CI | Риск | M |

Критичных — 3 (R1, R4, R5). Все закрываются за один спринт.

---

## 6.1 Архитектура и код

**Карта сервисов.** `frontend/` (Next.js 16, App Router, прокси `/api/[...path]` → FastAPI с mock-fallback), `backend/` (FastAPI + asyncpg + Redis; ORD, скоринг, биллинг, ML), `bidder-go/` (горячий путь RTB), Celery/Flower (cron через `/cron/*` под `CRON_SECRET`), Postgres 16, Redis.

| Проверка | Результат | Статус |
|----------|-----------|--------|
| Дубли логики | Python RTB (`routers/rtb.py`, 1240 строк) оставлен как reference и симулятор (GAP-020, accepted). Shading, prefilter, targeting реализованы дважды. Контракт-тесты Go↔Python есть (`parity_matrix.md`), но дрейф неизбежен | Риск (R7) |
| Схема БД | 31 миграция (`001`…`031`), все `IF NOT EXISTS`, идемпотентные. FK на `campaigns/creatives/line_items` есть. Мёртвых таблиц не найдено; `campaign_groups` используется как «Папки» | OK |
| OpenAPI ↔ `lib/types.ts` | Типы поддерживаются вручную; после блока 3 контракт `/scoring/report` синхронизирован (backend, mock, types). Автогенерации нет | Риск, S |
| TypeScript | `strict: true`, `tsc --noEmit` — 0 ошибок. Но `ignoreBuildErrors: true` — гейт отключён | Риск (R6) |
| Python типизация | `mypy` в `pyproject.toml`, в CI не запускается | Риск (R13) |
| TODO/FIXME | 355 вхождений (backend+go+frontend) | Риск (R13) |

**Воспроизведение:** `cd frontend && pnpm exec tsc --noEmit`; `grep -rn "TODO\|FIXME" backend bidder-go frontend/{app,components,lib} | wc -l`.

**Рекомендации:** убрать `ignoreBuildErrors` (tsc уже чист) — S; добавить `mypy --strict` на `backend/{tokens,auth,rbac,billing_core}.py` как первый контур — S; генерировать `lib/types.ts` из `/openapi.json` (`openapi-typescript`) в CI с diff-check — M; зафиксировать в `parity_matrix.md` владельца каждой дублированной функции и тест-двойник — входит в R7.

---

## 6.2 RTB

| Проверка | Результат | Статус |
|----------|-----------|--------|
| OpenRTB поля | 2.5: `tmax`, `badv`, `bcat`, `user.eids` (UID2/ID5/RampID), `device`, `geo`, `imp[].banner/video/native`. Multi-imp — `for i := range req.Imp` в Go, ответ по каждому imp. `schain`/`source.ext.schain` не разбирается; `regs.ext.gdpr`/`consent` не читаются (для РФ приемлемо, но нужно для зарубежных SSP) | OK / Риск S |
| tmax | Go-сервер `ReadTimeout/WriteTimeout` 5 с, внутренний бюджет < 100 мс; `tmax` из запроса в контекст не пробрасывается | Риск, S |
| nurl/burl | `nurl = /api/rtb/win?bid=<token>&price=${AUCTION_PRICE}`, `burl` → `/rtb/imp`. Токен HMAC-SHA256 `v1|bid_id|exp` (`tokens.py`) | OK |
| Win-notice дедуп | `billing_core.charge()` возвращает `billed=False` на повтор (идемпотентно через ledger); `imp`/`click` — `mark_once` (Redis `SET NX EX 86400`) | OK |
| Freq caps / бюджет | `GateAndReserve` — Lua-скрипт, атомарный, ключи `spend:camp:*`, окна caps с TTL. При ошибке Redis — `continue` (fail-close по деньгам) + `IncGateError` | OK, но нет алерта на `gate_error` (R3) |
| Pacing при N инстансах | Бюджет в Redis (общий), pacing-коэффициент считается в Python по часу — консистентно между инстансами | OK |
| Fail-open/close | Prefilter (`prefilter.py`) fail-open по документу; блок-листы/скоринг — fail-open при отсутствии в кэше; деньги — fail-close | OK (осознанно) |
| Body size SSP | Нет `http.MaxBytesReader`; `json.NewDecoder(r.Body)` без лимита | Риск (R9) |
| Rate limit per partner | Нет; авторизация партнёра — по `auth_token` (см. 6.3) | Риск, S |
| Нагрузочный тест | `backend/loadtest/rtb_load.py` есть; в песочнице запустить нельзя (нет go/docker). p50/95/99 при 1k/5k QPS �� **не проверено** | — |

**Воспроизведение:** `grep -n "MaxBytesReader" -r bidder-go` → пусто; `grep -n "GateAndReserve" -A 8 bidder-go/internal/rtb/handler.go`.

**Рекомендации:** `r.Body = http.MaxBytesReader(w, r.Body, 64<<10)` + метрика — S; алерт `rate(gate_errors_total[5m]) > 0` в `ops/prometheus/alerts.yml` — S; пробрасывать `tmax` в `context.WithTimeout` (минус сетевой запас 20 мс) — S; per-partner rate limit в Go (token bucket в памяти) — S; прогнать `rtb_load.py` на staging и зафиксировать p99 в `test_report.md` — S (после деплоя).

---

## 6.3 Безопасность

| Проверка | Результат | Статус |
|----------|-----------|--------|
| Сессии | Cookie `cdsp_session`, серверная таблица `sessions`, TTL 30 дней, `logout` удаляет запись (revoke работает). Refresh нет — сессия скользящая не продлевается | OK / Риск S (sliding TTL) |
| Брутфорс логина | Redis fixed-window: 20/5 мин по IP, 8/5 мин по e-mail, fail-open (GAP-021 accepted) | OK |
| RBAC | `rbac.py`: 9 ролей, зависимости `require_campaign_write / platform_write / finance_write`, agency-скоуп. Роутеры используют Depends | OK |
| IDOR | Проверены `groups.py` (все запросы с `AND user_id = $2`), `campaigns.py`, `creatives.py`, `scoring.py` (владение кампанией). Грубый grep выдал 4 подозрительных места в `cron.py` (закрыт `CRON_SECRET`) и 3 в `rtb.py` (публичные трекеры по HMAC-токену) — ложные срабатывания | OK |
| SSP-эндпоинт | `supply_partners.auth_token` — plain text в БД, сравнение `==` (не constant-time), у партнёров с `NULL` авторизация отключена (миграция 011, «backward-compatible») | Критично (R1) |
| Пиксель | CORS `*` на `/pixel/*` (обязателен для стороннего JS), нет rate-limit и дедупа | Риск (R10) |
| Загрузки | Блок 2: magic bytes, лимит размера, whitelist MIME, хранение на диске вне webroot, отдача через маршрут | OK |
| HTML5/SVG XSS | В кабинете — `<iframe sandbox="allow-scripts" srcDoc>` без `allow-same-origin` (cookie недоступны) — OK. В `adm` для SSP `html_snippet` вставляется как есть — ответственность паблишера, но модерация должна проверять `<script src>` на внешние домены. SVG не принимается загрузчиком | Риск (R2) |
| Секреты | `.env` в `.gitignore`, только `.env.example`; compose требует `${VAR:?required}`; `SECRET_KEY` обязателен в production (GAP-003) | OK |
| Динамические ORDER BY | `stats.py:458/476` — `grp_by` строится из `dim_{i}` (индексы, не пользовательский ввод); `scoring.py` — whitelist (блок 3) | OK |
| Заголовки | `next.config.ts`: Referrer-Policy, X-Content-Type-Options, XFO. **CSP отсутствует** | Риск (R6) |
| Зависимости | `pnpm audit` — 0 уязвимостей; `pip-audit` — не запускался (нет в CI) | OK / Риск S |

**Воспроизведение R1:** `grep -n "auth_token" backend/routers/rtb.py` → строка 697, сравнение `==`; `SELECT auth_token FROM supply_partners` возвращает секрет.

**Рекомендации:** R1 — хранить `sha256(token)`, сравнивать `hmac.compare_digest`, миграция `032_ssp_token_hash.sql` с бэкфиллом и период двойной проверки, в UI показывать токен один раз при генерации — S; R6 — добавить `Content-Security-Policy-Report-Only` со стартовой политикой (`default-src 'self'; img-src 'self' data: https:; frame-src` для превью креативов; `connect-src 'self' <API origin>`), через 2 недели переводить в enforce — S; `pip-audit` в CI — S; R2 — на модерации автоматически помечать HTML5 с внешними `<script src>` не из whitelist CDN — M.

---

## 6.4 Данные

| Проверка | Результат | Статус |
|----------|-----------|--------|
| Сверка `bid_events` → `domain_stats_hourly` → дашборд/Статистика | Агрегация — cron `/cron/aggregate-hourly`; дашборд и Статистика читают одну таблицу. Отдельного reconcile-джоба (сумма сырых vs агрегат за сутки) нет — расхождения не будут замечены | Риск, S |
| Viewability | Миграция 028: `viewable` по MRC (50% × 1 с, видео 50% × 2 с), метрика в `obs_metrics`, колонка в Статистике | OK |
| Ретенция / партиционирование `bid_events` | Не найдено ни `PARTITION BY`, ни `DELETE ... WHERE ts <`, ни `pg_partman`. При 1k QPS ≈ 86M строк/сутки; индексы и `VACUUM` деградируют за 1–2 недели | Критично (R4) |
| Пиксель и 152-ФЗ | Собираются `visitor_id`, UA, IP (для geo), события целей; нет фиксации согласия, нет срока хранения | Риск (R11) |

**Рекомендации:** R4 — `bid_events` → декларативное партиционирование по дню (`RANGE (ts)`), cron `drop partition older than 14 d` для сырых событий, агрегаты хранить 13 месяцев; миграция с переливом через `INSERT ... SELECT` в окно низкой нагрузки — M; reconcile-cron `sum(bid_events) vs sum(domain_stats_hourly)` за вчера с алертом при расхождении > 0.5% — S.

---

## 6.5 Эксплуатация

| Проверка | Результат | Статус |
|----------|-----------|--------|
| Метрики | Go: `internal/metrics` (запросы, no-bid по причинам, гистограмма латентности, feed staleness, redis up, gate rejects/errors). Python: `obs_metrics.py` (bid/win/imp/click, спенд, token rejects, prefilter drops), `/metrics` в формате Prometheus | OK |
| Алерты | `ops/prometheus/alerts.yml` — 11 правил, runbook `docs/runbooks/rtb_alerts.md`. Нет правила на `gate_error` и на отставание cron ОРД | Риск, S |
| Бэкапы | В `DEPLOYMENT.md` — одна строка `pg_dump | gzip`; нет расписания, оффсайт-копии, теста восстановления. Uploads-volume добавлен в бэкап-секцию (блок 2) | Критично (R5) |
| Миграции и откат | Прямые SQL-файлы, идемпотентные, применяются скриптом; down-миграций нет (по практике — forward-only с бэкапом перед деплоем) | OK при наличии R5 |
| Blue/green биддера | Compose — один инстанс; `go_bidder_plan.md` описывает канареечный процент через SSP-конфиг; не реализовано | Риск, M |
| Падение Redis | Login fail-open; RTB деньги fail-close; `mark_once` бросит исключение → 500 на трекерах (потеря учёта показов) | Риск, S |
| Падение Postgres | FastAPI отдаёт 500; Go-биддер продолжает бидить по кэшированному фиду до `feed staleness` ал��рта | OK |
| UTC/Москва | Все `timestamptz`, агрегация по UTC-часам, в UI не подписан пояс; отчёты ОРД сдаются по MSK-суткам | Риск (R12) |

**Рекомендации:** R5 — `ops/backup.sh` (pg_dump + uploads → S3-совместимое хранилище, ежедневно, хранение 30 дней), ежемесячный `restore-drill` на staging с записью RTO; целевые RPO 24 ч / RTO 2 ч — S; трекеры — оборачивать `mark_once` в try/except с fail-open и метрикой `redis_errors_total` — S; R12 — вывести пояс в UI («по МСК»), агрегировать ОРД-выгрузки по `Europe/Moscow` — S.

---

## 6.6 UX

| Проверка | Результат | Статус |
|----------|-----------|--------|
| Роли | Прогон mock-режима под advertiser/manager/admin: меню и действия соответствуют `rbac.py`. Share-гость — только `/report` | OK |
| Флоу «регистрация → первый показ» | Регистрация → кампания (мастер) → группа объявлений → креатив (модалка, блок 2) → модерация → ОРД (маркировка, блок 5) → запуск. Точка трения: без `erid` кампания не стартует, а подсказка появляется только на `/ord` — стоит дублировать в карточке кампании | Риск, S |
| 390 px | Сайдбар off-canvas (блок 4), таблицы скоринга/статистики — горизонтальный скролл со sticky первой колонкой. Модалка креатива — шаги в один столбец | OK |
| a11y | Диалоги с `role="dialog"`/`aria-label`, сортировка `aria-sort`, тултипы `aria-describedby`, фокус-ловушка в модалках; иконки-кнопки с `aria-label`. Не проверено скринридером | OK |
| Консистентность | Единые `Field`-сетки фильтров, `ColumnsButton`, `Pagination`, `ConfirmDialog`; крошки и сайдбар — из `lib/nav.ts` | OK |
| UTF-8 в CI | Шаг проверки U+FFFD в `ci.yml` (блок 1) | OK |

---

## 6.7 Право

| Проверка | Результат | Статус |
|----------|-----------|--------|
| 38-ФЗ / ОРД | Токен `erid` до старта, подстановка в `adm`, ежемесячная выгрузка актов (cron `ord-export` зарегистрирован, блок 5), чек-лист `docs/ord_checklist.md` | OK |
| 152-ФЗ | Сбор идентификаторов пикселем без учёта согласия; нет раздела о сроках хранения и трансграничной передаче (все сервера в РФ — подтвердить в DEPLOYMENT) | Риск (R11) |
| Запрещённые категории (ККТУ) | Коды ККТУ в `schemas.py`, валидация на кампании/креативе, передача в ОРД. Список запрещённых к рекламе категорий (алкоголь, табак, финансы без лицензии и т.д.) — только на модерации вручную, автоматической блокировки по коду нет | Риск, S |

**Рекомендации:** R11 — параметр `consent=1` в пиксельном JS (устанавливает сайт-рекламодатель), без него не писать `visitor_id`; политика хранения сырых событий 14 дней (совпадает с R4) — M; ККТУ — таблица `kktu_restricted` с признаком «требует лицензии/запрещено» и жёсткий стоп на сохранении креатива — S.

---

## 6.8 Тесты

| Проверка | Результат | Статус |
|----------|-----------|--------|
| Backend | 23 файла, 236 тестов; CI с Postgres 16 и `fakeredis[lua]`. 6 тестов требуют живой Redis (`cache.mark_once`, rate_limit) — падают локально без него | Риск (R8) |
| Go | `go vet` + `go test ./...` в CI; 2 тест-файла в `internal/rtb` + `metrics_test.go`. Покрытие горячего пути (`handler.go`) частичное | Риск, M |
| Frontend | `tsc --noEmit` и `pnpm build` в CI; юнит-тестов и e2e нет | Риск (R8) |
| Нагрузочные | `loadtest/rtb_load.py` — только вручную | Риск, S |

**Рекомендации:** маркер `@pytest.mark.integration` на Redis-зависимые тесты + `-m "not integration"` в быстром прогоне — S; Playwright-скелет `frontend/e2e/` с 3 сценариями (запуск кампании через мастер; загрузка креатива с прогрессом; подключение SSP-партнёра с генерацией токена) против `NEXT_PUBLIC_MOCK=1`, затем против staging с seed `backend/seed_staging.py` — M; coverage-гейт Go ≥ 70% на `internal/rtb` — M.

---

## Дорожная карта

**Спринт 1 — блокеры и дешёвые закрытия (R1, R4, R5, R6, R9, R3)**
1. Хэширование `supply_partners.auth_token` + constant-time сравнение + миграция 032.
2. Партиционирование `bid_events` по дню + cron очистки 14 дней + reconcile-алерт.
3. `ops/backup.sh`, расписание, оффсайт, restore-drill, RPO/RTO в DEPLOYMENT.
4. CSP report-only; убрать `ignoreBuildErrors`; `pip-audit` в CI.
5. `MaxBytesReader` на SSP-эндпоинте; алерт на `gate_error`; fail-open `mark_once`.

**Спринт 2 — качество и право (R2, R8, R10, R11, R12)**
1. Автопометка HTML5 с внешними скриптами на модерации.
2. Маркер `integration`, Playwright-скелет (3 сценария), seed для staging.
3. Пиксель: `consent`, дедуп событий, per-site rate limit.
4. Пояс МСК в UI и ОРД-выгрузках.

**Спринт 3 — архитектурный долг (R7, R13, blue/green)**
1. Реестр владельцев дублированных функций Python↔Go, удаление мёртвых веток из `rtb.py`.
2. `mypy` на критичных модулях, разбор TODO по приоритету.
3. Канареечный процент трафика на второй инстанс биддера.
4. Нагрузочный прогон 1k/5k QPS на staging, фиксация p99 в `test_report.md`.

Связанные документы: `gap_register.md` (обновлён GAP-024…GAP-036), `reality_check_2026-08-31.md`, `parity_matrix.md`, `ord_checklist.md`, `runbooks/rtb_alerts.md`.
