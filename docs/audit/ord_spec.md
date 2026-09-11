# ОРД: версии erid, статистика, акты (B13, audit L-02/L-03)

Статус: B13a и B13b (акты, §6) реализованы (2026-09-10/11). B13c (UI) — см. план.
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
| ord_invoices (акт) | `POST /api/external/v2/invoice` | `externalInvoiceId` = наш UUID, `externalContractId`, `invoiceNumber`, `invoiceDate`, `startDate/endDate`, `price` (= `amount`), `withNds`, `clientRole`/`contractorRole` (RD/RA/RR/ORS), `contracts=[{externalContractId, price, withNds}]` (один договор); повтор = редактирование `[V]` |
| детализация акта | `POST /api/external/v3/invoice/creative/unlink` (`deleteAllCreatives`) → `.../creative/link` | `externalInvoiceId`, `externalContractId`, `externalCreativeIds=[<id>:v<n>]`; 4xx на unlink нового акта игнорируется `[V]` |
| строки акта | `POST /api/external/v2/statistic` | тот же `externalStatisticId`, что у месячной выгрузки; `dateStart/EndFact` = месяц ∩ период акта, `viewsCountByFact` = факт, `viewsCountByInvoice`/`moneySpent`/`unitCost` = значения акта `[V]` |

Роли контрагентов (`counterparties.role` → `ORGANISATION_ROLE_*`): advertiser→RD,
agency→RA, publisher→RR, ors→ORS. Срок подачи актов и статистики за месяц N —
до 30-го числа месяца N+1.

## 5. Тесты

* `tests/test_ord_ozon_mapping.py` — маппинг, реальные пути, построчный результат, 5xx.
* `tests/test_ord_versions_db.py` — версии/история, `409 ord_locked`, деление
  показов по версиям, `partial`/`failed`, no-op/`force`, advisory-lock.
* `tests/test_ord_flow_db.py` — сквозной поток approve → erid → фид (обновлён под версии).
* `tests/test_ord_invoices_db.py` — акты: предпросмотр по месяцам и цене клиента,
  создание/409-конфликты, submit ok/partial/failed, PUT → draft → повторный submit,
  выгрузка крона берёт значения принятого акта, scope.

## 6. Акты (B13b, `ord/invoices.py`, миграция 048)

Решения 2026-09-11 (пользователь): единица акта — **договор** (акт ссылается на
один договор, как требует Ozon), период **произвольный** (например 15.09–15.10),
расход в строках — **цена клиента**, повтор после правки — **upsert + пересборка
связей**. Логику ВК и выдержки из документации Ozon пользователь пришлёт позже —
пункты `[V]` подлежат сверке.

### Модель

* `ord_invoices`: `user_id` (= владелец договора), `contract_id`, `number`
  (уникален в договоре), `invoice_date`, `period_start/end` (≤ 366 дней),
  `amount` (по умолчанию Σ `spend` строк), `is_vat` (по умолчанию из договора),
  `client_role`/`contractor_role` (по умолчанию `counterparties.role` заказчика/
  исполнителя договора), `campaign_ids` (фильтр предпросмотра), `status`
  `draft | submitting | ok | partial | failed`, `ord_external_id` (= id), `error`,
  `submitted_at`.
* `ord_invoice_items`: строка = **(креатив, версия erid, календарный месяц)**.
  ОРД принимает статистику строго в рамках месяца, поэтому акт 15.09–15.10 даёт
  по две строки на креатив: `period_start/end` = месяц ∩ период акта.
  Хранятся `fact_*` (что было в системе при сохранении) и редактируемые
  `impressions/clicks/spend/unit_cost` (что подписали), `payment_type`
  (`line_items.bid_type`: CPM — за 1000 показов, CPC — за клик), `status`
  `pending | accepted | rejected` + `error`.
* `external_statistic_id` строки = `<creative>:v<n>:<YYYY-MM>` — **тот же, что у
  месячной выгрузки крона** (`UNIQUE` по всей таблице). Отправка акта = коррекция
  той же строки в ОРД, двойного учёта нет. Следствие: (креатив, версия, месяц)
  может входить только в один акт (второй акт → 409 с номером первого).
* Цена клиента: `net × rbac.spend_multiplier(владелец договора)` — та же точка,
  что в отчётах (B14b); в БД `stats_hourly.spend` остаётся net.

### Поток

1. `GET /ord/invoices/preview?contract_id&period_start&period_end[&campaign_ids][&invoice_id]`
   — строки по факту (LATERAL-версия на конец часа, как в `/cron/ord-export`,
   договор строки = договор версии), `amount` = Σ по строкам, не занятым другими
   актами; `warnings.already_invoiced` (строка уже в акте №…), `warnings.without_erid`
   (показы креативов контрагента договора без действующего erid).
2. `POST /ord/invoices` / `PUT /ord/invoices/{id}` — черновик с построчной
   детализацией; всё редактируемо, факт пересчитывается при каждом сохранении.
   Валидация: месяц строки внутри периода акта (422), версия с erid этого
   владельца (404) и этого договора (409), строка не в другом акте (409), номер
   уникален в договоре (409). PUT после отправки → снова `draft`; договор акта
   менять нельзя (409). `DELETE` — только неотправленный черновик.
3. `POST /ord/invoices/{id}/submit`: `register_invoice` (upsert) →
   `link_invoice_creatives` (unlink all → link) → `submit_stats` по месяцам.
   OrdError на первых двух шагах → акт `failed`, все строки `rejected`, HTTP 502.
   Статистика — построчно: `ok` (все accepted) | `partial` | `failed`; строка без
   подтверждения провайдера = `rejected`. Повторный submit после правки — та же
   цепочка с тем же `externalInvoiceId` (коррекция).
4. `/cron/ord-export` по строке с принятым актом отправляет `viewsCountByFact` =
   факт, а `viewsCountByInvoice/moneySpent/unitCost` и даты — из акта, чтобы
   плановая/принудительная выгрузка не затирала подписанные значения.

### Открытые вопросы `[V]`

* Достаточно ли `v2/invoice` + `v3/invoice/creative/link` для привязки строк
  статистики к акту, или ОРД связывает статистику с актом сам по креативу и датам.
* Нужны ли в акте `violation`/`comment`; формат `price` при `withNds=false`.
* Excel-шаблоны Ozon (акты) — маппинг колонок для импорта/экспорта в B13c.


## 7. ОРС-модель (B13c–B13g, решения 2026-09-11)

Central DSP — **Оператор рекламной системы (ОРС)**, не рекламораспространитель. Источники: шаблоны Ozon ОРД
(`organisation/contract/creative/platform/statistic/invoice_template.xlsx`), модели API (`OrganisationData{isOpc,isPp,rsUrl}`,
`ExternalContract{externalContractId,price,withNds}`, `CreativeLinkItem`), кабинеты Avito («Данные ОРД» на кампании) и VK Ads
(«Отчёты для ОРД» / «Добавление актов»).

### Решения

| # | Вопрос | Решение |
|---|---|---|
| D1 | Площадки | **Одна сводная площадка Central DSP** (правило РКН для DSP/сетей, как `yandex.ru`/`vk.com`): тип «Сайт», URL = домен рекламной системы, владелец = наша организация. В отчётах ОРД по SSP не разделяем. Сейчас `register_platform` нигде не вызывается, а статистика ссылается на `central-dsp` → площадку надо регистрировать до первого экспорта. |
| D2 | Где цепочка | **На кампании**, креативы наследуют. Режимы как у Avito: `self` (своё), `own_client` (свой клиент, прямой договор), `partner_client` (клиент партнёра, через посредника). После подтверждения (`ord_confirmed_at`) поля цепочки заморожены. |
| D3 | Роли | Central DSP = ОРС (`isOpc=true`, `rsUrl`), SSP/паблишер = РР (`isPp=true`), клиент/агентство = заказчик исполнительского договора. |
| D4 | НДС | Расход в БД и в статистике — **без НДС**; 22 % добавляется поверх (клиент платит 122 000 → бюджет 100 000). Акт: `amount_gross = amount_net × (1 + vat_rate/100)`, ставка по умолчанию 22, `unit_cost` в акте тоже с НДС. В Ozon уходит `price` (с НДС) + `withNds=true`. |

### Цепочка в терминах таблиц

```
counterparty(advertiser)  ──initial contract (client=advertiser, contractor=agency | self)──►
counterparty(agency)?     ──executive contract (client=agency|advertiser, contractor=self)──►
counterparty(self, ОРС, isOpc)  ──►  платформа «Central DSP» (SITE, владелец = self)
```

- `self` (самореклама): договоров нет; креатив `isSelfPromotion=true`, `externalOrganisationId=self`; акты не подаются.
- `own_client`: `initial = executive` — один договор (рекламодатель ↔ мы). В креативе `externalContractId=initial`.
- `partner_client`: `initial` (рекламодатель ↔ агентство) + `executive` (агентство ↔ мы, `externalParentId=initial`).
  Креатив регистрируется на **изначальный** договор; акт выставляется по **исполнительскому** с разбивкой `contracts[]`
  по изначальным договорам (лист «Изначальные договоры» шаблона).

### Этапы

**B13c — организация-ОРС и сводная площадка (backend + настройки). DONE 2026-09-11.** Миграция 049: `counterparties.is_self bool`
(частичный уникальный индекс — ровно одна), `counterparties.rs_url`. `organisation_body`: `isOpc = is_self`, `isPp = role=='publisher'`,
`rsUrl`. Площадка: `ord.service.ensure_platform()` — регистрирует `central-dsp` (`PLATFORM_TYPE_SITE`, `url` из настроек
`ord_ozon.platform_url`, `isOwner=true`, `externalOrganisationId = self`) один раз (`platform_settings.ord_platform`), вызывается
перед экспортом статистики (крон, акт) и из `POST /ord/platform/register` (force); без `is_self`-организации/URL экспорт
через реальный Ozon → `failed: self_org_missing|self_org_unregistered|platform_url_missing`, через mock/тестовый адаптер — пропуск.
Владелец площадки задаётся со стороны организации: `organisation.platforms[] = [{externalPlatformId, isPlatformOwner: true}]`,
поэтому после `POST /platform` наша организация пересылается повторно. `GET /ord/platform` — статус и список `missing`.
UI: переключатель «Наша организация (ОРС)» в карточке контрагента, URL площадки и статус регистрации в настройках ОРД.

**B13d — цепочка на кампании (backend + UI, поток как в Avito: «Данные ОРД» до настройки кампании). DONE 2026-09-11.** Как реализовано (отличия от плана ниже): шаг «Данные ОРД» — **первый** шаг мастера (до «Формат»); режим `self` = свои товары/услуги с **одним** исполнительским договором (рекламодатель ↔ ОРС), самореклама рекламной системы (`isSelfPromotion`) не поддерживается (`self_promotion_not_supported`); `own_client` = изначальный (рекламодатель ↔ агентство) + исполнительский (агентство ↔ ОРС); `partner_client` — то же, посреднический договор через `parent_contract_id`. Логика в `ord/chain.py`; эндпоинты `POST /campaigns/ord-chain/preview`, `GET /campaigns/{id}/ord-chain`, `POST /campaigns/{id}/ord-chain/confirm`. План: Миграция 050: `campaigns.ord_mode` (`self|own_client|partner_client`),
`campaigns.counterparty_id` (рекламодатель), `campaigns.initial_contract_id`, `campaigns.executive_contract_id`,
`campaigns.ord_confirmed_at`. Бэкфилл из креативов (все креативы кампании с одним `counterparty_id/contract_id` →
кампания; иначе `ord_mode=NULL`, предупреждение). `ord.service.resolve_contract` → из кампании; `creatives.counterparty_id/contract_id`
остаются как снапшот на момент регистрации (не редактируются, из модалки убираются). Валидации: `initial.client = counterparty`,
`executive.contractor = self`, в `partner_client` `executive.client = initial.contractor`; запуск кампании (`launch_checks` блокер `ord_chain`, вместо текущего `ord_marking` только для РФ) / регистрация erid
без подтверждения → `422 ord_chain_required`; изменение после `ord_confirmed_at` → `409 ord_locked`.
`GET /campaigns/{id}/ord-chain` — сводка для окна «Данные в ОРД». UI мастера `/campaigns/new`: шаг «Данные ОРД» сразу после «Основное» (радио «кого рекламируете», рекламодатель
(+ «добавить» inline), изначальный/исполнительский договор, «Подтвердить данные» → окно цепочки «Всё верно / Есть ошибки»),
блок контрагента/ККТУ со шага «Креативы» уходит (ККТУ остаётся). В карточке кампании блок read-only после подтверждения.
`rbac_matrix.py --write`, `assert_refs_owned` для новых ссылок.

**B13e — акты по ОРС-модели (backend).** Миграция 051: `ord_invoices.vat_rate int DEFAULT 22`, `amount_net`, `amount_gross`
(`amount` → `amount_gross`), `ord_invoice_contracts(invoice_id, initial_contract_id, amount_gross, amount_net, vat_rate)`.
Preview: по **исполнительскому** договору + период → строки как сейчас + группировка по изначальным договорам кампаний;
`statistics_amount_net/gross` (факт, колонка «Статистика, сумма» VK). `invoice_body`: `contractorRole=ORS`,
`clientRole` из роли контрагента (`ADVERTISER|AGENCY`), `contracts[]` = изначальные договоры, `v3/creative/link` по изначальному
договору. `own_client`: `contracts[] = [initial]`. Разница акт ≠ факт > 1 % → предупреждение, не блок.

**B13f — UI `/ord` вкладка «Акты» (VK-style).** Выбор месяца → таблица по исполнительским договорам/клиентам:
ОРД-статус, Отчёт (Не заполнен / Черновик / Отправлен / Ошибка), Сумма без НДС, Сумма с НДС, Статистика (факт), «Заполнить»/✎.
Drawer «Добавление акта»: шапка договора (номер, тип, заказчик/исполнитель + ИНН), номер акта, период, сумма с НДС, ставка НДС,
дата акта, авто «без НДС», разбивка по изначальным договорам (редактируемые суммы, сумма = итогу), строки креативов (свернуто),
«Сохранить» / «Отправить в ОРД»; построчный статус и повтор; кнопка «Отправить все в ОРД» для черновиков месяца.
В карточке креатива: история версий erid; на `409 ord_locked` — текст «данные ОРД зафиксированы после запуска кампании».

**B13g — ошибки ОРД.** Крон `/cron/ord-notifications` (раз в час): `GET /api/external/notification/error` →
по `objectType/externalId` проставить `ord_status='error'`, `ord_error=text` (организации, договоры, креативы, площадки, акты),
`ord_notification_cursor` в `settings`. Бейдж «Ошибки ОРД: N» на `/ord`, метрика `ord_notification_errors_total`, алерт, runbook.

Порядок: B13c → B13d → B13e → B13f → B13g → C1–C6. Каждый этап: миграция идемпотентна, тесты (`tests/test_ord_*`,
`*_db.py`), `parity_matrix` не затрагивается (Go-биддер не участвует, кроме чтения `supply_partner_id` в `stats_hourly`).
