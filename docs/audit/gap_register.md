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

## Принятые решения (accepted)

| ID | Описание | Обоснование |
|----|----------|-------------|
| GAP-020 | Python RTB остаётся в кодовой базе как reference + dev/симулятор | Источник истины для contract-тестов Go; выключен в проде флагом `RTB_PYTHON_SERVING=false` |
| GAP-021 | Rate limit логина fail-open при недоступном Redis | Доступность логина важнее строгости лимита; Redis-даун — отдельный алерт |
