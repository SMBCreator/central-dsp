# Сверка адаптера Ozon ОРД со swagger (раздел C)

Источник истины: `docs/ord/swagger_ord_ozon.json` (OpenAPI 3.0.0, 43 пути).
Код: `backend/ord/ozon.py` (транспорт, пути), `backend/ord/ozon_mapping.py` (единственный адаптер полей).
Контракт закреплён тестом `backend/tests/test_ord_swagger_contract.py`: каждое тело,
которое строит адаптер, и каждый реальный вызов клиента валидируются против схемы swagger
(путь+метод существуют, нет неизвестных полей, все `required` на месте, enum и типы верны). Без сети.

Логика цепочки, состояния, гейты (запрет mock erid в prod, freeze юр. полей, привязка договоров
к владельцу) не менялись — только расхождения ниже (C.3).

| № | Метод | Поле / аспект | Swagger | Код (до) | P | Решение |
|---|---|---|---|---|---|---|
| C1 | организация | путь | `POST /api/external/v6/organisation` | `/api/external/organisation` | P0 | путь v6 |
| C2 | организация | наименование | `name` — **required** | `fullOpf` (нет в схеме) | P0 | `name` |
| C3 | организация | адрес | `address: string` | `legalAddress{}`, `postAddress{}` | P1 | строка `address`, только если задана |
| C4 | организация | ОГРН / ФИО | полей `ogrn`, `ogrnip`, `fio` нет | отправлялись | P2 | не отправляем (ОРД определяет по ИНН; у нас поля остаются для UI) |
| C5 | площадка | путь | `POST /api/external/v6/platform` | `/api/external/platform` | P0 | путь v6; поля совпадали |
| C6 | договор | путь | `POST /api/external/v6/contract` | `/api/external/contract` | P0 | путь v6 |
| C7 | договор | НДС | поля `withNds` нет | `withNds` | P2 | убрано (НДС — в статистике и акте) |
| C8 | креатив | путь | `POST /api/external/v4/creative` | `/api/external/creative` | P0 | путь v4 |
| C9 | креатив | ККТУ | `kktuCategory: string[]` (обязателен с 13.01.2025) | ККТУ уходил в `okvedCodes` | P0 | `kktuCategory` |
| C10 | креатив | `mediaData[].file.id` | int64 — ID файла, загруженного в ОРД | sha1-хэш URL | P1 | внешние медиа — только `originalUrl` + `fileType` |
| C11 | креатив | лишние поля | `paymentType`, `isNative`, `mediaData[].description` нет в v4 | отправлялись | P2 | убраны; `creativeType=CREATIVE_TYPE_CREATIVE` |
| C12 | креатив | HTML5 | есть `ADV_OBJECT_TYPE_BANNER_HTML5` | html5 → `BANNER` | P3 | html5 → `BANNER_HTML5` |
| C13 | статистика | путь | `POST /api/external/v6/statistic` | `/api/external/v2/statistic` | P0 | путь v6 |
| C14 | статистика | тип оплаты | `paymentType` — **required** | не отправлялся | P0 | из `line_items.bid_type` (выгрузка) / `ord_invoice_items.payment_type` (акт); дефолт CPM |
| C15 | статистика | сумма | `price{amount, excludingAmount, vatRate}` — **required** | `moneySpent`, `withNds` | P0 | `price_obj()`: суммы у нас с НДС; ставка — из акта или 22% |
| C16 | статистика | `unitCost` для CPC | цена за единицу | всегда за 1000 показов, если не из акта | P2 | CPC = spend / clicks |
| C17 | акт | путь | `POST /api/external/v7/invoice` | `/api/external/v2/invoice` | P0 | путь v7 |
| C18 | акт | сумма акта | `servicePrice{amount, excludingAmount, vatRate}` | `price` строкой, `withNds` | P0 | `servicePrice`, net — хранимый `amount_net` |
| C19 | акт | доли договоров | `contracts[].price` — объект `ext_v7Price` | строка + `withNds` | P0 | объект цены, net — `ord_invoice_contracts.amount_net` |
| C20 | акт | тип | `invoiceType` (enum) | не задавался | P2 | `INVOICE_TYPE_INVOICE` |
| C21 | акт: link / unlink | путь и поля | `v3/invoice/creative/link|unlink` | совпадают | — | без изменений |
| C22 | ошибки ЕРИР | метод | `GET /api/external/notification/error_list` | `POST` с JSON-телом | P1 | GET, `pageSize` + `gtNotificationId` в query (тело GET в swagger — артефакт grpc-gateway) **[V]** |
| C23 | ошибки ЕРИР | `objectType` | `ERROR_NOTIFICATION_OBJECT_TYPE_CREATIVE` … | ожидали `CREATIVE` → всё становилось `unknown` | P1 | нормализация префикса (`notifications.normalize_object_type`) |
| C24 | ping | путь | `POST /api/external/v6/organisation/list` | `/api/external/organisation/list` | P1 | путь v6 (404 выглядел как «ключ неверный») |
| C25 | окружение | sandbox URL | `https://api-ord-sandbox.ozon.ru` | `https://ord-sandbox.ozon.ru` | P0 | исправлено в `settings._ORD_BASE_URLS` и UI; ранее сохранённый хост маппится в `adapter.get_ord_settings` |
| C26 | ретраи | 429 | лимиты не описаны | 429 = ошибка данных, без повтора | P2 | 429 ретраится как 5xx (3 попытки, backoff); повтор безопасен — upsert по `external*Id` |
| C27 | аутентификация | схема | `securitySchemes` в спеке нет | `Authorization: Bearer <key>` | P3 | без изменений **[V]** в sandbox |
| C28 | ошибки | тело | `rpcStatus {error: string}` для любых не-200 | текст ответа в `OrdError` | — | без изменений |
| C29 | ККТУ в UI | справочник | источник — `POST /api/external/dict/kktu` | `frontend/lib/kktu.json` из официального xlsx (294 листа) | P1 | **[V]** сверить коды с `dict/kktu` при первом sandbox-прогоне |
| C30 | иностранные контрагенты | поля | `regionNumber` (ОКСМ), `taxId`, `registrationNumber` | не передаются | P2 | **не закрыто**: в модели `counterparties` нет этих полей; иностранные организации в ОРД сейчас уйдут без обязательных для них реквизитов |

## НДС

`vatRate` — строка из enum `100` (без НДС), `0, 5, 7, 10, 20, 22`. Наши суммы (`stats_hourly.spend`,
`ord_invoices.amount_gross`) — с НДС. `ozon_mapping.price_obj()`:
* `is_vat=false` → `amount = excludingAmount`, `vatRate="100"`;
* иначе ставка из акта (`ord_invoices.vat_rate`, 22% по умолчанию), `excludingAmount` — хранимый
  `amount_net`, если он есть, иначе `amount / (1 + rate/100)` с округлением до копейки;
* ставка вне enum → `OrdDataError` (в ОРД не уходит).

Ежемесячная выгрузка без акта не знает ставку договора и использует 22%; когда по строке принят
акт, ставка, тип оплаты и суммы берутся из акта (тот же `externalStatisticId` → upsert).

## Не проверено без ключа (sandbox, вне CI)

Пункты **[V]** и чек-лист C.4 требуют тестового ключа Ozon ОРД (`environment=sandbox` в
настройках ОРД). Порядок прогона: организации (РД + мы как ОРС) → площадка → договор →
креатив → erid → статистика (повтор = upsert) → акт + link → намеренная ошибка (невалидный ИНН) →
появление в `error_list` и привязка к объекту в UI. Лог прогона — в `docs/ord/SANDBOX_RUN.md`
(без ключей и персональных данных).
