# Pre-deploy findings (правило 0.6 плана)

База: `feat/avito-ui-kktu` @ `121fd69`. Находки, которые противоречат плану или являются дефектами
текущего кода. **Ничего из этого ещё не исправлено** — требуется решение владельца, где отмечено.

Критичность: P0 — блокирует деплой / ломает основную функцию; P1 — серьёзная ошибка; P2 — умеренная; P3 — косметика.

---

## Раздел B — Гео

### F-B1 (P0). Таргетинг по стране никогда не совпадает с запросом по стандарту OpenRTB
- UI/БД хранят страну в **ISO alpha-2** (`"RU"`) — `backend/geo_data.py:4`, `frontend/components/targeting-editor.tsx:401`.
- OpenRTB 2.5/2.6 передаёт `device.geo.country` в **ISO alpha-3** (`"RUS"`); так же выглядят все fixtures наших тестов (`handler_test.go:176`, `fx_test.go:36` …).
- Go сравнивает точным равенством после `ToUpper` (`bidder-go/internal/targeting/targeting.go:159`, `handler.go:1228`), Python — так же (`backend/routers/rtb.py:148`). Нормализации alpha-3→alpha-2 нет ни там, ни там (известный незакрытый долг `docs/audit/parity_matrix.md:15`).
- Тест `handler_test.go:235` проходит только потому, что таргет в нём задан как `"RUS"` — такой формат UI никогда не сохранит.
- **Следствие:** `onApply` в GeoDrawer добавляет `"RU"` в `geo_countries` при выборе *любого* региона/города РФ → **любая кампания с гео-таргетингом не выигрывает ни одного запроса от SSP, соблюдающего спецификацию**. Сработает только у SSP, который шлёт alpha-2 вопреки стандарту.
- Это не новая функция, а дефект существующего пути → по плану исправляется вне флага `GEO_V2_ENABLED`? **Нужно решение владельца** (см. вопрос 1 в отчёте). Предлагаемое исправление: нормализация `device.geo.country` к alpha-2 по статической таблице ISO-3166 при построении `RequestView` (O(1) map lookup), то же в Python-эталоне, regression-тесты на `RUS`/`RU`/`rus`/пусто. Изменение затрагивает и `snap.Blocked(…"geo_country"…)` (`handler.go:664`) и запись `geo_country` в события (`handler.go:790, 828`) — в событиях станет alpha-2 вместо «как пришло».

### F-B2 (P0/P1). Регион и город объединяются по AND, а UI подразумевает OR
- `Match` проверяет три независимых условия: страна ∈ countries **И** регион ∈ regions **И** город ∈ cities (`targeting.go:159-167`, `rtb.py:147-155`).
- UI позволяет выбрать «Москва (регион) + Казань (город)» или «Казахстан + Краснодарский край». Ожидание пользователя — объединение (показывать в Москве ИЛИ в Казани). Фактически:
  - регион «Москва» + город «Казань» → нужно одновременно region=москва и city=казань → **никогда**;
  - регион «Краснодарский край» + город «Сочи» → фактически только Сочи (весь край теряется);
  - страны `[RU, KZ]` + регионы РФ → запросы из KZ никогда не проходят (у них нет региона РФ).
- Изменение семантики затрагивает существующие кампании → **нужно решение владельца** (вопрос 2). Предложение: в рамках GEO v2 — иерархическая модель `place_id` с OR-объединением и ancestor-цепочкой (B.3 п.5); для старого формата — либо оставить AND (побайтовая совместимость), либо исправить на OR сразу как дефект.

### F-B3 (P1). Регион/город сравниваются с русскими названиями без нормализации
- В БД — `"Москва"`, `"Республика Татарстан"`, `"Могилёв"`; в запросе SSP обычно `"Moscow"`, `"MOW"`, `"RU-MOW"`, `"Moskva"`, `"Tatarstan"`, `"Могилев"`. Сравнение — только `ToLower` (`handler.go:1229-1230`).
- Без слоя маппинга по партнёру (B.3 п.3) таргетинг по региону/городу на реальном трафике почти не будет срабатывать. Это ровно то, что закрывает GEO v2 — **противоречия плану нет**, фиксирую как подтверждение необходимости B.3.

### F-B4 (P2). Два независимых справочника гео
- `backend/geo_data.py` (autocomplete) и `frontend/components/geo-panel.tsx:71-185` (`RU_REGIONS`, `REGION_CITIES`, `OTHER_COUNTRIES`) — разные списки, могут расходиться. GEO v2 (`geo_places`) должен стать единственным источником; старые списки оставить для обратной совместимости до миграции UI.

### F-B5 (P2). Пустое гео в запросе = no-bid для любой кампании с гео
- Фактическое поведение — `strict` (`containsFold(list, "")` → false). План требует «сохранить текущее поведение по умолчанию» — сохраняем `strict`, `lenient` — опция кампании.

---

## Раздел A — Identity

### F-A1 (P1). Порядок идентификации противоречит A.6 и смешивает ID разных SSP
- Текущий `deviceIDOf` (`handler.go:1358-1379`): `device.ifa` → первый `eids` **любого source** → `user.id` → `user.buyeruid`.
- План A.6: `buyeruid` → `eids` по настроенному source → `ifa` → idsync → `user.id` в namespace партнёра.
- Проблемы текущего кода (существуют уже сейчас, флаг ID sync тут ни при чём):
  1. `user.id` — ID в пространстве биржи; он кладётся в общий namespace `audz:{segment}` / freq cap **без префикса партнёра** → коллизии между SSP (одинаковые `user.id` у разных бирж засчитываются как один пользователь в freq cap; членство в аудитории «утекает» между SSP).
  2. `buyeruid` (уже сопоставленный для нас ID) используется **только если нет `user.id`** — т.е. почти никогда.
  3. `ifa` не фильтруется на нулевое значение (`00000000-0000-0000-0000-000000000000` при LAT) → все такие устройства — один «пользователь» для freq cap.
- План требует «добавить `resolveIdentity`, не меняя существующий путь». Но существующий путь сам дефектен (п.1, п.3). **Нужно решение владельца** (вопрос 3): (а) оставить `deviceIDOf` как есть при `ID_SYNC_ENABLED=false` и включать новый порядок только флагом; (б) минимально исправить п.3 (zero-IFA) и п.1 (namespace `p:{partner}:` для `user.id`) сразу. Вариант (б) меняет ключи freq cap/аудиторий для трафика без ifa — существующие счётчики «обнулятся» для таких пользователей.

### F-A2 (P3). Реальных данных для sync нет — подтверждение плана
- Ни эндпоинтов, ни таблиц, ни cookie. Реализация A.3–A.5 с нуля, расширяя `supply_partners`. Противоречий нет. Юридический блок A.10 остаётся блокирующим для включения флага.

### F-A3 (P3). Parity Go↔Python по deviceID
- Python-эталон использует `device.ifa or device.ip` (`rtb.py:931`), Go — ifa/eids/user.id/buyeruid без IP. Python не обслуживает трафик (Caddy → Go), но parity-тесты могут вводить в заблуждение. Зафиксировать в `parity_matrix.md`.

---

## Раздел C — ОРД (предварительно; полная таблица — `docs/ord/DIFF.md`)

Клиент `backend/ord/ozon.py` писался до получения спецификации (в docstring: «пути … помечены для финальной сверки»; `docs/ord_checklist.md:29` — «Частично»). Сверка со swagger показывает, что **ни один POST регистрации не попадёт в существующий эндпоинт**: production/sandbox-цепочка в нынешнем виде полностью нерабочая (все сущности уйдут в `failed`). Mock-провайдер и гейты при этом корректны — поэтому тесты зелёные.

| № | Метод | Код | Swagger | P |
|---|---|---|---|---|
| F-C1 | организация | `POST /api/external/organisation`; поля `fullOpf`, `legalAddress{}`, `postAddress{}`, `ogrn/ogrnip`, `fio` | `POST /api/external/v6/organisation`; **required** `name, externalOrganisationId, inn, isOpc, isPp`; `address` — строка; полей ogrn/fio нет | P0 |
| F-C2 | договор | `POST /api/external/contract`; `withNds` | `POST /api/external/v6/contract`; `withNds` в схеме нет | P0 |
| F-C3 | креатив | `POST /api/external/creative`; ККТУ отправляется в `okvedCodes`; `paymentType`, `isNative` | `POST /api/external/v4/creative`; ККТУ — **`kktuCategory: string[]`** (обязателен с 13.01.2025); `paymentType`/`isNative` в v4 нет; `mediaData[].file` = `{id, url, originalUrl, fileType, size, sizeType}`, `description` в mediaData нет | P0 |
| F-C4 | площадка | `POST /api/external/platform` | `POST /api/external/v6/platform` | P0 |
| F-C5 | статистика | `POST /api/external/v2/statistic`; `moneySpent`, `withNds` | `POST /api/external/v6/statistic`; **required** `paymentType`, `price{amount, excludingAmount, vatRate}`; `moneySpent`/`withNds` нет; доступны `externalContractId`, `externalInvoiceId` | P0 |
| F-C6 | акт | `POST /api/external/v2/invoice`; `price`, `withNds`, `contracts[].price` строкой | `POST /api/external/v7/invoice`; сумма — `servicePrice{amount, excludingAmount, vatRate}`, `contracts[].price` — объект `{amount, excludingAmount, vatRate}`; есть `invoiceType` | P0 |
| F-C7 | ошибки ЕРИР | `POST .../notification/error_list` с `{pageSize, gtNotificationId}` | `GET .../notification/error_list` без параметров; `id` в ответе — **string** (мы делаем `int(n["id"])`) | P1 |
| F-C8 | ping | `POST /api/external/organisation/list` | `POST /api/external/v6/organisation/list` (404 будет выглядеть как «ключ неверный/ОРД недоступен») | P1 |
| F-C9 | retry | 429 не ретраится | лимиты в swagger не описаны; добавить backoff на 429 | P2 |
| F-C10 | ККТУ в UI | `frontend/lib/kktu.json` из `data/kktu-source.xlsx` (294 листа) | источник истины — `POST /api/external/dict/kktu`; сверить коды | P1 |
| F-C11 | invoice link/unlink v3 | пути и поля совпадают | — | ✅ |

НДС: по `ext_v6Price/ext_v7Price.vatRate` допустимы `100` (без НДС), `0, 5, 7, 10, 20, 22`. В коде НДС передаётся только булевым `withNds` — нужен расчёт `amount / excludingAmount / vatRate` из `money.py` (ставка 22%).

Правка затрагивает только адаптер `ozon_mapping.py` + пути в `ozon.py` (+ тесты `test_ord_ozon_mapping.py`). Логика цепочки, состояния, гейты, freeze полей не меняются — соответствует C.3.

### Раздел C — итог (ветка `feat/predeploy-c-ord`)
- Закрыты все P0/P1 из таблицы выше. Полная таблица и решения — `docs/ord/DIFF.md` (C1–C30).
- Найдено дополнительно при контрактном тесте: `withNds` в договоре v6 (C7), `objectType` ошибок ЕРИР приходит как `ERROR_NOTIFICATION_OBJECT_TYPE_*` и раньше всегда становился `unknown` (C23), неверный хост sandbox `ord-sandbox.ozon.ru` вместо `api-ord-sandbox.ozon.ru` (C25).
- **Отступление от DoD «тесты без изменения ожиданий»:** 11 unit-тестов (`test_ord_ozon_mapping.py`, `test_ord_invoice_ors_unit.py`, `test_ord_notifications_unit.py`) закрепляли прежние (неверные) пути и формы тел. Их ожидания обновлены на swagger, смысл проверок сохранён; новый `test_ord_swagger_contract.py` проверяет тела против спеки.
- C29 (сверка ККТУ) и C30 (реквизиты иностранных контрагентов) закрыты в коде; sandbox E2E C.4 и сверка ККТУ на реальном справочнике — по кнопке в Настройках, нужен тестовый ключ.
