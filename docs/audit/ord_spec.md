# ОРД: версии erid, статистика, акты (B13, audit L-02/L-03)

Статус: B13a реализован (2026-09-10). B13b (акты) и B13c (UI) — см. план.
Пункты `[V]` требуют сверки с песочницей Ozon при получении API-ключа.

## 1. Версии erid (`ord_creatives`)

* Одна строка = одна версия содержимого креатива. `UNIQUE (creative_id, version)`;
  ровно одна открытая версия — `UNIQUE (creative_id) WHERE superseded_at IS NULL`.
* `erid` неизменяем. Смена содержимого одобренного креатива (`_CONTENT_FIELDS`
  в `routers/creatives.py`) → `ord.service.supersede_current` закрывает текущую
  `registered`-версию (`status='superseded'`, `superseded_at=now()`), новая
  регистрация создаёт `version+1` с новым erid. Незарегистрированные версии
  (`awaiting_*`/`failed`) не закрываются — обновляются на месте.
* `externalCreativeId` в ОРД = `<creative_id>:v<version>` (`ord.service.external_creative_id`).
* Юридически значимые поля `counterparty_id`, `contract_id`, `kktu_code` после
  первой выдачи erid заморожены: `creatives.ord_locked_at`, PATCH отвечает
  `409 {"code":"ord_locked","fields":[...]}`. Другой заказчик/договор/тематика =
  новый креатив (модерация, новый erid, отдельная статистика) — так же, как в
  ОРД новые данные договора = новый externalCreativeId.
* История: `GET /ord/creatives/{id}/versions`.

## 2. Привязка статистики к версии

Показ из `stats_hourly` относится к версии, действовавшей **на конец часа**:
последняя версия с `erid`, у которой `COALESCE(registered_at, created_at) < ts_hour + 1h`
(`JOIN LATERAL ... ORDER BY registered_at DESC LIMIT 1`). Следствия:

* смена содержимого в середине месяца делит показы между старым и новым erid;
* час, в котором произошла смена, целиком уходит новой версии;
* окно между `supersede` и новой регистрацией остаётся за старой версией
  (креатив в это время вне фида для РФ — фактических показов там нет);
* показы, для которых ни одна версия не действовала, → `skipped_without_erid`.

Go-биддер и ключ `stats_hourly` не менялись (решение 2026-09-10: точность
«час» достаточна, стамп erid в события не нужен).

## 3. Месячная выгрузка `/cron/ord-export`

* `period=YYYY-MM` (по умолчанию прошлый месяц), `force=1` — коррекция.
* Advisory-lock `hashtext('ord_export:<period>')`: параллельный запуск → 409.
* Строка = креатив × версия × период; `externalStatisticId =
  <creative_id>:v<version>:<period>` → повторная отправка = upsert в ОРД.
* Контракт адаптера: `submit_stats(period, rows) -> {"accepted": [ids], "rejected": [{id, error}]}`.
  Ozon: батчи по 100 в `POST /api/external/v2/statistic`; батч с 4xx досылается
  построчно (`OrdDataError`), 5xx/сеть → `OrdError` на всю выгрузку.
* Результат построчно в `ord_export_rows` (`accepted | rejected` + `error`).
  Строка без подтверждения провайдера считается `rejected`.
* Статус `ord_export_log`:

  | status | когда | считается выгруженным |
  |---|---|---|
  | `ok` | все строки accepted | да |
  | `empty` | показов за период не было | да |
  | `partial` | часть строк rejected | нет — следующий cron повторит |
  | `failed` | OrdError, все строки rejected, или показы были, но ни одной строки с erid | нет |

  `kind`: `stats` (плановая), `stats_correction` (`force=1` по уже выгруженному периоду).
* Метрики: `ord_export_failures_total`, `ord_export_last_success_timestamp_seconds`
  (только `ok`/`empty`), `ord_export_skipped_without_erid`, `ord_export_rows_rejected`.
  Алерты: OrdExportFailed / OrdExportLag / OrdExportSkippedWithoutErid /
  OrdExportRowsRejected (runbook `rtb_alerts.md#ord-export`).
* `GET /ord/exports`, `GET /ord/exports/{log_id}/rows` (в scope пользователя).

## 4. Маппинг в API Ozon (`ord/ozon_mapping.py`)

Базовый URL `https://api-ord.ozon.ru`, `Authorization: Bearer <key>`. Все
операции — upsert по внешнему ID (наш UUID).

| Наше | Ozon | Ключевые поля |
|---|---|---|
| counterparties | `POST /api/external/organisation` | `externalOrganisationId`, `inn`, `fullOpf`, `legalType` (ul→LEGAL, ip→ENTREPRENEUR, fl→INDIVIDUAL, foreign→FOREIGN_LEGAL), `ogrn`/`ogrnip`, адреса `[V]` |
| ord_contracts | `POST /api/external/contract` | `externalContractId`, `externalOrganisationCustomerId`/`PerformerId` (внешние ID сторон, обе `registered`), `contractType` (service→SERVICE, mediation→INTERMEDIARY, additional→ADDITIONAL_AGREEMENT), `subjectType`, `actionType=DISTRIBUTION`, `contractNumber`, `contractDate`, `withNds`, `price`, `externalParentId` (родительский договор цепочки), `isCreativeReporter=true` |
| ord_creatives | `POST /api/external/creative` | `externalCreativeId=<id>:v<n>`, `externalContractIds`, `title`, `description`, `advObjectType` (banner/html5→BANNER, video→VIDEO `[V]`, native/push→TEXT_GRAPHIC_BLOCK), `paymentType` (CPM по умолчанию), `okvedCodes=[kktu]`, `mediaData` (file {id,url} `[V]` — возможно, нужна предзагрузка через `/api/external/file`), `targetLink`, `urlList`; ответ `creative.marker` = erid |
| площадка DSP | `POST /api/external/platform` | `externalPlatformId` = env `ORD_PLATFORM_EXTERNAL_ID` (по умолчанию `central-dsp`), `platformType=PLATFORM_TYPE_SYSTEM` `[V]` регистрируется вручную/при первом экспорте |
| статистика | `POST /api/external/v2/statistic` `{statistics:[...]}` | `externalStatisticId`, `externalCreativeId`, `externalPlatformId`, `dateStart/EndPlan`, `dateStart/EndFact` (границы месяца), `viewsCountByFact`, `viewsCountByInvoice`, `moneySpent`, `unitCost` (за 1000 показов), `withNds` |
| акт (B13b) | `POST /api/external/v2/invoice` + `POST /api/external/v3/invoice/creative/link` | `externalInvoiceId`, `invoiceNumber`, `invoiceDate`, `startDate/endDate`, `price`, `withNds`, `clientRole`/`contractorRole` (RD/RA/RR/ORS), `contracts[{externalContractId, price, withNds}]`; повторная отправка = редактирование; `unlink` для исключения креативов |

Роли контрагентов (`counterparties.role` → `ORGANISATION_ROLE_*`): advertiser→RD,
agency→RA, publisher→RR, ors→ORS. Срок подачи актов и статистики за месяц N —
до 30-го числа месяца N+1.

## 5. Тесты

* `tests/test_ord_ozon_mapping.py` — маппинг, реальные пути, построчный результат, 5xx.
* `tests/test_ord_versions_db.py` — версии/история, `409 ord_locked`, деление
  показов по версиям, `partial`/`failed`, no-op/`force`, advisory-lock.
* `tests/test_ord_flow_db.py` — сквозной поток approve → erid → фид (обновлён под версии).
