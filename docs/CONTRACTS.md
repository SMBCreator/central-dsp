# Контракты между слоями

Что именно связывает UI, FastAPI, Postgres, Redis и Go-биддер, и какой тест
падает, если контракт нарушен. Правило: **меняешь одну сторону — прогоняешь
`backend/tests/test_targeting_contract.py` и `test_redis_contracts.py`**, они
парсят исходники соседних слоёв и не требуют запущенных сервисов.

## 1. `line_items.targeting` (UI → Pydantic → JSONB → Go)

| Слой | Где | Что проверяется |
|---|---|---|
| UI | `frontend/lib/types.ts: Targeting`, `components/targeting-editor.tsx` | имена/типы полей = контракт; у каждого ключа есть контрол |
| API | `backend/targeting_contract.py: TARGETING_KEYS`, `schemas.LineItemIn/Patch` | **источник правды**; неизвестный ключ / неверный тип / диапазон → 422 с русским текстом |
| DB | `line_items.targeting JSONB` | хранится как есть после валидации |
| Go | `bidder-go/internal/feed/feed.go: Targeting`, `rtb/handler.go: feedTargetingToMatch` | json-теги и Go-типы = контракт; каждое поле читается в handler |

Семантика пустого значения: `[]`, `0`, `false`, отсутствие ключа — «нет
ограничения». Диапазоны: `hours 0..23`, `days 0..6` (0 = Пн), `banner_positions
0..7`, `video_placements 1..5` (OpenRTB 2.6 plcmt), `video_protocols 1..14`,
`viewability_min 0..100`, `age_* 0..120`, списки ≤ 5000 элементов.

Удалённые ключи: `ad_types` — формат задаётся креативом; API отвергает явно.

## 2. Redis: кто пишет, кто читает, TTL

| Ключ | Тип | Пишет | Читает | TTL | Назначение |
|---|---|---|---|---|---|
| `spend:camp:{campaign_id}:{YYYY-MM-DD}` | string (money) | Go Lua-гейт, Python `cache.py` | оба, `clear_stale_daily_spend` | до 00:00 Europe/Moscow + 1 ч grace | дневной расход кампании за бизнес-день (дата МСК в ключе) — **единственный источник бюджета на торгах** |
| `spend:li:{li_id}:{YYYY-MM-DD}` | string (money) | Go Lua-гейт, Python | оба | до 00:00 Europe/Moscow + 1 ч grace | дневной расход группы за бизнес-день |
| `bidmeta:{bid_id}` | JSON | Go (`persistBidMeta`), Python-биддер | Python `win/imp/click` (`_find_bid`), Go `loss` | 1 ч | биллинг выигрыша; `is_test` блокирует списание |
| `bidfreq:{bid_id}` | string `device|caps` | Go, Python-биддер | Python `/imp` | 1 ч | инкремент frequency-cap после показа |
| `lossdone:{bid_id}` | flag (SETNX) | Go `loss` | Go | 1 ч | идемпотентность возврата бюджета по loss |
| `rtb:events` | stream (MAXLEN ~200k) | Go `EmitEvent` (bid, loss) | Python `events_consumer` (группа `backend`) | обрезка по длине | `bid_events` + `stats_hourly.bids` |
| `freq:{campaign_id}:{device}` / `...:{hours}h:{device}` | counter | Python `/imp` | Go, Python при биде | `hours*3600` | frequency capping |
| `qps:{partner_id}:{sec}` | counter | Python | Python | 2 с | лимит QPS SSP |
| `aud:{segment_id}` | SET device_id | Python `audiences.py` | Go `audienceOK`, Python | `ttl_days` | аудиторные сегменты (`aud:buyers`, `aud:churned`) |
| `clidmap:{bid_id}` | string device_id | Go, Python-биддер | Python `audiences.resolve_clid` (пиксель) | 7 д | привязка клика к device_id для наполнения сегментов |
| `once:{kind}:{token}` | flag (SETNX) | Python | Python | по вызову | одноразовые токены (event-token replay) |
| `rl:login:email:{email}` / `rl:login:ip:{ip}` / `rl:{scope}:{key}` | counter | Python | Python | окно лимита | rate-limit логина/API |
| `px:dd:{pixel}:{fp}` / `px:rl:*` | flag/counter | Python `pixel_guard` | Python | 2–60 с | дедуп и лимиты пикселя |
| `partner:{endpoint_key}` | JSON | Python | Python-биддер | — | кеш SSP-партнёра (legacy путь) |
| `fx:rates` | JSON | Python `fx.py` | Python | 24 ч | курсы валют |
| `prefilter:dc_subnets` | SET | Python | Python | — | подсети дата-центров для префильтра |
| `vev:{event}` | counter | Python | Python | — | счётчики VAST-событий |

### 2.1 Схема `rtb:events`

Все значения — строки (XREADGROUP). Go форматирует числа через `strconv`
(тест `test_stream_values_are_strings_in_go`).

Общие поля (`bid` и `loss`): `type`, `bid_id`, `campaign_id`, `line_item_id`,
`creative_id`, `supply_partner_id`, `price`, `device_type`, `geo_country`,
`ad_type`, `domain`, `os`, `browser`, `is_test` ("0"/"1"), `ts` (unix sec).

Только `bid`: `imp_id`, `bidfloor`, `geo_region`, `geo_city`, `bundle`,
`publisher_id`, `tagid`.

Только `loss`: `loss_reason` (OpenRTB lossreason code), `min_to_win`.

Consumer: `events_consumer.normalize()` → `bid_events` (24 колонки, см.
`_INSERT_COLS`), плюс агрегат `stats_hourly.bids` по (hour, campaign, li,
creative, ssp) для не-test bid.

### 2.2 Схема `bidmeta:{bid_id}`

JSON: `bid_id`, `imp_id`, `campaign_id`, `line_item_id`, `creative_id`,
`supply_partner_id`, `price` (наша ставка, потолок для clearing price),
`device_type`, `geo_country`, `ad_type`, `domain`, `os`, `browser`, `is_test`
(0/1). Python-биддер пишет тот же набор — поведение win-notice не зависит от
того, какой биддер ответил (`test_python_bidmeta_writer_matches_go_bidmeta`).

## 3. Кто пишет `stats_hourly`

| Колонка | Писатель | Триггер |
|---|---|---|
| `bids` | `events_consumer.py` (агрегат батча) | событие `bid` из `rtb:events`, не test |
| `impressions`, `spend`, `clicks` … | `routers/rtb.py` (`/win`, `/imp`, `/click`) | биллинг через `billing_core.bill_win` |
| pixel-метрики | `routers/pixel.py` | пиксель сайта |

Legacy Python-биддер (`rtb.py:553`) тоже инкрементит `bids` — в проде он
выключен (`bidder-go` единственный), на dev при параллельном запуске цифры
задвоятся.

## 4. Lua-гейт бюджета

Python `cache.py:_GATE_LUA` и Go `redisx.go:luaGateAndReserve` — один и тот
же скрипт (`test_gate_lua_is_byte_identical_between_python_and_go`). Возврат
по loss (`ReleaseGate`, только Go) — по существующим ключам, clamp 0, TTL
сохраняется (`redisx_test.go: TestReleaseGate_*`).

## 5. Наценка и представление отчётности (audit F-02, `backend/stats_math.py`)

**Хранение.** `stats_hourly.spend`, `domain_stats_hourly.spend`,
`bid_events.clear_price`, `transactions.amount` — **net, в валюте платформы
(RUB)**. Курс (`fx.py`) применяется один раз, в момент win, дальше только RUB.

**Наценка — товарная цепочка.** `users.markup_pct` — процент, который родитель
назначил пользователю; `rbac.effective_markup_pct(user)` — произведение по
цепочке вверх = «цена, которую видит этот пользователь». Каждый уровень видит
net × свою цепочку; никто не видит цену уровня ниже себя. Множитель
вычисляется в одном месте (`routers/stats._resolve_scope`; share/guest — цена
владельца доступа, с B14b — snapshot на доступе) и применяется одним вызовом
`present_spend(net, mult)`. Money-метрики, производные от spend (cpm/ecpm, cpc,
cpa, avg_bid, avg_bidfloor, avg_min_to_win), — в той же цене. Операционные
отчёты владельца инвентаря (`/supply-partners/{id}/stats`, скоринг) — net,
наценка не применяется намеренно.

**Кто может добавлять наценку сверх своей цены** (ссылки/гости, B14b):
superadmin, admin, agency. manager (его наценка задана admin-ом в
`users.markup_pct`), advertiser, client, viewer — нет.

**Одна арифметика.** Производные метрики — только `stats_math.derive()`:
деньги `MONEY_DECIMALS=4`, проценты `PCT_DECIMALS=2`, CTR `CTR_DECIMALS=3`.
`conversions=None` означает «для этого среза недоступно» (cpa=None), `0` —
«не было». `losses=None` — источник (hourly) не измеряет lurl.

**Отчёт-конструктор (`POST /stats/report`).**
* `totals` — отдельный запрос тем же фильтром без GROUP BY/LIMIT.
* Не-временные группировки сортируются `spend DESC, impressions DESC, bids DESC`;
  хвост за `limit` сворачивается в строку `«Прочее»` (`kind=other`), аддитивные
  метрики = totals − показанные, так что `sum(rows) == totals` всегда; средние
  цены для свёртки не определены (null). `meta.truncated`, `meta.limit`.
* NULL-значение измерения — строка `«Не указано»` (`kind=not_set`), а не тире;
  конверсии с NULL-измерением остаются в этой строке, не теряются.
* Конверсии в raw-источнике — из `conversions` для измерений, которые
  атрибуция кладёт в строку конверсии (время, campaign, creative, supply_partner,
  country, device, domain); для остальных (os/browser/ad_type/tagid/publisher/
  bundle/loss_reason) `conversions=null`, `meta.conversions_available=false` —
  до слоя `conversion_attributions` (план B14c).
* Лимиты: `hours ≤ MAX_HOURS (8784)`, `limit ≤ MAX_LIMIT (5000)`, диапазон дат
  ≤ 366 дней, `date_from > date_to` → 422 (не swap).

Контракт закреплён `tests/test_stats_presentation.py` (в т.ч. проверка, что в
роутерах не осталось ad-hoc `round(... * 1000)`/`* 100` расчётов).
