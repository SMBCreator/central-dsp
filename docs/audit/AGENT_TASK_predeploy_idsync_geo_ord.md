# AGENT TASK: Pre-deploy доработка central-dsp (Cookie Matching, Гео, ОРД)

Ветка-основа: `feat/avito-ui-kktu` (HEAD на момент постановки: `121fd69`). Рабочая ветка: `feat/predeploy-idsync-geo-ord`.
Документация ОРД: `docs/ord/swagger_ord_ozon.json` (положить в репо первым коммитом).

## 0. ГЛАВНЫЕ ПРАВИЛА (нарушать нельзя)

1. **Не переписываем, а дорабатываем.** Существующую логику ОРД (цепочка, жизненный цикл, erid-гейты, freeze юр. полей), биллинг, Redis Lua, контракт bidder feed, RBAC — не менять без явного дефекта.
2. Все новые функции — за флагами, по умолчанию OFF: `ID_SYNC_ENABLED`, `GEO_V2_ENABLED`.
3. Миграции только аддитивные (новые таблицы/колонки nullable), с down-скриптом. Старые значения кампаний не мигрируем.
4. Hot path bidder: никаких обращений к PostgreSQL и сетевых вызовов вне Redis. p99 не должен ухудшиться (замер до/после).
5. Один PR на раздел (A, B, C). Каждый PR: описание, список затронутых файлов, тесты, инструкция отката.
6. Если находка по факту кода противоречит этому плану — остановиться, записать в `docs/audit/PREDEPLOY_FINDINGS.md`, не «додумывать».
7. Никаких секретов в коде/логах/тестах. Чужие user ID в логах только в хэшированном виде.
8. Реальных bid request от SSP у нас нет → тестируем на синтетических fixtures по спецификации OpenRTB 2.5/2.6 и документации конкретного SSP, при подключении SSP — запросить у него образцы (см. раздел A.9).

## 1. ШАГ 0 — ИНВЕНТАРИЗАЦИЯ (без правок кода)

Выполнить и сохранить результат в `docs/audit/PREDEPLOY_INVENTORY.md` (пути файлов, функции, таблицы, строки):

```bash
grep -rniE "buyeruid|\.eids|eids|device\.ifa|\bifa\b|user\.id|cookie.?sync|usersync|id_sync|/sync" backend bidder-go frontend
grep -rniE "geo|region|city|country|metro|geonames|iso_3166|device\.geo|fias|kladr" backend bidder-go frontend
grep -rniE "ord_|erid|ozon|kktu|invoice|statistic" backend --include=*.py -l
```

Ответить письменно:
- Q1. Есть ли эндпоинт sync? Таблица/ключи соответствия ID? Cookie DSP? Использует ли bidder `buyeruid` при проверке аудитории?
- Q2. Где хранится список гео (БД/JSON/фронт-константа)? Как он попадает в feed? Где в Go проверяется? Какие поля запроса сравниваются (`device.geo.country/region/city`, lat/lon, IP-geo)? Что происходит, если у запроса гео нет?
- Q3. Список модулей ОРД и вызываемых ими методов API (см. раздел C).

## 2. РАЗДЕЛ A — COOKIE MATCHING / ID SYNC

### A.1 Суть (чтобы агент не ошибся в постановке)
- ID matching связывает `ID пользователя в SSP` ↔ `ID пользователя в DSP`. Это **не** даёт доступа к аудиториям VK/любого SSP автоматически: аудитории VK доступны только через договорённость с VK/SSP (передача сегментов, matching API, deal с аудиторным таргетингом).
- В OpenRTB: `user.id` — ID у биржи; `user.buyeruid` — ID, уже сопоставленный биржей именно для нашей DSP; `user.eids[]` — внешние ID (source + uids); `device.ifa` — рекламный ID мобильного.
- Если SSP присылает `buyeruid` — sync уже сделан по схеме SSP. Если присылает только свой `user.id` — можно использовать его только в пространстве имён этого SSP (frequency cap, сегменты внутри партнёра), без cross-SSP связки.
- Safari/Firefox/Brave режут third-party cookie → долю cookieless нужно измерять, а не предполагать.

### A.2 Что проверить в текущем коде (Шаг 0) и решение
| Находка | Действие |
|---|---|
| Парсинг `buyeruid/eids/ifa` в Go-модели есть | Не трогать модель, добавить только использование в идентификации (A.6) |
| Эндпоинтов sync нет | Реализовать A.3–A.5 |
| Аудитории хранятся как `audz:{segment}` по device_id | Добавить резолв `identity_key` (A.6), старый путь не ломать |
| Есть partner config (supply partners) | Расширить колонками/JSON-полем, не создавать параллельную сущность |

### A.3 Эндпоинты (за флагом `ID_SYNC_ENABLED`)
Публичные, без cookie основной сессии, лучше на отдельном домене/поддомене (`sync.<domain>`), cookieless для основного приложения.

1. `GET /sync/{partner_key}/in` — SSP → DSP: SSP вызывает наш пиксель, передаёт свой ID макросом (имя параметра задаётся в конфиге партнёра, напр. `ssp_uid`). Мы читаем/создаём `dsp_uid`, сохраняем соответствие, возвращаем 302 на SSP callback (если у партнёра задан) либо 1x1 gif.
2. `GET /sync/{partner_key}/out` — DSP → SSP: редирект на sync URL SSP с нашим `dsp_uid` (для SSP, где инициатор мы).
3. `GET /sync/optout` и `POST /sync/delete` — opt-out и удаление записей по `dsp_uid`.

Требования:
- Cookie `dsp_uid`: `Secure; SameSite=None; HttpOnly; Max-Age` из конфига (по умолчанию 90 дней), значение — случайный 128-bit (CSPRNG), не производное от чужих ID.
- Redirect — только на домены из allowlist партнёра (`sync_redirect_hosts`), параметры подписаны HMAC (`sig`, `ts`, TTL 10 мин), запрет open redirect.
- Rate limit по IP и по partner_key; ответ всегда 200/302/204 без раскрытия, найден ли ID.
- Кэширование запретить (`Cache-Control: no-store`), заголовок `Referrer-Policy: no-referrer`.
- Параметры согласия: принимать `gdpr`, `gdpr_consent`, `us_privacy` (если SSP передаёт); при отсутствии согласия там, где оно обязательно, mapping не создавать.
- Размер/формат входного ID валидировать (длина, charset), хранить хэш (`sha256(partner_salt||ssp_uid)`).

### A.4 Хранилище
Миграция (аддитивная):
```sql
CREATE TABLE id_sync_map (
  partner_id    uuid NOT NULL REFERENCES supply_partners(id),
  ssp_uid_hash  bytea NOT NULL,
  dsp_uid       text  NOT NULL,
  consent_scope text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  PRIMARY KEY (partner_id, ssp_uid_hash)
);
CREATE INDEX ON id_sync_map (dsp_uid);
CREATE INDEX ON id_sync_map (expires_at);
```
(Имя таблицы `supply_partners` подставить по факту Шага 0.)
Hot-копия Redis: `idsync:{partner_id}:{hex(hash)}` → `dsp_uid`, TTL = `expires_at`. Запись: PostgreSQL (durable) → Redis. Восстановление Redis из PostgreSQL — отдельная джоба (`rebuild_idsync`).
Очистка просроченных — scheduler job, а не cron на хосте.

### A.5 Конфигурация партнёра
Добавить в supply partner (nullable поля или jsonb `identity`):
`sync_enabled`, `sync_in_param`, `sync_callback_url_template`, `sync_redirect_hosts[]`, `id_source` (`buyeruid|eids:<source>|user_id|ifa`), `ttl_days`, `requires_consent`.
UI: отдельная секция в карточке SSP (формы create/edit должны быть симметричны — проверить).

### A.6 Bidder (Go): порядок идентификации
Не менять существующий путь, а добавить функцию `resolveIdentity(req, partner)`; порядок:
1. `user.buyeruid` (если валиден),
2. `user.eids[*]` по настроенному `source`,
3. `device.ifa` (mobile app, не нулевой и не `0000…`),
4. lookup `idsync:{partner}:{hash(user.id)}` в Redis → `dsp_uid` (только если у партнёра включено),
5. `user.id` в namespace партнёра `p:{partner_id}:{user.id}`,
6. анонимный запрос.
Результат — `identity_key` + `identity_source`. Он используется для audience membership и frequency cap. Lookup в Redis ≤ 1 вызов, с жёстким таймаутом; при ошибке — переход к анонимному, но не no-bid.

### A.7 Метрики и алерты
`id_sync_requests_total{partner,status}`, `id_sync_mappings_created_total{partner}`, `id_sync_redirect_rejected_total`, `id_sync_consent_denied_total`, `bid_requests_identity_source_total{partner,source}`, `bid_requests_anonymous_ratio`, `audience_match_total{partner,result}`, `idsync_lookup_latency_seconds`.
Алерт: падение доли идентифицируемых запросов у партнёра > X% к 7-дневной норме.

### A.8 Тесты
- Unit: валидация параметров, подпись редиректа, allowlist, TTL.
- Integration: полный цикл in → mapping → bid request с `user.id` → resolve → audience match.
- Security: open redirect, oversized ID, replay подписи, SQL/CRLF-инъекции в параметрах, отсутствие cookie основного приложения на sync-домене.
- Негативные: Redis недоступен (bid идёт анонимно), невалидный consent.
- Нагрузка: sync endpoint не влияет на latency bidder (отдельный процесс/лимиты).

### A.9 Что запросить у каждого SSP (чек-лист, т.к. реальных запросов нет)
1. Образец bid request с блоками `user`, `device`, `regs`, `ext`.
2. Какое поле несёт пользовательский ID, его стабильность и срок жизни.
3. Поддерживается ли `buyeruid` и формат sync URL (макросы, направление).
4. Доля cookieless/мобильного трафика, список EID-провайдеров.
5. Как передаётся согласие/ограничения по ПДн в РФ.
6. Как получить тестовый user для E2E.
7. Для VK: договор/продукт, через который доступны аудиторные сегменты (matching API, deal, передача сегментов).

### A.10 Правовой блок (не кодовый, но блокирующий для включения флага)
Устойчивые cookie/device ID для таргетинга оцениваются юристом как возможные персональные данные (152-ФЗ): основание обработки, согласие, политика cookie, локализация, retention, opt-out, договоры с SSP. До юридического ОК `ID_SYNC_ENABLED` в production = false.

### A.11 Критерий приёмки A
- Флаг OFF → поведение платформы побайтово прежнее (регресс-тесты зелёные).
- Флаг ON на stage → E2E цикл проходит, метрики видны, p99 bidder без деградации.
- Документ `docs/ID_SYNC.md` с схемой, настройкой партнёра и runbook (откат: флаг OFF + очистка таблицы).

## 3. РАЗДЕЛ B — ГЕО

### B.1 Что выяснить (Шаг 0) и зафиксировать в `docs/GEO.md`
1. Источник списка (регионы + несколько крупных городов).
2. Формат хранения в line item/campaign (названия? коды? ID?).
3. Как формируется feed и как Go сопоставляет с запросом.
4. Какие поля OpenRTB используются: `device.geo.country` (ISO-3166-1 alpha-3 по стандарту), `device.geo.region` (ISO-3166-2 или текст — зависит от SSP), `device.geo.city` (текст), `lat/lon`, `type` (источник гео: GPS, IP и т.д.), `device.ip`.
5. Поведение при пустом гео (сейчас: пропуск/отказ?) — зафиксировать и сделать настраиваемым.
6. Нормализация названий (ё/е, регистр, «г.», латиница/кириллица).

### B.2 Как работает привязка к SSP (для документа)
Таргетинг не привязан к SSP напрямую: DSP получает bid request, берёт географию из полей запроса и сравнивает с таргетом кампании. Разные SSP заполняют `region/city` по-разному (коды vs названия, язык, глубина), поэтому нужен **слой нормализации и маппинга по партнёру**, а не один список строк.

### B.3 Доработка (за флагом `GEO_V2_ENABLED`, обратно совместимо)
1. Справочник `geo_places` (аддитивная таблица): `id, country_iso, region_code (ISO-3166-2), name_ru, name_en, aliases text[], type (region|city|town), lat, lon, population, parent_id, source`.
2. Источник данных: открытый справочник (GeoNames и/или ФИАС/ОКТМО для РФ). Лицензию источника проверить и записать в `docs/GEO.md`. Импорт — скрипт `ops/geo_import.py`, идемпотентный, с версионированием справочника.
3. Таблица `geo_ssp_alias (partner_id, ssp_value, place_id)` — маппинг значений конкретного SSP на наш `place_id`; заполняется по образцам запросов при подключении SSP; без образца используется нормализованное название + aliases.
4. Таргет кампании: добавить новое поле (`geo_place_ids[]`) рядом со старым. Старые значения остаются работать; при `GEO_V2_ENABLED=true` bidder проверяет оба.
5. Go: предвычисленные в feed структуры (set `place_id`, ancestor-цепочка город → регион → страна), O(1) проверка; в hot path только map lookup, без строкового fuzzy-поиска. Нормализация названия запроса — кэшируемая функция.
6. Приоритет сопоставления: код региона (ISO-3166-2) → нормализованное название города/aliases → (опционально) ближайший город по lat/lon в радиусе (отдельный флаг `GEO_LATLON_ENABLED`) → иначе «unknown».
7. Режимы для unknown: `strict` (не торгуем) / `lenient` (торгуем, если включено в кампании). По умолчанию сохранить текущее поведение.
8. Исключения (negative geo): поддержать include и exclude; exclude приоритетнее.
9. UI: поиск по населённым пунктам (серверный autocomplete с пагинацией и лимитом), иерархический выбор «регион → города», отображение выбранного списком чипов, массовое добавление (список городов вставкой), лимит на количество объектов в кампании (оценить размер feed!).
10. Размер feed: измерить прирост; при превышении порога — перейти на включение по региону + города как исключения/уточнения.
11. Метрики: `bid_requests_geo_resolved_total{partner,level}` (city/region/country/unknown), `geo_unmatched_values_total{partner}` (топ непонятых значений — для пополнения aliases).

### B.4 Тесты
- Fixtures запросов с разными формами гео (код, название, кириллица, латиница, пусто, только IP).
- Include/exclude, иерархия, старые кампании без изменений, ё/е, регистр.
- Regression: результаты `GEO_V2_ENABLED=false` совпадают с прежними на наборе кейсов.
- Performance: Go benchmark проверки гео (не хуже прежнего значения).

### B.5 Критерий приёмки B
Документ с цепочкой UI → API → DB → feed → Go; справочник загружен; старые кампании не изменили поведение; новые таргеты работают на fixtures; прирост feed и p99 измерены.

## 4. РАЗДЕЛ C — ОРД (сверка со swagger Ozon ОРД)

### C.0 Принцип
Логику, порядок цепочки и состояния НЕ менять. Задача — сверить текущие модули с `swagger_ord_ozon.json`, исправить только расхождения. Уважать `docs/ord_checklist.md` и существующие audit-документы.

### C.1 Что описывает swagger (OpenAPI 3.0.0, 43 пути)
Окружения: sandbox `https://api-ord-sandbox.ozon.ru`, production `https://api-ord.ozon.ru`.
Сущности и актуальные версии методов:

| Область | Методы (версии из swagger) |
|---|---|
| Организации | `POST /api/external/v6/organisation`, `.../organisation/list`, `GET .../organisation/{id}`, `POST .../organisation/{id}/platform`, `.../platform/delete` |
| Площадки | `POST /api/external/v6/platform`, `.../platform/batch`, `.../platform/list`, `GET .../platform/{id}` |
| Договоры | `POST /api/external/v6/contract`, `GET .../contract/{id}`, список `POST /api/ord-api/api/external/contract/list` (нестандартный префикс — сверить!) |
| Креативы | `POST /api/external/v4/creative`, `GET .../creative/{id}`, `POST .../creative/list` |
| Фиды | `POST /api/external/v3/feed`, `.../feed/list`, `GET .../feed/{id}`, `POST .../feed/{id}/elements` |
| Статистика | `POST /api/external/v6/statistic`, `GET .../statistic/{id}`, `POST /api/external/v3/statistic/list` |
| Акты | `POST /api/external/v7/invoice`, `.../invoice/list`, `GET .../invoice/{id}`, `POST /api/external/v3/invoice/creative/link`, `.../unlink`, `GET .../invoice/creative` |
| Файлы | `POST /api/external/file/{bucket}`, `GET /api/external/file/{id}` |
| Справочники | `POST /api/external/dict/bank`, `dict/fias`, `dict/kktu`, `dict/oksm`, `dict/okved` |
| Уведомления | `GET /api/external/notification/error_list` |
| Удаление | `POST /api/external/delete_operation/create`, `entity_count`, `related_entities` |
| CID | `POST /api/external/v5/cid`, `v5/external_cid`, `v5/external_cid/list` |
| Адрес | `GET /api/external/v6/address/correct` |

### C.2 Алгоритм сверки (по каждому методу, который мы вызываем)
1. Найти в коде клиент ОРД (`backend/.../ord*`) и составить таблицу: наш метод → путь/версия → swagger.
2. **Версии путей:** сравнить с swagger (organisation v6, platform v6, contract v6, creative v4, statistic v6, invoice v7, feed v3, cid v5). Любой устаревший путь — дефект.
3. **Схемы запросов:** для каждого поля — имя, тип, обязательность (`required`), `enum`, `format` (date/date-time), `minLength/maxLength`, `pattern` (ИНН/ОГРН/КПП), вложенные объекты, `nullable`, `oneOf/anyOf`.
4. **Схемы ответов и ошибок:** статусы 200/400/401/403/404/409/422/429/5xx, структура тела ошибки, поле с идентификатором сущности, правила идемпотентности (внешние ID `externalXxxId`).
5. **Аутентификация:** схема безопасности из `components.securitySchemes`, формат заголовка/токена, срок жизни, обновление.
6. **Деньги и числа:** единицы (рубли/копейки), десятичные знаки, НДС, формат дат и часовой пояс, формат периода статистики (дневной/месячный).
7. **Справочники:** ККТУ, ОКСМ, ОКВЭД, ФИАС, банки — использовать dict-методы для проверки значений до отправки; сверить, что UI-списки (в т.ч. ККТУ на `feat/avito-ui-kktu`) соответствуют справочнику ОРД.
8. **Повторы и лимиты:** retry/backoff на 429/5xx, идемпотентность повтора POST (тот же `externalId`), таймауты.
9. **Файлы:** загрузка договора/креатива через `/file/{bucket}`: допустимые bucket, типы, размеры.
10. **Уведомления об ошибках:** `notification/error_list` — курсор/пагинация, как мы сопоставляем ошибки с сущностями, ретраим и показываем в UI.
11. **Удаление:** использовать `delete_operation` вместо ad-hoc логики, если мы удаляем в ОРД.
12. **CID/внешние CID:** проверить, используется ли в нашей модели erid/CID так, как требует v5.

Результаты записать в `docs/ord/DIFF.md`:
`№ | метод | поле/аспект | swagger | код | критичность (P0–P3) | решение`.

### C.3 Правила правок
- Исправляем только расхождения из DIFF.md. Состояния сущностей, гейты (запрет mock erid в prod, freeze полей, привязка договоров к владельцу) — не трогаем.
- Каждое исправление — тест на сохранённых примерах ответов (из примеров swagger, `examples`), без обращения к сети; плюс smoke-сценарий в **sandbox** при наличии тестовых учётных данных (вне CI).
- Маппинг внутренних полей → поля ОРД оформить в одном месте (адаптер), не размазывать по сервисам.
- Не логировать токены и персональные данные организаций/ИП (паспортные данные и пр. — маскировать).
- Production-режим ОРД остаётся выключен/guarded, пока не пройдёт sandbox-прогон цепочки: организация → площадка → договор → креатив → erid → статистика → акт.

### C.4 Sandbox E2E (чек-лист)
1. Создать организации (рекламодатель, рекламораспространитель), площадку.
2. Договор (включая загрузку файла).
3. Креатив → получение erid.
4. Статистика за период; повторная отправка (идемпотентность) и корректировка.
5. Акт (invoice) + привязка креативов; повторная подача.
6. Намеренные ошибки (невалидный ИНН, дубль externalId, неверный справочник) → проверка обработки через `error_list`.
7. Недоступность ОРД (таймаут/5xx) → поведение платформы (очередь/ретрай, остановка только затронутого трафика).

### C.5 Критерий приёмки C
`DIFF.md` заполнен, все P0/P1 закрыты, тесты зелёные, sandbox E2E пройден и приложен лог (без секретов), `ord_checklist.md` актуализирован.

## 5. ОБЩИЕ ТРЕБОВАНИЯ К ВЫПОЛНЕНИЮ

### Порядок работ
1. Шаг 0 (инвентаризация) → отчёт пользователю до начала правок.
2. PR-1: Раздел C (ОРД) — самый ограниченный по риску и самый «юридически» критичный.
3. PR-2: Раздел B (Гео).
4. PR-3: Раздел A (ID sync) — после юридического ОК и ответов SSP.
5. Финальный прогон всех тестов, нагрузка bidder до/после, обновление docs и runbooks.

### Definition of Done (общий)
- Все существующие тесты проходят без изменения ожиданий.
- Новые функции выключены по умолчанию и безопасно включаются/выключаются.
- Миграции применимы на копии прод-данных, откатываются.
- p99 bidder и размер feed измерены и не ухудшились сверх согласованного порога.
- Нет новых зависимостей без SBOM/аудита; secret-scan и dependency-scan зелёные.
- Документы: `docs/ID_SYNC.md`, `docs/GEO.md`, `docs/ord/DIFF.md`, обновлённые runbooks, `PREDEPLOY_FINDINGS.md`.

### Формат отчёта агента пользователю
По каждому разделу: что найдено (файлы/строки), что изменено, что намеренно не тронуто, как проверить, как откатить, остаточные риски.
