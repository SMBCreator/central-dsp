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
| GAP-022 | high | backend/rtb | Нет предфильтрации входящего SSP-трафика — боты, чужое гео и datacenter-IP проходили полный цикл (Redis+Postgres), сжигая инфраструктуру | Часть C: `backend/prefilter.py` (O(1)-отсев по UA/geo/device-id/CIDR, fail-open) + метрика `rtb_prefilter_drops_total` + admin API `/settings/throttle/subnets` + alert `PrefilterDropRateHigh` |
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
| GAP-018 | low | repo | Java-биддер остаётся в репо (deprecated) — риск случайных правок | Удалить каталог `bidder/` после cutover Go |
| GAP-019 | low | docs | README репо описывает Java как основной биддер | Обновить при merge этой ветки |

## Открытые гэпы по аудиту 2026-09 (`prod_readiness_2026-09.md`)

| ID | Sev | Компонент | Описание | Рекомендация |
|----|-----|-----------|----------|--------------|
| GAP-024 | critical | backend/security | `supply_partners.auth_token` хранится в открытом виде, сравнение `==` (не constant-time), партнёры с `NULL` без авторизации (R1) | Миграция `032_ssp_token_hash.sql`: `sha256(token)` + `hmac.compare_digest`, показ токена один раз при генерации |
| GAP-025 | critical | data | `bid_events` без партиционирования и ретенции; ≈ 86M строк/сутки при 1k QPS (R4) | `RANGE (ts)` по дню, cron drop > 14 d, агрегаты 13 мес., reconcile-алерт сырые ↔ hourly |
| GAP-026 | critical | ops | Бэкап — одна строка `pg_dump` в DEPLOYMENT.md; нет расписания, оффсайта, restore-drill, RPO/RTO (R5) | `ops/backup.sh` (pg_dump + uploads → S3-совместимое, 30 дней), ежемесячный restore на staging, RPO 24 ч / RTO 2 ч |
| GAP-027 | high | frontend/security | Нет CSP; `ignoreBuildErrors: true` при чистом tsc (R6) | `Content-Security-Policy-Report-Only` → enforce через 2 недели; убрать `ignoreBuildErrors` |
| GAP-028 | high | bidder-go | SSP-эндпоинт без лимита тела запроса и per-partner rate limit; `tmax` не пробрасывается в контекст (R9) | `http.MaxBytesReader` 64 KiB, token bucket per partner, `context.WithTimeout(tmax-20ms)` |
| GAP-029 | high | observability | Ошибки Redis в `GateAndReserve` (fail-close по деньгам) не алертятся — тихая потеря трафика; `mark_once` на трекерах бросает 500 при падении Redis (R3) | Правило `rate(gate_errors_total[5m]) > 0`; трекеры — fail-open + `redis_errors_total` |
| GAP-030 | medium | moderation | `html_snippet` HTML5-креативов уходит в `adm` без проверки внешних `<script src>` (R2) | Автопометка на модерации при скриптах не из whitelist CDN |
| GAP-031 | medium | pixel/legal | Пиксель: нет дедупа событий и rate-limit (накрутка целей); сбор `visitor_id` без согласия, нет срока хранения — 152-ФЗ (R10, R11) | Параметр `consent=1`, дедуп (visitor, event, минута), per-site лимит; политика хранения 14 дней |
| GAP-032 | medium | tests | 6 бэкенд-тестов требуют живой Redis без маркера; нет e2e; покрытие Go `handler.go` частичное (R8) | `@pytest.mark.integration`; Playwright-скелет на 3 сценария; coverage-гейт Go ≥ 70% на `internal/rtb` |
| GAP-033 | medium | ops/ord | Вся агрегация в UTC, пояс не подписан в UI; ОРД-акты сдаются по MSK-суткам (R12) | Пояс «по МСК» в UI, выгрузки ОРД по `Europe/Moscow` |
| GAP-034 | medium | legal | Запрещённые к рекламе категории ККТУ блокируются только вручную на модерации | Таблица `kktu_restricted` + жёсткий стоп на сохранении креатива |
| GAP-035 | low | code | 355 TODO/FIXME; `mypy` объявлен, не в CI; `lib/types.ts` поддерживается вручную (R13) | `mypy` на `tokens/auth/rbac/billing_core`; `openapi-typescript` с diff-check в CI |
| GAP-036 | low | architecture | Дубли логики Python↔Go (shading, prefilter, targeting), `rtb.py` 1240 строк (R7) | Реестр владельцев функций в `parity_matrix.md`, удаление мёртвых веток |

## Принятые решения (accepted)

| ID | Описание | Обоснование |
|----|----------|-------------|
| GAP-020 | Python RTB остаётся в кодовой базе как reference + dev/симулятор | Источник истины для contract-тестов Go; выключен в проде флагом `RTB_PYTHON_SERVING=false` |
| GAP-021 | Rate limit логина fail-open при недоступном Redis | Доступность логина важнее строгости лимита; Redis-даун — отдельный алерт |
