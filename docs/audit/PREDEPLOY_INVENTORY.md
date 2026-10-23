# Pre-deploy inventory — ID sync / Гео / ОРД (Шаг 0)

База: `feat/avito-ui-kktu` @ `121fd69`. Рабочая ветка: `feat/predeploy-idsync-geo-ord`.
Код на этом шаге **не менялся**. План работ: `docs/audit/AGENT_TASK_predeploy_idsync_geo_ord.md`.
Спецификация ОРД: `docs/ord/swagger_ord_ozon.json` (OpenAPI 3.0.0, 43 операции).
Расхождения с планом и дефекты — в `docs/audit/PREDEPLOY_FINDINGS.md`.

Продакшн-биддер — Go (`Caddyfile:35-38`: `/rtb/*` → `bidder-go:8090`). Python `backend/routers/rtb.py`
содержит эталонную (parity) реализацию той же логики таргетинга.

---

## Q1. ID matching / cookie sync

| Вопрос | Ответ | Где |
|---|---|---|
| Эндпоинт sync (`/sync/*`, usersync) | **Нет.** Единственное совпадение по `/sync` — `POST /ord/notifications/sync` (это ОРД, не cookie sync) | `backend/routers/ord_router.py:211` |
| Таблица/ключи соответствия ID | **Нет** (ни в миграциях 000–057, ни в Redis) | — |
| Cookie DSP (`dsp_uid`) | **Нет** | — |
| Парсинг `user.buyeruid / user.eids / device.ifa` в Go | **Есть**: `User{ID, BuyerUID, EIDs[]{Source, UIDs[]{ID, AType}}}`, `Device.IFA` | `bidder-go/internal/rtb/models.go:192, 208-227` |
| Идентификация пользователя в Go | Функция `deviceIDOf` — порядок: `device.ifa` → первый непустой `user.eids[*].uids[*].id` (любой source) → `user.id` → `user.buyeruid` | `bidder-go/internal/rtb/handler.go:1358-1379` |
| Где используется результат | 1) аудитории: `audienceOK` → `ZSCORE audz:{segment} {deviceID}`; 2) freq cap: `Reserve(... DeviceID ...)` (Lua); 3) `clidmap:{bid_id}` → deviceID (атрибуция кликов); 4) поле `fdev` в bid meta | `handler.go:324, 452, 562, 805, 858-859, 894-910` |
| Использует ли bidder `buyeruid` при проверке аудитории | **Да, но последним** (после `user.id`), без namespace по партнёру | `handler.go:1371-1376` |
| Хранилище аудиторий | Postgres `aud:` + Redis `audz:{segment_id}` ZSET `device_id → expires_at`; rebuild — `backend/scripts/redis_rebuild_audiences.py` | `backend/audiences.py:16-34, 76-88, 187-227` |
| Сущность партнёра | `supply_partners` (baseline + ALTER в 011, 020, 030, 032, 039, 043, 046). Расширять её, а не создавать новую | `backend/migrations/000_baseline.sql:123-136` |
| Scheduler для джоб очистки | Есть (`scheduled_jobs`, `backend/scheduler.py`, миграция 053) | `backend/migrations/053_scheduled_jobs.sql` |
| Python-эталон | deviceID = `device.ifa` или `device.ip` (Go IP не использует — расхождение parity, см. FINDINGS F-A3) | `backend/routers/rtb.py:931` |
| Prefilter | `no_device_id` — отказ, если нет ни ifa, ни ip/ipv6 (`RequireDeviceID`) | `bidder-go/internal/prefilter/prefilter.go:148`, `backend/prefilter.py:182` |

Итог Q1: по таблице A.2 плана — «модель есть → не трогать модель, только добавить использование», «эндпоинтов нет → A.3–A.5», «аудитории `audz:` по device_id → добавить резолв identity_key, старый путь не ломать», «partner config есть → расширять `supply_partners`».

---

## Q2. Гео

### Цепочка UI → API → DB → feed → Go

| Звено | Что происходит | Где |
|---|---|---|
| Справочник (бэкенд) | Статический Python-модуль: 64 страны (`label_ru, label_en, ISO alpha-2`), `RU_REGIONS` (список названий), `CITIES` (`label_ru, label_en, alpha-2`) | `backend/geo_data.py` (130 строк) |
| Справочник (фронт) | **Второй, независимый** список: `RU_REGIONS`, `REGION_CITIES` (регион → города), `OTHER_COUNTRIES` (название → ISO) | `frontend/components/geo-panel.tsx:71, 96, 185` |
| Autocomplete | `GET /geo/search?q=` — подстрочный поиск по статическим спискам, лимит ≤ 50, без пагинации | `backend/routers/geo.py` |
| UI выбора | `GeoDrawer` (панель «как на Авито»): регионы, города, другие страны | `frontend/components/geo-panel.tsx:217-316` |
| Сохранение | `onApply`: `geo_regions = [названия]`, `geo_cities = [названия]`, `geo_countries = ["RU" (если выбраны регионы/города)] + [alpha-2 других стран]` | `frontend/components/targeting-editor.tsx:397-406` |
| Формат хранения | `line_items.targeting` JSONB: `geo_countries` (**ISO alpha-2**), `geo_regions` (**русские названия**), `geo_cities` (**русские названия**). Валидация — только «список строк» | `backend/targeting_contract.py:27-29` |
| Feed | Передаётся как есть: `feed.Targeting{GeoCountries, GeoRegions, GeoCities}` | `bidder-go/internal/feed/feed.go:103`, `handler.go:1457` |
| Нормализация запроса | `Country = ToUpper(device.geo.country)`, `Region = ToLower(device.geo.region)`, `City = ToLower(device.geo.city)` — больше никакой | `handler.go:1227-1231` |
| Сравнение | `Match`: точное равенство после upper/lower, **три независимых AND-условия** (страна И регион И город) | `bidder-go/internal/targeting/targeting.go:158-167, 536-544`; Python — `rtb.py:147-155` |
| Блок по гео в scoring | `snap.Blocked(campaign, "geo_country", view.Country)` | `handler.go:664` |
| Prefilter | `THROTTLE_COUNTRIES` (по умолчанию `ru,rus`) — принимает обе формы | `prefilter.go:82-88, 142-143`, `backend/prefilter.py:48` |
| Связь с ОРД | `ord/service.py` решает «нужен ли erid» по `geo_countries` (пусто или содержит `RU`) | `backend/ord/service.py:23-31, 98-99` |

### Ответы
- **Поля запроса**: используются только `device.geo.country / region / city`. `lat/lon` парсятся (`models.go:203-204`), но не используются; `geo.type` и IP-гео не используются.
- **Если гео в запросе нет** и у line item есть гео-таргет: `containsFold(list, "")` → false → **no-bid** (фактически режим `strict`). Без гео-таргета — торгуем.
- **Нормализация названий**: нет (ни ё/е, ни «г.», ни латиница/кириллица, ни ISO-3166-2 коды регионов).
- **Известный долг**: `docs/audit/parity_matrix.md:15` — «Geo country (alpha-3→alpha-2) … 🟡 (без нормализации) | TODO в Go: `_normalize_country`» — не закрыт (и в Python нормализации тоже нет, `rtb.py:148`).

---

## Q3. ОРД — модули и вызываемые методы API

Модули: `backend/ord/{adapter, chain, invoices, mock, notifications, ozon, ozon_mapping, platform, service}.py`, роутер `backend/routers/ord_router.py`. HTTP-клиент — только `backend/ord/ozon.py`; маппинг полей уже вынесен в один адаптер `ozon_mapping.py` (требование C.3 выполнено структурно).

| Наш метод (`ozon.py`) | Вызов в коде | Актуально по swagger | Статус |
|---|---|---|---|
| `ping` (:71) | `POST /api/external/organisation/list` | `POST /api/external/v6/organisation/list` | ❌ путь отсутствует в swagger |
| `register_counterparty` (:90) | `POST /api/external/organisation` | `POST /api/external/v6/organisation` | ❌ |
| `register_contract` (:94) | `POST /api/external/contract` | `POST /api/external/v6/contract` | ❌ |
| `register_creative` (:98) | `POST /api/external/creative` | `POST /api/external/v4/creative` | ❌ |
| `register_platform` (:105) | `POST /api/external/platform` | `POST /api/external/v6/platform` | ❌ |
| `list_error_notifications` (:109) | `POST /api/external/notification/error_list` + body | `GET /api/external/notification/error_list` (без параметров) | ❌ метод HTTP |
| `register_invoice` (:132) | `POST /api/external/v2/invoice` | `POST /api/external/v7/invoice` (v6 удалён 01.10.2026, v2 нет) | ❌ |
| `link_invoice_creatives` (:138) | `POST /api/external/v3/invoice/creative/unlink` → `.../link` | те же пути v3 | ✅ путь (схему сверить) |
| `submit_stats` (:150) | `POST /api/external/v2/statistic` | `POST /api/external/v6/statistic` | ❌ |

Не используются: `dict/*` (ККТУ/ОКСМ/ОКВЭД/ФИАС/банки), `file/{bucket}`, `delete_operation/*`, `v5/cid*`, `feed`, `address/correct`, все `GET` / `list` (кроме ping).

Аутентификация: `Authorization: Bearer <api_key>` (`ozon.py:42-45`) — соответствует разделу Auth swagger (в `components.securitySchemes` схема не объявлена, описана текстом).
Retry: 3 попытки, backoff 1/2/4 c на 5xx и сетевых; 4xx не ретраится; **429 не обрабатывается отдельно** (уходит в 4xx → `OrdDataError`).
Тело ошибки по swagger — `rpcStatus {error: string}` для всех `default`-ответов.

Подробная сверка полей — `docs/ord/DIFF.md` (PR-1, раздел C). Предварительно найденные расхождения схем — в FINDINGS (F-C*).
