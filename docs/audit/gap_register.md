# Gap Register — Central DSP

Статусы: `fixed` (исправлено в этой итерации), `open` (требует работы),
`accepted` (осознанно принято, не блокирует прод).
Severity: `blocker` > `critical` > `high` > `medium` > `low`.

## Исправлено в этой итерации

| ID | Sev | Компонент | Описание | Фикс |
|----|-----|-----------|----------|------|
| GAP-001 | blocker | backend | `get_pool()` вызывался без `await` в `routers/bidder_feed.py` и `shading.py` — coroutine вместо пула, рантайм-падение фида биддера и обучения shading при первом обращении | `8379d8d`: добавлен `await`, +regression-тесты |
| GAP-002 | critical | backend/auth | `/auth/login` без rate limiting — brute-force паролей ничем не ограничен | `f756e64`: Redis fixed-window per-IP+username (10/5min), fail-open при недоступном Redis, +6 тестов |
| GAP-003 | critical | backend/security | `tokens.py` молча падал на dev-секрет при отсутствии `SECRET_KEY` — в проде подписи токенов предсказуемы | `80005b1`: `ENVIRONMENT=production` без секрета => RuntimeError на старте, +тест |
| GAP-004 | critical | infra | docker-compose с дефолтными паролями Postgres/Flower и пустым RTB-токеном — деплой «как есть» открывал БД и админку | `c2fe99e`: обязательные env через `${VAR:?required}`, `.env.example` дополнен |
| GAP-005 | critical | backend/rtb | Bid shading применялся при `at=2` (second price) — систематический недоплат/проигрыш аукционов, искажение обучения фактора | `d6f72d8`: `should_shade()` — только явный `at=1` + флаг кампании, +5 тестов |
| GAP-011 | high | observability | Нет алертинга на деньги: расход бюджета, win rate, разница bid/win цен, ошибки биллинга | Часть D: `bidder-go/internal/metrics` (запросы, no-bid по причинам, латентность-гистограмма, feed staleness, redis up, gate rejects) + `backend/obs_metrics.py` (bid/win/imp/click, спенд, token rejects) + `ops/prometheus/alerts.yml` (11 правил) + runbook `docs/runbooks/rtb_alerts.md` |
| GAP-022 | high | backend/rtb | Нет предфильтрации входящего SSP-трафика — боты, чужое гео и datacenter-IP проходили полный цикл (Redis+Postgres), сжигая инфраструктуру | Часть C: предфильтр в Go-биддере `bidder-go/internal/prefilter` (O(1)-отсев по UA/geo/device-id/CIDR, fail-open; `backend/prefilter.py` — паритетный оракул) + метрика `bidder_prefilter_drops_total{reason}` (Python-метрика `rtb_prefilter_drops_total` удалена в C1d) + admin API `/settings/throttle/subnets` + alert `PrefilterDropRateHigh` на Go-метриках |
| GAP-023 | high | backend/ml | Биддинг вслепую: ставка не учитывала вероятность клика, качество доменов не скорилось — закупка фродовых площадок наравне с чистыми | Часть B: `backend/ml/pctr.py` (сегментный logistic-composite pCTR, версии в `ml_models`, guardrails-множитель 0.7–1.3 только для traffic/conversions) + `backend/ml/quality.py` (explainable-скоринг доменов, clean-traffic режим per-campaign) + кроны `/cron/ml-train`, `/cron/domain-quality` + decision log `bid_events.pctr` + UI (тумблер, панель качества, ML-статус) |

## Открытые гэпы (приоритезированы)

| ID | Sev | Компонент | Описание | Рекомендация |
|----|-----|-----------|----------|--------------|
| GAP-010 | high | bidder-go | Скелет без имплементации budget/freq/shading/audiences — паритет не достигнут | Фаза 2 `go_bidder_plan.md`: перенос Lua-скриптов 1:1, contract-тесты |
| GAP-012 | high | ОРД | Ошибки отправки в ОРД не ретраятся управляемо; нет DLQ/отчёта о непереданных креативах | Celery-ретраи с backoff + таблица `ord_outbox` со статусами |
| GAP-013 | high | infra | Нет задокументированной стратегии backup/restore Postgres | pg_dump-крон + восстановление на stage раз в месяц (runbook) |
| GAP-014 | medium | backend | 7 интеграционных тестов скипаются без live Postgres — money-path частично не покрыт CI | CI-job с сервисным Postgres (docker) на GitHub Actions |
| GAP-015 | medium | backend/auth | Refresh-токены без ротации/отзыва списком | Ротация при каждом refresh + `jti` denylist в Redis |
| GAP-016 | medium | frontend | Дашборд не показывает причину остановки кампании (бюджет/капы/модерация) | Поле `pause_reason` в API кампаний + бейдж в UI |
| GAP-017 | medium | bidder-go | GeoIP: Java использует GeoLite2-volume, Go-скелет читает только `device.geo` | Подключить mmdb-ридер в Go при имплементации targeting |
| GAP-018 | low | repo | ~~Java-биддер остаётся в репо (deprecated)~~ — **закрыт C1c (2026-09-11)**: `bidder/` удалён, Go bidder единственный auction runtime | ~~Удалить каталог `bidder/` после cutover Go |
| GAP-019 | low | docs | README репо описывает Java как основной биддер | Обновить при merge этой ветки |

## Исправлено по аудиту 2026-09 (`prod_readiness_2026-09.md`, 03.09.2026)

| ID | Sev | Компонент | Описание | Фикс |
|----|-----|-----------|----------|------|
| GAP-024 | critical | backend/security | `supply_partners.auth_token` хранился в открытом виде, сравнение `==`, партнёры с `NULL` без авторизации (R1) | `5d8aab3`: миграция `032_ssp_token_hash.sql` (`auth_token_hash` sha256 + `auth_token_hint`, plaintext обнулён), `ssp_tokens.py` с `compare_digest`, Go `partner_auth.go` `ConstantTimeCompare`, заголовок `X-CentralDSP-Token` (+legacy `X-SSP-Token`), токен показывается один раз |
| GAP-025 | critical | data | `bid_events` без партиционирования и ретенции (R4) | `634731c`: миграция `033_bid_events_partitioned.sql` (RANGE по дням + DEFAULT, `ensure_partitions`/`drop_old_partitions`), `/cron/retention` 02:30 UTC (`BID_EVENTS_RETENTION_DAYS`=90), `migrate.py --stamp`, инструкция для большой таблицы — DEPLOYMENT §9.2. Reconcile-алерт сырые ↔ hourly — остаётся, см. GAP-037 |
| GAP-026 | critical | ops | Бэкап без расписания, оффсайта, restore-drill, RPO/RTO (R5) | `23f9373`, `bb1202c`: `ops/backup.sh` (pg_dump custom + Redis + uploads, ротация, restore-check), `ops/restore.sh`, RPO 24 ч / RTO 2 ч, ежемесячный drill — DEPLOYMENT §9 |
| GAP-027 | high | frontend/security | Нет CSP; `ignoreBuildErrors: true` (R6) | `1ca6a38`: CSP report-only, XFO SAMEORIGIN, nosniff, Referrer-Policy, HSTS; `ignoreBuildErrors` снят. Enforce CSP — после 2 недель без репортов |
| GAP-028 | high | bidder-go | Нет лимита тела запроса; `tmax` не в контексте (R9) | `5efe61f`: `MaxBytesReader` 256 KiB → 413, `bidDeadline(tmax)` → `context.WithTimeout`; `guard_test.go`. Per-partner token bucket — не сделан (QPS-гейт есть в Redis) |
| GAP-029 | high | observability | Ошибки Redis-гейта не видны (R3) | `5efe61f`: метрика `gate_error` в Go. Правило алерта в `alerts.yml` и fail-open `mark_once` — см. GAP-038 |
| GAP-031a | medium | pixel | Нет дедупа событий и rate-limit на `/collect` (R10) | `23ef447`: `pixel_guard.py` — дедуп (visitor, event, минута) `SET NX`, лимит на сессию и IP, fail-open; тест на fakeredis |
| GAP-033 | medium | ops | Пояс не подписан в UI (R12) | `bb1202c`: оси и подписи дашборда/аналитики в UTC с пометкой «время UTC». Переход ОРД-выгрузок на MSK-сутки — по решению бизнеса |

## Открытые гэпы по аудиту 2026-09

| ID | Sev | Компонент | Описание | Рекомендация |
|----|-----|-----------|----------|--------------|
| GAP-030 | medium | moderation | `html_snippet` HTML5-креативов уходит в `adm` без проверки внешних `<script src>` (R2) | Автопометка на модерации при скриптах не из whitelist CDN |
| GAP-031 | medium | legal | Пиксель: сбор `visitor_id` без согласия, нет срока хранения — 152-ФЗ (R11) | Параметр `consent=1`, без него не писать `visitor_id`; политика хранения 14 дней |
| GAP-032 | medium | tests | 6 бэкенд-тестов требуют живой Redis без маркера; нет e2e; покрытие Go `handler.go` частичное (R8) | `@pytest.mark.integration`; Playwright-скелет на 3 сценария; coverage-гейт Go ≥ 70% на `internal/rtb` |
| GAP-037 | medium | data | Нет reconcile сырые `bid_events` ↔ `stats_hourly` за вчера — расхождения не будут замечены | Cron-сверка с алертом при расхождении > 0.5% |
| GAP-038 | medium | observability | Нет правила алерта на `gate_error`; `mark_once` на трекерах бросает 500 при падении Redis | `rate(rtb_gate_errors_total[5m]) > 0` в `alerts.yml`; трекеры — fail-open + `redis_errors_total` |
| GAP-039 | low | ops | Результаты нагрузочных прогонов Go-биддера (≈35k QPS, p99 ≈ 28 мс) не сохранены как артефакт | Прогнать `rtb_load.py` после деплоя R3/R9, сохранить в `docs/audit/loadtest/` |
| GAP-034 | medium | legal | Запрещённые к рекламе категории ККТУ блокируются только вручную на модерации | Таблица `kktu_restricted` + жёсткий стоп на сохранении креатива |
| GAP-035 | low | code | 355 TODO/FIXME; `mypy` объявлен, не в CI; `lib/types.ts` поддерживается вручную (R13) | `mypy` на `tokens/auth/rbac/billing_core`; `openapi-typescript` с diff-check в CI |
| GAP-036 | low | architecture | Дубли логики Python↔Go (shading, prefilter, targeting), `rtb.py` 1240 строк (R7) | Реестр владельцев функций в `parity_matrix.md`, удаление мёртвых веток |

## Принятые решения (accepted)

| ID | Описание | Обоснование |
|----|----------|-------------|
| GAP-020 | Python RTB остаётся в кодовой базе как reference + dev/симулятор | Источник истины для contract-тестов Go; выключен в проде флагом `RTB_PYTHON_SERVING=false` |
| GAP-021 | Rate limit логина fail-open при недоступном Redis | Доступность логина важнее строгости лимита; Redis-даун — отдельный алерт |
