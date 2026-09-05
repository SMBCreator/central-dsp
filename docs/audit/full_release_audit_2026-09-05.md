# CentralDSP — независимый аудит готовности к серверу и programmatic-тестированию

**Дата:** 2026-09-05, UTC.

**Проверенная ревизия:** `4a42e95047df189ad0ee5d5a352f5f4e7785c49b` (`4a42e95`).

**Ветка:** `feat/avito-ui-kktu`.

**Режим работы:** аудит, без исправлений прикладного кода, миграций, конфигурации и существующих runbook-файлов. Новый результат работы — этот документ.

## 1. Итоговый вердикт

**NO-GO для публичного production и закупки за реальные деньги.** Наличие работающего кабинета, 507 успешных backend-тестов и зелёного CI не подтверждает целостность полного RTB → billing → analytics → ORD тракта при сбоях и конкурентной работе.

Платформа не является пустым прототипом: реализованы CRUD, аутентификация, роли, модерация, launch gate, Go RTB, Redis Lua-гейты, финансовый журнал, отчётность и существенный набор тестов. Однако найдены не только эксплуатационные вопросы VPS, но и подтверждённые дефекты кода, влияющие на деньги, достоверность статистики, соблюдение настроек закупки и безопасность.

| Сценарий | Решение на проверенной ревизии |
|---|---|
| Сборка frontend, unit/DB-тесты, локальная разработка | **GO**, с оговорками об окружении ниже |
| Копирование репозитория на закрытый QA-сервер | Допустимо, но не равно успешному запуску стека |
| Развёртывание строго по текущим Compose/runbook одной командой | **NO-GO**: O-01, O-02, O-03, R-01 |
| Изолированный функциональный стенд без внешнего SSP и клиентских денег | Условный GO после устранения инфраструктурных препятствий; тестовые данные считать расходными |
| Подключение реального SSP для полноценной приёмки | **NO-GO** до исправления применимых P0/P1 и проверки сквозных контрактов |
| Приёмка 35 000 QPS, p99 ≤ 30 мс | **Не подтверждена**; существующий генератор может выдать ложный PASS |
| Реальная закупка в РФ / реальный ORD-экспорт | **NO-GO**, в том числе из-за mock-ОРД и отсутствия истории версий erid |

**Прежнее утверждение «в коде всё готово, осталось только проверить VPS» этой ревизией аудита опровергнуто.** Старые отметки DONE и комментарии в исходниках не использовались как доказательство работоспособности.

### Как читать приоритеты

- **P0:** блокирует публичный/денежный запуск: обход финансового контроля, потеря бюджетных ограничений, фиктивная маркировка.
- **P1:** исправить до приёмки соответствующего рабочего сценария. Если сценарий исключён из пилота, исключение должно быть технически enforced, а не только записано в инструкции.
- **P2:** улучшение надёжности, UX, эксплуатации; допустимость переноса согласовать явно.
- **S:** подтверждено чтением кода/конфигурации и трассировкой вызовов.
- **R:** дополнительно воспроизведено отдельным экспериментом; где использованы doubles, это указано.
- **T:** результат существующего автоматического теста/сборки.
- **V:** требует проверки на целевой инфраструктуре или у внешнего провайдера; не объявляется пройденным.

Все чекбоксы исправлений в этом документе намеренно остаются открытыми.

## 2. Объём и ограничения аудита

### 2.1. Инвентаризация

В исходной ревизии — **418 tracked-файлов**: `backend/` 142, `frontend/` 100, `bidder-go/` 28, `bidder/` 30, `legacy/` 79, `docs/` 25, `ops/` 4, остальные — корневая конфигурация, CI и deployment. В frontend найдены **26 `page.tsx`**, в backend — **42 файла `test_*.py`**.

Проверены структура маршрутов, зависимости, middleware и права, схемы и ссылки сущностей, финансовый и событийный тракты, feed и targeting, формирование объявлений, Redis-контракты, ORD, пиксель/аудитории/оптимизация, proxy, сборка, основные браузерные сценарии, миграции, backup/restore, preflight, нагрузочный инструмент и алерты. Наиболее глубокая ручная проверка выполнена на критических путях, перечисленных в находках.

`bidder-go/` рассматривается как единственный production bidder. Java `bidder/` и `legacy/` проверены на участие в развёртывании и пересечения со стеком; их полноценная функциональная сертификация не выполнялась. Возврат к ним требует отдельной приёмки.

**Это широкий инженерный аудит, но не заявление о проверке каждой возможной ветви каждой функции.** Все файлы инвентаризированы, выполнены сквозные поиски и целевая ручная проверка; все 418 файлов не объявляются построчно прочитанными. Не проверены все комбинации таргетингов, браузеров, форматов и ролей через UI. Отсутствие находки в модуле не означает доказанное отсутствие дефектов.

### 2.2. Окружение

- Локальная VM: Python 3.13.11, PostgreSQL 18.6 (`dsp_ci`, localhost:5433), Redis 8.10.1 для локальных HTTP-проверок; аварийные Redis-сценарии — `fakeredis`.
- Go: **1.25.14**. Race-тесты также используют miniredis, а не production Redis Cluster.
- Frontend: production `next build` / `next start`, Next.js **16.3.1**, Node **24.16.0**, pnpm **10.34.3** в VM.
- В проекте Node ограничен `>=22 <23`; CI/Docker используют Node 22. Локальная сборка дала предупреждение о версии Node и нескольких lockfile. Это ограничение сопоставимости, не падение сборки.
- CI использует Python 3.12 и PostgreSQL 16, Compose — PostgreSQL 15. Тестирование на PG18 не заменяет PG15-проверку целевой схемы.
- Browser: light, **390×659** и **1440×900**, настоящий локальный FastAPI/Postgres, `ALLOW_MOCK_FALLBACK=0`.
- Тестовый QA-пользователь создан только в локальной `dsp_ci`; после проверки его сессии отозваны, аккаунт заблокирован, пароль заменён случайным. Исходные DB-тесты могут оставлять audit/test-данные по своей существующей логике.

**На production/VPS ничего не запускалось и не изменялось.** Docker daemon/целевой Compose, реальный SSP, ключ/спецификация ОРД, TLS/DNS/firewall, backup restore на отдельном сервере, аварийные остановки рабочих сервисов и 35k QPS здесь не проверялись. Запросов к платному AI/реальному ORD для аудита не отправлялось.

## 3. Фактически выполненные проверки

| Проверка | Результат и предел доказательства |
|---|---|
| Git baseline | Рабочее дерево до аудита чистое, SHA зафиксирован выше |
| GitHub CI | Run `33964958611`, SHA `4a42e95`: backend, frontend, bidder-go, security — success; также success второго запуска на том же SHA |
| Миграции локальной БД | `migrate.py --dry-run`: `Nothing to do: all migrations applied.` Это не новый upgrade/restore-прогон |
| Backend | `pytest tests/ -q --tb=short`: **507 passed, 2 warnings**, 8.52 с после старта локального Postgres; DB-тесты не пропущены |
| Первичный backend-прогон | Был неуспешным из-за остановленного localhost:5433. После запуска PG полный прогон успешен; первичный сбой не записывается как баг продукта |
| Go | `go vet ./...` — PASS; `go test -race -count=1 ./...` — PASS, без test cache |
| Go coverage | cmd/bidder 16.2%; feed 0%; metrics 83.1%; redisx 61.8%; rtb 75.9%; shading 3.1%; targeting 74.2% statements |
| Frontend | TypeScript и production build — PASS, маршруты успешно собраны |
| Зависимости frontend | `pnpm audit --prod --audit-level high`: известных уязвимостей не найдено на момент проверки |
| Зависимости Go | `govulncheck ./...`: 0 достижимых уязвимостей; дополнительно сообщил 1 уязвимость в требуемых модулях, без используемых уязвимых вызовов |
| Python security | `pip-audit` PASS в проверенном CI; локально повторно не запускался. Image-only пакеты Celery/Flower этим manifest-аудитом не покрываются полностью |
| Shell | `bash -n` для backup, restore, preprod — PASS. Синтаксис shell не подтверждает правильность сценария |
| UTF-8 / подозрительные конструкции | Поиск U+FFFD — без находок; целевой поиск ключей/private-key-маркеров не выявил секретов. Совпадение `eval` в cache.py — штатные Redis Lua-вызовы, не Python eval. Полный git-history secret scan не выполнялся |
| HTTP smoke с настоящей сессией | 35 уникальных корректных GET API + `POST /stats/report` дали 200, `x-dsp-mock` отсутствовал. Данные/пустые ответы не доказывают корректность всех бизнес-метрик |
| Негативный HTTP | Без сессии защищённые ручки возвращают 401; `POST /pixel/collect` с JSON-массивом реально вернул 500 |
| Независимые аварийные эксперименты | Воспроизведены E-02/E-03/E-04, M-01/M-02, L-01, S-01, O-04; детали ниже |

Правильный список пикселей — **`GET /pixels`**. Пробные `/pixel` и `/pixel/pixels` вернули 404: это ошибка адреса в исследовательском запросе, не дефект маршрута приложения.

### 3.1. Browser-проверка

- Реальный вход через `/login`, HttpOnly-cookie; logout; повторное открытие `/campaigns` после выхода перенаправляет на `/login`.
- `/`: мобильный дашборд, пустое состояние KPI/графика, навигация.
- `/campaigns/new`: desktop, выбор баннера, переход «Формат → Основное»; дальнейшая отправка формы не выполнялась.
- `/campaigns/[id]`: переход из общего списка под superadmin воспроизвёл «Кампания не найдена» для чужой кампании — S-03.
- `/supply`, `/analytics`, `/creatives`, `/audit`, `/settings`: отрисовка и доступные элементы проверены на реальном API; desktop document overflow не обнаружен на проверенных экранах.
- Мобильное меню открывается, но Escape его не закрывает, фокус остаётся на кнопке открытия — F-01.
- На прочих страницах выполнены сборка/контрактная/API-проверка, но не полный ручной браузерный сценарий каждого CRUD.

Лабораторные Web Vitals `/`, production, 1440×900, локальная сеть: **TTFB 19.7 мс, FCP 240 мс, LCP 336 мс, CLS 0**. INP и hydration timings инструмент не предоставил (`null`); они не объявляются пройденными. Это не SLO VPS и не field-метрики.

Скриншоты в текущей VM, не в репозитории: `/tmp/agent-browser/dsp-audit-login-mobile.png`, `dsp-audit-dashboard-mobile.png`, `dsp-audit-admin-campaign.png`, `dsp-audit-campaign-wizard-desktop.png`, `dsp-audit-supply-desktop.png`, `dsp-audit-mobile-menu.png`. Эти временные артефакты могут исчезнуть при пересоздании VM.

## 4. Что уже реализовано и не требует переписывания с нуля

| Контур / файлы | Подтверждённая основа | Оговорка |
|---|---|---|
| `auth.py`, `routers/auth_router.py` | bcrypt, случайные серверные сессии, HttpOnly/Secure-cookie, проверка срока и блокировки, logout, login limiter | S-01, S-04, F-01 |
| `rbac.py`, `users_router.py`, IDOR/RBAC-тесты | 9 ролей, guards, client permissions, иерархия пользователей, значительное негативное покрытие | S-02/S-03: не все object/field scopes согласованы |
| `advertisers`, `campaigns`, `groups`, `line_items` | CRUD, ownership базовых сущностей, диагностика запуска, дублирование кампаний | M-01, S-02/S-03, R-03 |
| `creatives`, `moderation`, upload | Pending/approved flow, повторная модерация части содержимого, magic-byte validation, лимиты изображений/видео, sandbox HTML-preview | L-02, R-05, O-02 |
| `ord/service.py` | erid gate в feed для РФ, ККТУ до регистрации, связка с договором, статусы ошибок | L-01–L-03: не завершён реальный legal lifecycle |
| `billing_core.py`, migration 010, money.py | Decimal, clamp clearing price, транзакция win→ledger→balance, UNIQUE для повторного bill_win | M-01–M-06, E-02: это не весь денежный контур |
| Go `targeting`, `models`, `partner_auth` | Parsing, multi-imp, 204/no-bid, размер тела, targeting, bcat/badv/battr, SHA-256 partner token, QPS, HMAC events | R-01–R-05, S-06 |
| Go `redisx` | Атомарный reservation кампании/line item, concurrent-тесты, clamp возврата | M-03–M-06: сохранность ключей и жизненный цикл резерва |
| Go `feed`, `metrics`, entrypoint | Периодический immutable snapshot, endpoint метрик, recover, graceful HTTP shutdown | R-02, O-05/O-07 |
| `events_consumer.py` | Батчинг, transaction на batch, ACK после записи, исключение test-bids из hourly | E-03: нет надёжной обработки failure/replay |
| `stats`, `scoring`, SSP stats | Summary/timeseries/report, whitelist динамического SQL, domain/SSP-срезы, CSV и фильтры | E-01, S-03, F-02 |
| `pixel`, `audiences`, `pixel_guard` | Beacon, цели, conversion dedup, clid bridge, базовый антифлуд и сегменты | E-05, R-06 |
| `rules_engine`, `optimizer`, `ml`, `shading` | Реальные расчёты и сохранение результатов, Python-тесты | Не все результаты доходят до production Go — R-03 |
| `notifications`, `audit` | Уведомления пользователя; журнал мутаций и admin UI/CSV, append-only guard | Audit best-effort, не транзакционный финансовый аудит; O-05 |
| `geo` | Защищённый справочник поиска с query/limit validation | Названия/коды надо сверить с гео-значениями реального SSP |
| Frontend shell/API/forms | Серверная cookie-auth, типизация, SWR, обработка ошибок, mock-off в production, готовая навигация и страницы | R-01, S-03/S-05, F-01/F-02 |
| `migrate.py`, CI | Порядок миграций, sha256, advisory lock, fresh-schema/idempotency в CI | O-06; fresh install не заменяет upgrade под трафиком |

## 5. Деньги и бюджет

### M-01 — P0 — баланс меняется в обход финансовых прав и журнала [S, R]

**Где:** `backend/schemas.py:AdvertiserIn/AdvertiserPatch`, `routers/advertisers.py:create_advertiser/update_advertiser`, `rbac.py:CAMPAIGN_WRITE`.

Создание и PATCH рекламодателя принимают `balance`. Guard — `require_campaign_write`, разрешающий manager, advertiser и agency, а не только finance/admin. Значение напрямую попадает в INSERT/UPDATE; запись в `transactions` не создаётся. Приватность рекламодателя не спасает: пользователь может увеличивать свой баланс и закупать больше оплаченного.

**Воспроизведение:** настоящий router с dependency override только личности `advertiser` и fake DB получил PATCH balance=999999 → HTTP 200; SQL содержит `balance =`, вызовов ledger INSERT — 0. Production-данные не менялись.

- [ ] Убрать balance из нефинансовых CRUD; начальный баланс — 0. Пополнения/корректировки — только через финансовую операцию с аудитом и идемпотентностью. Добавить HTTP-тест: manager/advertiser/agency не могут ни создать положительный баланс, ни изменить его.

### M-02 — P1 — deposit/adjust неатомарны и неидемпотентны [S, R]

**Где:** `backend/routers/billing.py:deposit/adjust`.

UPDATE баланса и INSERT транзакции — отдельные pool-вызовы без общей transaction. После успешного UPDATE падение INSERT оставляет деньги без проводки; retry может начислить повторно. Входные суммы — float, а не нормализованный money-type. Финансовый guard не решает атомарность.

**Эксперимент с fake DB:** исходный баланс 100, deposit 50, отказ ledger INSERT → баланс 150, проводок 0.

- [ ] Общая DB transaction, нормализация Decimal, ключ идемпотентности пополнения/корректировки, проверка concurrent/retry/DB-failure. Сумма остатков должна восстанавливаться из журнала.

### M-03 — P0 — Redis может забыть бюджет и события, backend/Go могут попасть в разные Redis [S, V]

**Где:** `docker-compose.yml:redis/backend/bidder-go`, `redisx.go:GateAndReserve`, `cache.py`, `events_consumer.py`.

Compose задаёт `--maxmemory 512mb --maxmemory-policy allkeys-lru`, без явно закреплённого Redis volume/AOF. В одном хранилище находятся budgets, dedup, stream, bidmeta, frequency и clidmap. Eviction бюджетного ключа превращается в новый нулевой расход; потеря stream/group/meta повреждает учёт. Возможный anonymous volume образа не является явно управляемой стратегией восстановления.

Backend получает `REDIS_URL`, Go — `REDIS_URL_BIDDER` с дефолтом на локальный Redis. `.env.example` предлагает внешний Redis: без дополнительной настройки два сервиса могут читать разные базы, что ломает callbacks и consumer.

- [ ] Единый проверяемый Redis endpoint/DB для контрактных ключей; запрет eviction денежных ключей; обоснованная persistence/backup/replication; разделение cache и критического state либо иной надёжный дизайн. Проверить OOM, recreate, рестарт и восстановление фактического расхода до разрешения торгов. Не использовать слепой FLUSHDB как восстановление бюджета.

### M-04 — P0 — два сброса дневного бюджета: 21:00 и 00:00 UTC [S]

**Где:** `vercel.json` и `docs/DEPLOYMENT.md` расписание reset-daily в 21:00 UTC; `routers/cron.py:reset_daily_budgets`; `cache.py:_seconds_until_utc_midnight`; Go `secondsUntilUTCMidnight`.

Cron удаляет `spend:*` в московскую полночь, но вновь созданные ключи истекают в 00:00 UTC. Получаются два обнуления, и расход может превышать согласованный дневной лимит. Повтор cron не идемпотентен по бизнес-дню: может стереть уже набранный расход текущего дня.

- [ ] Выбрать единую business timezone, дату в ключе и семантику закрытия дня. Тесты 20:59→21:00→23:59→00:00 UTC, повтор/опоздание cron, параллельные bids/wins при смене дня. Лимит не должен открываться повторно.

### M-05 — P1 — нет атомарного резерва общего баланса и total budget [S, V]

**Где:** `bidder_feed.py:campaigns_feed`, Go `feed.Candidate`, `GateAndReserve`, `billing_core.py:bill_win`.

Feed фильтрует `adv.balance > 0` и `spent_total < total_budget` только на момент загрузки. Lua резервирует дневной budget кампании/line item, но не доступный баланс рекламодателя и не остаток total budget. bill_win затем вычитает сумму и ставит кампании на паузу уже после списания. Между feed refresh и поздними wins обязательства могут существенно превысить предоплату; row lock гарантирует последовательность, но не верхнюю границу обязательств.

- [ ] Резервировать все финансовые ограничения на bid-time либо определить и технически ограничить допустимый кредитный риск. Тест: малый advertiser balance, несколько кампаний, большой daily budget, конкурентные bids и задержанные win notices; контролируемый overdraft, а не только «пауза после минуса».

### M-06 — P1 — резерв не имеет надёжного per-bid жизненного цикла [S]

**Где:** Go `handler.go:bidOnImp/persistBidMeta/Loss`, `redisx.go:ReleaseGate`, backend `win_notice`.

Резерв снимается на lurl, но нет показанного sweeper для проигрышей без уведомления, reconciliation разницы bid price/clearing price и единого terminal state win/loss. `lossdone` ставится до возврата, ошибка возврата игнорируется: retry может быть уже подавлен. После полуночи поздний loss способен уменьшить новый расход по тому же ключу, если ключ уже пересоздан; clamp >=0 предотвращает отрицательное значение, но не возврат чужого резерва. Win и loss одного bid не согласованы между Go/DB.

- [ ] Per-bid reservation с датой, суммой и атомарными переходами reserve→win/loss/expire; безопасный refund, retry и корректировка clearing. Тесты lost/duplicate/out-of-order notices, midnight, failed refund, expired metadata, два bidder-инстанса.

## 6. События, отчётность и пиксель

### E-01 — P1 — расход в raw-отчётах берётся не из тех событий; supply-срезы неполны [S]

**Где:** `rtb.py:win_notice/impression/_log_event`; `stats.py:report` около 485; `supply_partners.py:_METRIC_COLS`; `scripts/convergence_check.py:_EVENTS_SQL`.

Clearing price записывается в событие **win**, а `_bump_stats(..., 'wins', cost, ...)` увеличивает hourly spend. Impression handler не передаёт clear_price. Между тем raw report, SSP-card и convergence checker суммируют clear_price у **impression**. На нормальном денежном потоке hourly содержит расход, raw-отчёт показывает ноль/недобор, checker объявляет расхождение.

Тест `test_convergence_check_db.py` вручную создаёт impression с clear_price, то есть моделирует не реальный handler. Он проверяет сравнение искусственно согласованных данных, не работоспособность pipeline.

Кроме того, publisher/tagid/bundle пишутся на bid, но не прокидываются `_log_event` в win/imp/click, а loss берёт сокращённый bidmeta. В группировках win/imp/click оказываются в «—», поэтому CTR/win-rate по конкретному inventory недостоверны.

- [ ] Единая семантика spend: billable win либо другой согласованный ledger-event, одинаковая во всех отчётах. Перенос измерений bid на последующие события или корректный join по bid. Сквозной тест реальных handlers: bid→win→imp→click→summary/report/SSP card/convergence с ненулевым расходом и inventory dimensions.

### E-02 — P1 — commit денег не атомарен с учётом win; dedup может навсегда потерять показ [S, R]

**Где:** `rtb.py:win_notice` 1124–1146, `impression/viewable_impression/video_event/click`; `cache.py:mark_once`.

bill_win коммитится раньше win log и stats. Если последующая запись падает, повтор win попадает в duplicate-return и не восстанавливает статистику. Для impression/click/view Redis `mark_once` ставится до DB-записи, иногда даже до нахождения bid: временная ошибка после SETNX превращается в постоянную потерю учёта до истечения dedup TTL.

**Эксперимент с настоящими handlers и подставными DB/billing-результатами:** после успешного billing и ошибки event write повтор win вернул `duplicate=True`, event_attempts=1, stats_writes=0. При отказе impression write после dedup повтор вернул пиксель HTTP 200, не повторив DB-запись. Сам DB commit в этом эксперименте моделировался, а не проверялся на production.

- [ ] DB inbox/outbox или атомарный ledger+event+aggregate transaction; dedup подтверждать после надёжной записи. Failure injection после каждого шага; retry должен восстанавливать запись ровно один раз, без двойного списания.

### E-03 — P1 — Redis Stream consumer теряет pending и дублирует агрегаты [S, R]

**Где:** `backend/events_consumer.py:process_once/run_forever/_ensure_group/_save_cursor`.

1. После DB-ошибки при `from_id='>'` сообщение остаётся pending, но цикл продолжает читать `>`; recovery PG не перечитывает эту запись автоматически.
2. Pending читаются только для `CONSUMER=HOSTNAME`; новый Docker-контейнер получает другое имя. Нет XAUTOCLAIM/XCLAIM чужих зависших сообщений.
3. Crash после DB COMMIT, до XACK: повторный batch снова INSERT events и `bids += n`. Уникального inbox/stream-id нет. «At-least-once» не равно exactly-once aggregation.
4. При первой группе старт с `$` пропускает уже записанные события. `_ensure_group` вне retry-loop: ошибка на старте может завершить фоновую задачу. Cursor сохраняется отдельно и только каждые 5000 событий, не атомарен с commit.
5. Stream maxlen=200000 ограничивает окно восстановления: при 35000 **bid events/s** это примерно 5.7 с; при 350 events/s — примерно 9.5 мин. Важен bid-rate, а не только request QPS.

**Воспроизведено на настоящем consumer с fakeredis/FakePool:** PG down→up: new_read=0, pending=1; смена consumer: pending_read=0; потеря ACK и replay одного bid: counted_bids=2.

- [ ] Надёжный inbox/dedup по stream event ID, claim зависших consumers, возврат к pending после ошибки, безопасный bootstrap и checkpoint. Контролировать backlog/lag/trim loss. Тесты crash-before/after-commit, restart с новым HOSTNAME, poison event, Redis replacement и consumer outage длиннее stream window.

### E-04 — P1 — test-трафик изолирован лишь частично; UI-тестер не тестирует Go [S, R]

**Где:** `routers/simulator.py`, `rtb.py:click/viewable_impression/video_event`, `pixel.py:_match_goals`, `frontend/app/rtb-test/page.tsx`.

Go test=1 не резервирует budget, backend test-win/test-imp исключаются из hourly — это сделано. Но click/view/video не проверяют `is_test`: пишут события как production и увеличивают агрегаты. Pixel conversion attribution также не исключает test bid.

Симулятор напрямую вызывает Python `process_bid_request`, не задаёт test/is_test и генерирует `cur=['USD']`, тогда как production Go — RUB. Следовательно, нулевые bids симулятора могут быть артефактом валюты, а успешная симуляция при достижении billing — загрязнять деньги/статистику. «Реальный Go-тест» в SSP-autocheck и `/simulator/run` — разные пути.

**Воспроизведено с подставленным test bid:** click пишет hourly 1 раз, view 1 раз, video start 2 раза; `logged_is_test=False`.

- [ ] Сквозной test-флаг до click/view/video/pixel/conversions/ORD/ML. UI-тестер должен обращаться к Go с корректной валютой. Отдельные load fixture/tenant; нулевое изменение клиентского баланса и production aggregates после полного тестового funnel.

### E-05 — P1 — pixel routing сломан в рекомендуемой топологии; ingestion недостаточно защищён [S, R]

**Где:** `pixel.py:tracking_script/collect/_match_goals`, `frontend/app/pixels/page.tsx:snippet`, Caddy из `docs/DEPLOYMENT.md`.

UI вставляет `/api/pixel/script/{id}.js`, а скрипт вычисляет отправку на `{request.base_url}/pixel/collect` без `/api`. Caddy направляет в backend только `/api/*`, `/pixel/collect` попадает во frontend. За proxy base_url также может иметь внутреннюю схему/host. Воспроизведён generated URL `https://dsp.example/pixel/collect`.

`collect` принимает неограниченное request.body до антифлуда, ожидает dict без проверки; JSON `[]` даёт AttributeError/HTTP 500. `sw` и `dur` приводятся через int без полноценной валидации. Проверка pixel в DB происходит раньше антифлуда. Goal attribution ищет bid по clid без привязки к владельцу/кампании pixel и без подтверждения разрешённой атрибуции.

- [ ] Канонический публичный API URL/prefix; реальный sendBeacon через внешний HTTPS. Схема и body limits до тяжёлой работы, 204/4xx на некорректные типы, rate limit до DB. Проверить clid ownership, attribution window, test exclusions, домен пикселя и replay.

## 7. ОРД и целостность креативов

### L-01 — P0 для РФ — mock-ОРД разрешён в production [S, R]

**Где:** `ord/adapter.py:get_ord_settings/get_ord_adapter`, `startup_checks.py`, `ord/service.py:ERID_FEED_GATE_SQL`.

При отсутствии настроек фабрика возвращает MockOrdAdapter независимо от APP_ENV. Startup checks не требуют действующий ORD provider. Feed проверяет непустой erid, не происхождение: mock-token удовлетворяет этому условию. Ошибки чтения DB settings проглатываются и могут привести к fallback. UI показывает mock-режим, но это предупреждение, а не запрет открутки.

**Воспроизведение:** `APP_ENV=production`, фабрика с пустыми настройками → provider `mock`.

- [ ] Явный dev/test-only mock; в production отказ регистрации/доставки РФ без верифицированного provider. Существующие mock erid не должны стать законными после добавления API-ключа: предусмотреть re-registration и контроль provenance.

### L-02 — P1 — переиздание erid уничтожает историческую связь; часть изменений не инвалидирует маркировку [S]

**Где:** migration `012_ord.sql` UNIQUE(creative_id), `ord/service.py:register_creative`, `creatives.py:_CONTENT_FIELDS/update_creative`, `cron.py:ord_monthly_export`.

Ord creative хранится одной строкой; повторная регистрация делает upsert нового erid поверх старого. Месячная статистика агрегируется по creative_id и join текущей ord_creatives. Показы старой версии могут уйти с новым erid; `superseded` не создаёт историю. Смена `counterparty_id`, `contract_id`, `kktu_code`, части трекеров и других metadata не входит в `_CONTENT_FIELDS`, значит не обязательно снимает прежнее approval/erid. Старый custom VAST с `<Erid>` вообще не переписывается Go-вставкой.

- [ ] Версионирование креатива/регистрации с неизменяемым erid и привязкой событий к версии; сохранение истории договора и маркировки. Тест: показы до и после изменения в одном месяце распределены между правильными erid. Юридически значимые metadata и трекеры проходят повторную валидацию/модерацию.

### L-03 — P1 — реальный ORD-протокол и семантика acceptance не подтверждены [S, V]

**Где:** `ord/ozon.py`, `routers/ord_router.py:register_contract`, `cron.py:ord_monthly_export`.

Сам клиент содержит требование финально сверить endpoint/payload со спецификацией. Регистрация договора не передаёт внешние ID сторон в текущем payload. `submit_stats` подставляет `accepted=len(rows)`, если поле отсутствует; cron помечает весь export ok без проверки частичного принятия. Предварительная проверка already-exported не защищает от двух concurrent cron, и успешный export блокирует исправленный повтор без специального механизма. Пустая выгрузка также фиксируется как ok.

- [ ] Контрактные tests по актуальному provider schema, sandbox smoke с реальными разрешёнными данными, проверка per-row acceptance, идемпотентность у провайдера, коррекции/дозагрузки, distributed lock. Проверить договоры, акты/статистику и дедлайны с ответственным за маркировку; это не юридическое заключение.

## 8. Безопасность и права

### S-01 — P1 — cookie SameSite=None без CSRF-защиты [S, R]

**Где:** `auth_router.py:_set_cookie`, `main.py`, mutating routers, Next catch-all proxy.

SameSite=None нужен preview, но действует и в production. Не обнаружена Origin/CSRF-token проверка. Некоторые действия принимают POST без JSON body, например moderation approve, duplicate, ORD register, token rotation. Такой POST может быть отправлен cross-origin HTML-формой; запрет чтения ответа CORS не предотвращает мутацию.

**Эксперимент:** moderation router с авторизованной admin identity (dependency override) принял `Origin: https://untrusted.example`, `Content-Type: application/x-www-form-urlencoded`, пустое тело → 200 и UPDATE. Это доказательство отсутствия Origin guard в handler, не проведённая атака на внешний сайт.

- [ ] Production cookie policy + CSRF/Origin validation для всех state-changing маршрутов, включая bodyless POST и logout; preview-исключения ограничены точными origins. Браузерный cross-site regression test должен показать отсутствие изменения данных.

### S-02 — P1 — ownership проверяется не на всех ссылочных полях [S]

**Где:** `campaigns.py:create/update`, `creatives.py:create/update`, `ord_router.py:create_counterparty/create_contract`, `scoring.py:create/update`.

Основной объект часто защищён user_id, но передаваемые foreign IDs не всегда проверяются: campaign group/pixel/goal/scoring_profile; CreativePatch counterparty/contract; CreativeIn contract; counterparty advertiser; parent_contract; profile pixel/goal. Проверка собственных client/contractor при создании ORD-договора есть — проблема не в этих двух полях. SQL FK гарантирует существование, но не tenant ownership.

Особенно опасна подмена counterparty на уже approved creative: feed join может взять чужие сведения рекламодателя. Полная эксплуатация каждого поля в живой БД здесь не выполнялась; отсутствие проверок подтверждено статически.

- [ ] Проверять ownership и совместимость цепочки сущностей в каждой create/patch-операции, по возможности усилить composite FK. Расширить IDOR-тесты на все ссылочные поля и PATCH, не ограничиваться ID в URL.

### S-03 — P1 — неодинаковые scopes в списках, карточках, финансах и гостевых отчётах [S, R]

**Где:** `rbac.py`, `campaigns.py`, `advertisers.py`, `billing.py`, `stats.py`.

- admin/analyst/finance видят все campaigns/advertisers в списке, но detail/update многих ручек требуют собственный user_id. **В браузере superadmin открыл видимую чужую кампанию → «Кампания не найдена».**
- Billing для finance/admin читает/изменяет только собственные advertiser rows, несмотря на заявленную платформенную финансовую роль.
- Agency visibility определена в recursive helper, но не единообразно используется CRUD.
- Viewer scope содержит advertiser_id, однако GET `/stats/domains` его отбрасывает (`_adv`); summary counts также считают сущности всего owner. UI может не показывать этот endpoint, но cookie позволяет обратиться напрямую. POST `/stats/report` для viewer блокируется общим readonly guard — не следует ошибочно считать его доступным гостю.

- [ ] Единая спецификация read/write scope для роли и endpoint, отдельная проверка всех viewer GET на advertiser boundary. Контрактный тест list→detail→allowed action для admin/finance/agency/client/viewer. Не исправлять простым снятием ownership-фильтров.

### S-04 — P1/P2 — небезопасные bootstrap/config и края жизненного цикла auth [S, R]

**Где:** `auth_router.py:register/change_password`, `schemas.py`, `startup_checks.py`, Go `tokens.go`.

Bootstrap делает count(users)==0 и INSERT без сериализации: два запроса могут оба получить superadmin; первый публичный пользователь захватывает управление, если bootstrap не изолирован. Дефолт регистрации — open; неизвестная строка REGISTRATION_MODE также фактически открывает регистрацию. Production startup не запрещает `RTB_ALLOW_UNSIGNED_EVENTS=1`, отключённые launch/feed gates; Go не выполняет полную fail-fast проверку длины/плейсхолдера секрета на старте, несмотря на комментарий `.env.example`.

Пароль допускает 128 символов, bcrypt ограничен 72 байтами: в текущем окружении **валидный RegisterIn с 73 ASCII-символами → ValueError hash_password**. Смена пароля не отзывает остальные сессии. Проверка bcrypt синхронна в async handler.

- [ ] Защитить bootstrap одноразовым provisioning-процессом/lock; enum и безопасный default registration; запрещать unsafe flags в prod; одинаковые проверки секретов у сервисов.
- [ ] Согласовать password byte limits/хэширование, избежать 500 на длинных и кириллических паролях; отзыв сессий при смене пароля и password reset; вынести CPU-bound bcrypt из event loop.

### S-05 — P1 — публичные платные AI endpoints без серверной авторизации [S, R]

**Где:** `frontend/app/ai-api/creative/route.ts`, `ai-api/insights/route.ts`.

AppShell скрывает страницу, но сами Next Route Handlers сразу читают body и вызывают generateText. Нет проверки cookie/session/role, rate limit, квоты и ограничения размеров product/stats. Python middleware эти маршруты не защищает. Пустой запрос без cookie получил 400 валидации, а не 401; запрос с содержимым, запускающий платную генерацию, намеренно не отправлялся.

На VPS нельзя предполагать Vercel-managed Gateway identity без отдельной конфигурации окружения. Compose не задаёт frontend параметры аутентификации Gateway для самостоятельного VPS.

- [ ] Серверная проверка существующей сессии, квоты, лимиты input/output/стоимости и rate limit; безопасно отключить функцию на стенде без провайдера. Проверить VPS auth по документации провайдера, без хранения секретов в браузере.

### S-06 — P1 — revoke SSP token открывает endpoint; IP-фильтр доверяет внешним заголовкам [S]

**Где:** `supply_partners.py:create_partner/revoke_token/update_partner`, Go `partner_auth.go`, `feed.go:ParseIPWhitelist`, Compose ports.

Пустой hash означает «проверка токена не требуется». Нового партнёра можно перевести в testing/active без токена; revoke удаляет hash и тем самым отключает auth, а не запрещает старому SSP торги. Некорректные IP-whitelist элементы отбрасываются; весь некорректный список превращается в allow-any. Go доверяет X-Forwarded-For/X-Real-IP любого клиента, а `8090:8090` опубликован на всех интерфейсах.

- [ ] Явный auth-required/disabled статус, revoke закрывает приём либо атомарно ротирует секрет; validate CIDR на сохранении, fail-closed при некорректном списке. Закрыть внутренние порты и доверять forwarded headers только доверенному proxy. Тест прямого обращения к bidder с подставным XFF.

## 9. Production Go-биддер и SSP-контракт

### R-01 — P1 — кабинет выдаёт SSP URL Python-биддера [S, R]

**Где:** `frontend/app/supply/page.tsx:copyEndpoint` около 190 и инструкция около 233; `docs/DEPLOYMENT.md` Caddy; `.env.example`.

Копируется `{origin}/api/rtb/bid/{key}`. В рекомендуемой топологии `/api/*` идёт в Python, `/rtb/*` — в Go. Значит SSP получает reference-path, а не проверяемый production bidder. При RTB_PYTHON_SERVING=false это отказ, при текущем true — реальные торги другим движком. В браузере показана та же ошибочная инструкция.

- [ ] Единый публичный BIDDER_BASE_URL/route contract во frontend, автопроверках, runbook и нагрузочном инструменте. Copy endpoint должен попадать именно в Go; Python bid отключён в prod, callbacks backend остаются доступны. Проверить запрос по буквально скопированному URL через внешний HTTPS.

### R-02 — P1 — бессрочный stale feed и ненадёжная готовность bid metadata [S]

**Где:** Go `feed.go:refresh`, `handler.go:Bid/persistBidMeta/FeedReload/Healthz`.

При ошибке refresh старый snapshot используется без max-age: pause, budget change, блокировка SSP, token rotation могут не применяться неограниченно долго. Healthz остаётся HTTP 200 даже с Redis=false; FeedReload возвращает ok после неудачного refresh. Ошибки SET bidmeta/bidfreq/clidmap игнорируются, emit-event error только логируется, после чего bid всё равно может уйти SSP. Так можно принять обязательство без надёжной возможности идентифицировать и учесть callback.

- [ ] Явный max-feed-age и readiness/no-bid policy; реальный outcome reload. Не отдавать оплачиваемый bid без достаточного durable bookkeeping либо гарантированного восстановления. Тесты feed outage, invalid feed, failed SET/XADD, истёкший контекст, pause/rotate во время outage.

### R-03 — P1 — часть настроек кабинета не исполняется production Go [S]

**Где:** `bidder_feed.py`, Go `feed.Candidate`, `handler.go:bidOnImp/matchCandidate`; Python `rtb.py`, `optimizer.py`, `scoring.py`, `prefilter.py`, `ml/`.

- `Pacing` и `BidType` загружаются, но не участвуют в расчёте Go. Цена берётся как `c.BidPrice`, резерв — price/1000, то есть CPC не имеет отдельной реализации, even не обеспечивает равномерность.
- Feed не переносит optimizer_blocks, scoring enforce/manual blocks, clean_traffic_only и pCTR-модель; matchCandidate их не читает. Реальный расчёт и видимый UI блокировки не означают блокировку в Go.
- Python datacenter/bot prefilter также не является автоматически частью Go-path.
- Шейдинг в Go есть: не следует приравнивать его к отсутствующему pCTR/optimizer enforcement.

- [ ] Составить executable parity matrix «настройка UI → persisted field → feed → конкретный Go decision». Реализовать нужные возможности либо запретить выбор неподдерживаемых режимов. Проверять не JSON-ключи, а изменение реального BidResponse/no-bid. Для первого пилота явно ограничить продукт supported CPM-path.

### R-04 — P1 — частичная совместимость OpenRTB, особенно PMP/native/video [S, V]

**Где:** Go `models.go`, `handler.go:bidOnImp/typeMatches`, `adm.go`.

BidRequest.cur проверяется на RUB, но imp.bidfloorcur/deal.bidfloorcur не используются для сопоставления цены. Deal floor, private_auction и deal auction type представлены в model, но не участвуют в показанном выборе/цене. Seat в handler фиксирован, onboarding seat_id не передаётся в feed. Это не полная поддержка полей SSP-анкеты.

Native assets генерируются с фиксированными id 1–5 без согласования с requested asset IDs/required/len. Для видео `typeMatches` не проверяет media MIME, допустимую фактическую длительность/совместимость креатива с протоколом; наличие targeting.video_protocols не равно проверке VAST ответа. В banner-path есть реальная проверка размеров.

- [ ] Contract fixtures от каждого SSP: currencies/floors, first/second-price, PMP/private auction, seat, native assets, video MIME/protocol/duration, secure slots, malformed JSON/multi-imp. Неподдерживаемые запросы — явный no-bid, не заведомо невалидная ставка. Уточнить billable event nurl/burl/render по договору SSP.

### R-05 — P1 — ошибки adm/click/VAST и неполная viewability-инструментация [S]

**Где:** Go `adm.go:BuildAdm/injectVastExtensions`, backend `rtb.py:click`, creative URL schemas.

Click URL вложен в `r=` без URL-encoding: `&` в landing query обрезает/переносит параметры. Сырой URL используется в HTML-атрибутах и JS-трекерах без контекстного escaping; валидатор URL отсутствует. В VAST `c.Name` не XML-escaped, duration 90 формируется как `00:00:90`, хотя длительность разрешена до 600 с. Custom VAST получает лишь extensions, без гарантированной вставки наших trackers; HTML snippet не получает автоматический click wrapper.

Go banner/native не генерируют полноценный viewability-beacon, хотя backend поддерживает `/rtb/view`. Video start приравнивается к viewable impression без измерения видимости — это не подтверждённая MRC viewability. `click?r=` допускает произвольный http(s) redirect с валидным bid token.

- [ ] URL builder и контекстное HTML/XML/JS escaping, проверка URL schemes/targets; VAST parse/validation вместо хрупкого string insertion, корректный duration. Инструментировать согласованные impression/click/view/video trackers для каждого формата; тесты `&`, quotes, XML-символов, длинного видео и custom VAST. Не подписывать start-based показатель как измеренную viewability.

### R-06 — P2, P1 для ретаргетинга — frequency и audience TTL имеют ограничения [S]

**Где:** `GateAndReserve`, backend impression handler, `audiences.py:add_member/_segment_matches`, `pixel.py:_match_goals`.

Frequency проверяется на bid-time read-only, увеличивается только на impression: конкурентные in-flight bids одного пользователя могут превысить cap. Это не доказанный hard cap при высокой конкурентности. Redis audience SET имеет TTL на весь сегмент, каждый новый member продлевает TTL всех старых; individual `audience_members.expires_at` при SISMEMBER не проверяется. Goal-segment не матчится `_segment_matches`, а `_match_goals` не добавляет его участников. После утраты Redis не виден автоматический rebuild membership из DB.

- [ ] Зафиксировать hard/soft frequency cap и допустимое превышение; при необходимости in-flight reservation. Per-member expiry/rebuild/cleanup, рабочий goal-segment hook; тесты истечения старого member при непрерывном притоке новых и восстановления Redis.

## 10. Сервер, миграции, нагрузка и мониторинг

### O-01 — P1 — Compose и runbook содержат неработающие компоненты [S, V]

**Где:** `docker-compose.yml`, `backend/Dockerfile`, `.env.example`, `docs/runbooks/release.md`.

Compose по умолчанию поднимает worker/beat/flower с `celery -A celery_app`, но `backend/celery_app.py` отсутствует: он только в legacy. Dockerfile действительно устанавливает celery/flower — проблема не в отсутствии бинарника, а в отсутствующем приложении. Postgres монтирует несуществующий tracked `./backend/db/init.sql`; текущая схема находится в migrations. Поведение отсутствующего bind-source необходимо проверить реальным Compose, но зависимость на отсутствующий файл уже подтверждена.

В `.env.example` смешаны external Neon/Redis и локальный Compose, отсутствуют важные локальные параметры, EVENT_TOKEN_SECRET продублирован, RTB_PYTHON_SERVING=true. Backend имеет source bind mount для dev; API/frontend/bidder порты открыты на 0.0.0.0. Публичный EVENT_BASE_URL по умолчанию может стать `http://backend:8000`, недоступным SSP/браузерам.

- [ ] Одна воспроизводимая production topology: удалить/профилировать неиспользуемую Celery-ветку либо реализовать нужный scheduler; привести mounts/env/ports/service names к действительности. Fresh install на пустой VM с `docker compose config`, build, migrations, health/readiness и проверкой публичных callback/media URL.

### O-02 — P1 — backup и restore несовместимы; uploads не бэкапятся [S, R частично]

**Где:** `ops/backup.sh`, `ops/restore.sh`, release/deployment docs.

- Backup создаёт `pg/dsp-*.dump`, restore ищет `postgres-*.dump` в другом layout или tar.gz. Передача `.dump`, указанная в release runbook, не подходит интерфейсу restore.
- В backup нет архива uploads, хотя runbook обещает PG+Redis+uploads.
- Weekly verify SELECT использует `ledger`, в схеме есть **transactions**. Read-only SQL подтвердил: to_regclass(ledger)=NULL.
- Restore проверяет `schema_migrations.version`; реальные колонки — filename/sha256/applied_at/stamped, это подтверждено SQL.
- Restore использует жёсткий container name, старый service `bidder`, советует FLUSHDB с обещанием пересчёта счётчиков, которого Go не делает автоматически.
- `pg_restore --jobs=4` читает dump через stdin; parallel restore требует подходящий seekable archive/file, а не такой pipe.
- SHA выводится, но не сравнивается с сохранённым manifest/checksum как критерий допуска. Offsite по умолчанию отсутствует.

- [ ] Единый backup artifact contract, uploads+DB consistency, проверка checksum, корректный restore-tool/file/container и SQL. Реальная репетиция backup→restore на чистой БД/volume с контрольными балансами, количеством событий, изображениями и RPO/RTO; только после неё считать recovery готовым.

### O-03 — P1 — `preprod_check.sh` не является надёжным release gate [S]

**Где:** весь `ops/preprod_check.sh`, `scripts/convergence_check.py`, release runbook.

- Default BIDDER_HOST=8080, Go в Compose=8090; DB defaults dsp/dsp против centraldsp; FRONT из HTTPS HOST может стать ошибочным `:3000`.
- Feed parser ищет campaigns/items, но API возвращает **candidates**. Ответ с 0 или даже JSON-ошибкой может стать PASS: HTTP status и ненулевой размер не проверяются надлежащим образом.
- `--apply` продолжает migrate даже после backup FAIL. После migrate используется grep, который может превратить ошибку dry-run в «pending=0».
- `--apply` не делает pull/build/restart/reload, хотя runbook называет его эквивалентом полного наката.
- Проверяется enforcing CSP, приложение корректно пока отдаёт Report-Only. Read-only HTTP подтвердил именно Report-Only — сейчас это ложный FAIL.
- Запрос с 300000 байт передаётся одним argv curl; возможен системный `Argument list too long`, а не проверка 413 от bidder.
- Load выполняется внутри backend-контейнера с BIDDER_HOST для хоста; localhost не указывает на соседний Go-контейнер.
- Проверка cron смотрит имена timers/crontab, не доказывает наличие и успешность всех jobs, может не видеть `/etc/cron.d`.
- Convergence `--api-url` посылает from/to, но summary принимает hours/campaign_id: границы игнорируются. Сравнивается общий DB aggregate с user-scoped API. Ошибки SQL/HTTP не все переводятся в обещанный exit 2.
- Даже `migrate.py --dry-run` выполняет CREATE TABLE IF NOT EXISTS schema_migrations: не буквально read-only на чистой БД.

- [ ] Fail-fast dependency chain, корректные адреса/контракты/status checks, различать FAIL/SKIP/NOT TESTED. Unit/subprocess-тесты shell с подставными ответами и интеграционный прогон на Compose. Не запускать текущий `--apply` как безопасный автоматический накат.

### O-04 — P1 — нагрузочный тест выдаёт PASS на ошибках и не подтверждает target QPS [S, R]

**Где:** `backend/loadtest/rtb_load.py`, preprod --load.

PASS зависит только от p99 ответов. HTTP 403/429/500 входят в обычные latency samples, exception/timeout не включается в percentiles. Не проверяются error rate, достигнутый QPS, валидность BidResponse, bid-rate или callback funnel. Closed-loop workers и concurrency=50 дают coordinated omission/ограничение генератора. Большой заданный QPS не гарантирует фактическую нагрузку.

**Воспроизведено:** httpx MockTransport всегда возвращал 403; 10 запросов, p99=0.6 мс → `pass: true`, **exit 0**. Удалённый SSP не использовался.

- [ ] Gate на допустимые statuses/errors/achieved QPS/валидные ответы; timeout учитывать как нарушение SLO. Отдельный генератор с контролем offered load, no coordinated omission, метриками самого генератора. Проверять real bids и notices, а не преимущественно 204. Отдельные отчёты latency/throughput/учёт/бюджет.

### O-05 — P1 — алерты и health не доказывают готовность [S, V]

**Где:** `ops/prometheus/alerts.yml`, `main.py:health`, Go `Healthz`, `audit.py`, `obs_metrics.py`.

`/health` backend всегда возвращает 200 и не проверяет PG; release runbook ожидает 503 при PG-down. Go также не использует failing HTTP status для readiness. `BidderWinGapAnomaly` делит backend и bidder vectors без согласования job/instance labels: обычные scrape labels не совпадают, результат может быть пустым. `BidderNoBidSpike` делит полностью агрегированный numerator на неагрегированный denominator с labels — тоже требует исправления matching.

CronRetentionStale не срабатывает, если серия отсутствует; после рестарта она absent до первого успешного запуска. Нет показанного надёжного consumer liveness/pending/lag gate и полного disk/memory/restart monitoring. YAML-проверки имён/ссылок в tests не исполняют PromQL на realistic label sets. Наличие alerts.yml не означает, что Prometheus/Alertmanager и доставка оповещения настроены на VPS.

Audit middleware исключает `/px/`, но реальный ingestion `/pixel/collect`: поток beacon может дополнительно раздувать audit_log. Многие 403/429 outer middleware и исключения до response не обязательно попадают в audit; это не полный security/financial audit trail.

- [ ] Разделить liveness/readiness, проверить зависимости/consumer; promtool rule tests с job/instance labels, absent/up alerts, stream lag/trim loss, datastore/disk metrics. Проверить доставку реального тестового alert. Исключить high-volume beacon из management audit и определить политику security/finance audit.

### O-06 — P1 — online upgrade партиций и восстановление DEFAULT не доказаны [S, V]

**Где:** `migrate.py`, `033_bid_events_partitioned.sql`, `cron.py:bid_events_retention`.

Миграция 033 копирует таблицу INSERT SELECT и затем делает rename swap. Без остановки writers/dual-write/эксклюзивной защиты на всём переносе записи, пришедшие после snapshot копирования, могут остаться только в legacy-таблице. Она не «просто аддитивная»; универсальное обещание совместимости любого rollback неверно. Stream ограничен, поэтому обещание «consumer потом догонит без потерь» неверно для произвольной длительности миграции.

Если DEFAULT уже содержит строки будущей создаваемой партиции, CREATE PARTITION может завершиться конфликтом; retention сначала создаёт партиции и лишь потом измеряет DEFAULT, то есть до диагностического gauge можно не дойти. CI проверяет свежую БД, не такой upgrade/repair.

- [ ] Репетиция upgrade на копии данных с concurrent writes, измерением locks/disk/WAL и контрольными суммами; выбранный drain/cutover. Runbook переноса DEFAULT rows и восстановления партиций. Backup перед start образа: Dockerfile автоматически запускает migrate до uvicorn.

### O-07 — P2 — воспроизводимость, ресурсы и hardening нуждаются в доводке [S, T, V]

**Где:** pyproject, Dockerfiles, CI, `db.py`, proxy, API middleware.

Python runtime dependencies имеют открытые нижние границы, без lock. Docker runtime ставит дополнительные Celery/Flower сверх manifest security scan. Нет smoke сборки всей Compose-топологии в CI; локальные/CI/Compose версии PG расходятся. Frontend выбирает workspace root при двух lockfile. Контейнеры не переключаются на отдельного unprivileged user.

Lazy `_pool` создаётся без lock: параллельные первые запросы могут создать несколько пулов; shutdown не закрывает DB/Redis явным образом. Синхронные file writes/bcrypt в async API, неограниченные списки и тяжёлые report queries на одном API-процессе требуют capacity-проверки. Авторизованные ответы не получают явный Cache-Control: private/no-store в просмотренном HTTP ответе; Next proxy не задаёт явный no-store для upstream и буферизует upload целиком. Это hardening gap, не доказанная утечка через кэш.

- [ ] Зафиксировать версии/образ и целевую матрицу, dependency/image scan, non-root и ресурсные лимиты. Lifecycle pools, pagination/query budgets, upload backpressure, явная cache policy и proxy header policy; нагрузить отдельно management API и callback/billing.

## 11. Frontend и бизнес-семантика интерфейса

### F-01 — P2 — ошибки auth могут оставлять пустой экран; мобильное меню не соответствует dialog UX [S, R]

**Где:** `frontend/components/app-shell.tsx`, `lib/api.ts`, базовые UI-компоненты.

AppShell при `!me` возвращает null; на 401 редирект есть, но 403/503 и другие auth errors не получают полноценного error/retry view, а Toaster находится внутри ещё не отрендеренного shell. В мобильном dialog нет focus trap/переноса фокуса/Escape. Реальный browser: после Escape dialog остался, activeElement — кнопка открытия. В dashboard встречается link с вложенной button; в wizard — вложенные main landmarks. Валидационные Pydantic ошибки показываются пользователю частично на английском.

- [ ] Auth loading/error/retry состояния, корректная обработка blocked account, доступный drawer/dialog, проверка Tab/Escape/focus restoration, единственный main и неналоженные интерактивные элементы. Smoke на 390px + keyboard + screen reader; локализация ошибок.

### F-02 — P1 для отчётности, P2 для UX — одинаковые показатели имеют разные scopes и формулы [S]

**Где:** `stats.py:_resolve_scope/breakdown/report`, `share.py:public_stats`, frontend dashboard/analytics/settings.

Markup применяется к summary/timeseries, но breakdown вычисляет mult и не использует, report получает `_fx` и также не применяет; share показывает сырой spend. Без единого договора это разные суммы в кабинетах одной цепочки. Raw report объявляет conversions=0 независимо от реальных conversions; totals считаются суммой уже LIMIT-обрезанных строк, а не общим итогом. report date range не ограничен как timeseries, часть hours/limit параметров других handlers также не имеет нижней/верхней границы.

UI обещает закупку USD с конверсией курса и value-based/clean-traffic поведение; production Go пока RUB-only и не исполняет часть этих настроек. Согласовать продуктовый контракт, а не маскировать разницу оформлением.

- [ ] Единые definitions gross/net/markup/currency и единые totals для summary/breakdown/report/share/CSV/ORD. Явное обозначение page totals, поддержка conversions в raw slices или сообщение «недоступно» вместо 0. Ограничить интервалы и размеры отчёта; сверка одних fixture данных во всех представлениях.

## 12. Дополнительные риски для отдельной проверки

Эти пункты не выдаются за полностью воспроизведённые production-инциденты:

- [ ] **Удаление сущностей с историей.** Проверить advertiser/campaign/creative/user deletion при наличии transactions, stats и ord_creatives. Append-only ledger trigger может конфликтовать с FK ON DELETE SET NULL/CASCADE; удаление creative уносит ORD registration, отчётные JOIN могут терять историю. Для финансовых/юридических сущностей предпочтительно архивирование.
- [ ] **Привязка креативов line item.** `set_line_item_creatives` делает исключённый creative `line_item_id=NULL`, а feed понимает NULL как «для всех line items кампании». Проверить, действительно ли UI-выбор «только эти» ограничивает выдачу, особенно для двух line items с разными наборами.
- [ ] **PII и retention.** Bid/device IDs, IP, URL и conversion/audience-связи требуют согласованного срока хранения, удаления, access logging и privacy policy. Retention только bid_events недостаточен для pixel_events, audience_members, audit_log, sessions и files. Не приравнивать наличие block_gdpr/block_coppa к полной privacy compliance.
- [ ] **Валидаторы и bounds.** UUID/date/type/NULL/NaN/Infinity, `permissions={'flag':'false'}` (bool строки становится True), отрицательные limits, oversized lists/bodies; массовые CRUD и экспорты на большом аккаунте. Business-invalid input должен быть 4xx, не 500.
- [ ] **Rule/ORD cron concurrency.** last_triggered_at/already-exported проверяются отдельно от действия: два scheduler могут выполнить действие повторно. Нужны ownership locks/idempotency и мониторинг результата каждой задачи.
- [ ] **География/identity.** Сверить ISO-2/ISO-3, region codes против русских названий справочника, city spelling, eids source namespaces, отсутствие ID, shared-IP/NAT, TTL clid и задержанную атрибуцию.
- [ ] **Секреты и сеть.** Полный secret scan текущего дерева и истории, секреты platform_settings в backup, права пользователя БД, Redis ACL/TLS, firewall, trusted proxies, HTTPS mixed-content, доступ к metrics/docs/internal routes.
- [ ] **Зависимости и supply chain.** Зафиксировать неиспользуемую уязвимость Go-модуля из govulncheck verbose, решить обновление; не трактовать «нет reachable vuln» как отсутствие любого CVE в образе.

## 13. Чек-лист исправлений по очередям

### Очередь A — закрыть прямой денежный и публичный риск

- [ ] M-01: запрет произвольного balance через advertiser CRUD.
- [ ] M-02: атомарный и идемпотентный deposit/adjust.
- [ ] M-03/M-04: сохранность Redis-budget, единый Redis и единственная business-day граница.
- [ ] M-05/M-06: полный финансовый reservation lifecycle и ограничение обязательств.
- [ ] L-01: production запрет mock-ОРД и unsafe serving flags.
- [ ] S-01/S-04/S-05/S-06: CSRF, bootstrap/config, AI auth/квоты, SSP auth/IP.

### Очередь B — обеспечить настоящий сквозной programmatic-контур

- [ ] R-01: скопированный SSP URL ведёт в Go, не в Python.
- [ ] E-01/E-02/E-03: spend/event/dedup/consumer согласованы и восстанавливаются после сбоя.
- [ ] E-04/E-05: тестовая изоляция и работающий pixel/callback routing.
- [ ] R-02: bounded stale feed и обязательная сохранность bid metadata.
- [ ] R-03/R-04/R-05/R-06: поддерживаемые modes/formats/targeting действительно применяются либо отключены.
- [ ] S-02/S-03: referenced-object и role/viewer scopes.
- [ ] L-02/L-03: versioned erid, provider acceptance, коррекции экспорта.
- [ ] F-02: единство financial/reporting definitions.

### Очередь C — сделать развертывание и критерии приёмки исполняемыми

- [ ] O-01: production Compose и env без мёртвых приложений/mounts.
- [ ] O-02: реальный restore drill с uploads.
- [ ] O-03/O-04: preflight и load checker не выдают ложный успех.
- [ ] O-05: readiness и проверенные PromQL/notification delivery.
- [ ] O-06: online upgrade/DEFAULT repair drill.
- [ ] O-07/F-01: reproducible builds, pools/resources, ошибки/доступность frontend.

## 14. Минимальный регрессионный набор, которого сейчас не хватает

| Сценарий | Приёмочный результат |
|---|---|
| Manager/advertiser меняет balance через любой CRUD | Отказ, balance/ledger неизменны |
| Пополнение повторено после timeout | Одна проводка, одно изменение остатка |
| 2 bidder replicas, малый balance/total/daily, late wins | Обязательства укладываются в согласованный предел; нет неограниченного overdraft |
| Win commit успешен, stats write упал | Повтор восстанавливает stats без повторного списания |
| Redis dedup успешно, DB insert упал | Повтор не теряет событие |
| Consumer commit успешен, ACK потерян | Один event и один aggregate increment |
| Consumer новый HOSTNAME/PG recovery/Redis group lost | Pending восстановлены, нет silent skip/двойного счёта |
| Redis eviction/recreate и смена суток | Бюджет не открывается повторно, поздний loss не уменьшает новый день |
| Полный test=1 funnel и UI simulator | Ноль реальных списаний/production metrics/conversions/ORD effects |
| Inventory report с реальными handlers | Ненулевой spend и корректные publisher/tagid/bundle на каждом этапе |
| Смена креатива и erid посередине месяца | Старые/новые показы экспортируются по своим регистрациям |
| Одновременная регистрация на пустой БД | Не более одного bootstrap admin, provisioning контролируется |
| Cross-origin form на bodyless mutation | Данные не изменились |
| Viewer одного advertiser обращается ко всем GET stats | Никаких данных других advertisers владельца |
| Admin/finance/agency list→detail→operation | Поведение совпадает со спецификацией роли, не случайный 404 |
| SSP URL copied из UI, callback/media/pixel через public HTTPS | Каждый запрос попадает в нужный сервис |
| Native required assets / private PMP / currency mismatch / 90s VAST | Валидный response либо корректный no-bid |
| Нагрузка из одних 403/429/500 или недостигнутый QPS | Ненулевой exit, FAIL |
| Backup→restore→application start | Рабочая схема, проверяемые балансы, история и открывающиеся assets |
| PG/Redis/consumer outage с реальными labels Prometheus | Readiness/alerts отражают проблему; после восстановления учёт сходится |

## 15. План серверной приёмки после исправлений

1. **Зафиксировать release SHA и поддерживаемый pilot scope.** Для раннего пилота разумно ограничить CPM/RUB и конкретный проверенный banner-path; прочие режимы заблокировать технически. Не отправлять реальные client campaigns в тестовый SSP.
2. **Чистый staging deployment.** Проверить всю топологию с production-версиями PG/Redis/Node, migrations на пустой БД, bootstrap, закрытые внутренние порты, trusted proxy, TLS и отсутствие demo fallback.
3. **Seed отдельного QA tenant/SSP и baseline.** Балансы, budgets, counts, ledger и ORD state известны до прогона. Тестовые fixtures не смешивать с реальными отчётами.
4. **Полный позитивный и негативный funnel.** Bid → win/loss → impression → click → pixel goal → reports → ORD sandbox; duplicate, delayed, out-of-order, test=1, missing/malformed macro, чужие IDs и роли.
5. **Финансовая конкуренция.** Несколько кампаний и bidder replicas, маленькие пределы, большой offered load, поздние уведомления, полночь, рестарт Redis. Сверять не только API-карточку, но obligations/reservations/transactions/balances.
6. **Нагрузка ступенями.** Разные bid-rate, candidate count, imp count, targeting lists, retargeting и форматы. Генератор отдельно от bidder. Отчёт включает offered/achieved QPS, p50/p95/p99, timeout/error rate, valid bid-rate, CPU/RAM/GC, Redis RTT/pool/memory/evictions, stream lag/pending/trim, PG locks/WAL/disk и callbacks throughput.
7. **Soak и деградация.** Продолжительный прогон и отдельно короткие Redis/PG/feed/consumer отказы. Для стресс-сценария не требовать от ложного read-only health обнаружения проблемы; использовать исправленные readiness/alerts. После recovery — строгая сверка closed event-time window и replay.
8. **Recovery drill.** Backup→потеря test deployment→restore с uploads; измерить RPO/RTO. Проверить upgrade и code rollback на копии реальной схемы, не полагаться на обещание «все миграции аддитивны».
9. **Сверка SSP/ORD.** Один и тот же UTC/business-period, валюта, billable event, win/loss definitions, erid version, accepted rows. Разницы классифицировать, не компенсировать ручной правкой spent_total.
10. **Решение о релизе.** Нет открытых P0 и применимых P1; исключённые возможности реально выключены; отчёты тестов, SLA и восстановления приложены к конкретному SHA. До этого статус остаётся NO-GO для live money.

## 16. Заключение

Основа продукта и тестовая дисциплина уже есть; переписывать платформу целиком оснований нет. Но до реальной закупки необходимо закрыть не косметику, а инварианты денег, событий, маркировки и исполнения настроек production-биддером. Самое полезное продолжение — небольшие исправления по очередям A/B/C с независимыми регрессионными сценариями, а не очередная отметка «все фазы закрыты» по числу тестов.

**В рамках этого аудита исправления не выполнялись.** Любые последующие изменения должны получить новый SHA, повторный прогон и обновлённое release decision; этот отчёт описывает только указанную ревизию.
