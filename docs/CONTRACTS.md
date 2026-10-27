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

### 1.1 Тематика площадки (`iab_categories`, `iab_block`)

Python-эталон — `backend/ctx_match.py` + `routers/rtb.py::_match_targeting`, Go —
`bidder-go/internal/targeting/iab.go` + `rtb/handler.go::requestCats`. Общие
тест-векторы лежат в одном файле `bidder-go/internal/targeting/testdata/match_cases.json`
(Go: `rtb/match_vectors_test.go`, Python: `tests/test_ctx_match.py`): меняя правило,
меняй оба слоя и этот файл в одном PR.

* **Категории запроса `C`** — объединение (верхний регистр, без пробелов, без
  пустых и дубликатов) полей `cat`, `sectioncat`, `pagecat`, `content.cat`,
  `publisher.cat` объектов `site` и `app`. Подчёркивание в кодах IAB терпимо:
  `IAB7_3` → `IAB7-3`. Нечитаемые типы (число, объект) в новых полях игнорируются и не
  ломают разбор запроса. Коды других таксономий (числовые) остаются как есть и не совпадут с `IABn`.
* **Совпадение кода таргетинга `T` с кодом запроса `R`:** `R == T`; либо `T` — tier-1 код
  без дефиса (`IAB7`), а `R` — его подкатегория (`IAB7-3`). Подкатегория в таргетинге
  (`IAB7-3`) родителя запроса (`IAB7`) **не** покрывает.
* `iab_categories` проходит, если есть хотя бы одна пара (T, R); `iab_block` отсекает,
  если пара есть для любого кода блок-листа. `bcat` запроса использует то же правило
  (`rtb/handler.go::iabBlocked` → `targeting.IABIntersects`).

### 1.2 Диагностика отказов (метрики биддера)

`no_bid_total{reason="no_candidates"}` не говорит, какой фильтр отсёк кандидатов. Две
дополнительные серии (метки только из фиксированных наборов; счётчики копятся
локально в `bidOnImp` и сбрасываются одним сложением на imp):

* `bidder_candidate_reject_total{stage}` — `type`, `partner_type`, `ssp`, `targeting`,
  `geo_v2`, `imp_targeting`, `floor`, `budget_gate`, `audience`, `fx`, `adm`. Кандидат
  считается один раз, на первой стадии, где отброшен; `partner_type` считает imp (все
  кандидаты отброшены сразу). Сумма стадий не равна `no_bid_total`: серия считает
  кандидатов, а не запросы.
* `bidder_targeting_reject_total{reason}` — какая группа проверок `targeting.Match`
  отсекла кандидата: `geo`, `device`, `inventory_iab`, `inventory_domain`,
  `inventory_other`, `audience`, `time`, `keywords`, `context`, `regulatory`, `other`.
  Сумма по `reason` равна `stage="targeting"`. В `other` попадают `badv`/`bcat` запроса и
  блоки оптимизатора; глобальные списки доменов, clean traffic и scoring-блоки — в
  `inventory_domain`.

### 1.3 Новые ключи контекста (Фаза 2)

Все три ключа необязательны. Сквозная цепочка каждого: Pydantic (`targeting_contract.py`,
недопустимое значение → 422) → JSONB → `/rtb-feed/campaigns` (отдаёт `targeting` как есть) →
`feed.Targeting` → `feedTargetingToMatch` → `targeting.Targeting` → `frontend/lib/types.ts` →
редактор. У кампаний без этих ключей фид и поведение прежние.

| Ключ | Значения | Если нет ключа |
|---|---|---|
| `iab_allow_unknown` | `true` / `false` | `false` |
| `keywords_match` | `"substring"` / `"word_start"` / `"whole_word"` | легаси-слова (старый алгоритм без изменений; это не то же самое, что `substring` v2) |
| `context_mode` | `"all"` / `"any"` | `"all"` |

**`iab_allow_unknown`.** Если `iab_categories` заданы, множество категорий запроса пусто и опция
включена, проверка тематики считается пройденной. Непустое множество — обычное пересечение.
`iab_block` при пустом множестве ничего не блокирует. Только при `context_mode = "all"`: в режиме
`any` опция игнорируется (значение в данных не стирается).

**Слова v2** (`keywords_match` задан). Правила побитово одинаковы в Go
(`bidder-go/internal/targeting/keywords.go`) и Python (`backend/ctx_match.py`); общие векторы —
`bidder-go/internal/targeting/testdata/keyword_cases.json`, их читают оба набора тестов.

* Нормализация: нижний регистр (по символам, как `unicode.ToLower`), `ё` → `е`; адрес страницы
  перед этим декодируется (`url.PathUnescape` / эквивалент в Python): `+` остаётся плюсом,
  битая `%`-последовательность — исходная строка. NFC не выполняется.
* Слово текста — буквы (`L*`), десятичные цифры (`Nd`) и комбинирующие знаки (`M*`); всё прочее
  разделитель. Регулярки с `\b`/`\w` не используются (в Go `\b` только ASCII).
* Источники текста: `site|app.keywords`, `content.keywords`, адрес страницы, `content.title`.
  Представления: `N` (нормализованные источники через `\n`) и `T` (токены; между источниками и
  элементами CSV — токен `\x00`, который не совпадает ни с одним ключом).
  Лимиты: источник — 2048 символов (обрезается до нормализации), токенов — 512.
* Подготовка ключей при загрузке фида: нормализация → токены; слова без токенов, длиннее 100
  символов или длиннее 8 токенов отбрасываются; список режется до 200. Если разрешающий список
  после очистки пуст — ограничения по словам нет (в легаси такой список отсекал бы всё).
* Сопоставление разрешающих слов: `substring` — `nk` (токены ключа через пробел) в `N` или в `T`;
  `word_start` — токены ключа являются префиксами подряд идущих токенов текста; `whole_word` —
  равны им. **Стоп-слова всегда широко** (как `substring`) независимо от режима.
  Пустой текст: разрешающие слова не совпали, стоп-слова не срабатывают.
* Текст запроса строится лениво один раз на запрос и только если у какого-либо кандидата задан
  `keywords_match`. Неизвестное значение `keywords_match` в данных (в обход валидации) Go
  трактует как легаси.
* Backend проверяет лимиты (200 слов, 100 символов, 8 токенов) на сохранении, только когда задан
  `keywords_match`: легаси-списки не затрагиваются.

### 1.4 Режим «И / ИЛИ» (`context_mode`)

`hasIAB` — заданы `iab_categories`; `hasKW` — заданы разрешающие слова (после очистки в v2).

| `context_mode` | `hasIAB` | `hasKW` | Разрешающая часть |
|---|---|---|---|
| `all` / нет | да | да | `iabOK И kwOK` |
| `all` / нет | да / нет | нет / да | то, что задано |
| `any` | да | да | `iabOK ИЛИ kwOK` |
| `any` | да / нет | нет / да | то, что задано |
| любой | нет | нет | проходит |

Стоп-условия (`iab_block`, `keywords_block`) проверяются всегда и первыми, в обоих режимах.
Порядок отказов в режиме `all` не менялся. Отказ в режиме `any`, когда не прошли оба условия, —
`reason="context"`; если задана только одна из частей, отказ идёт по её обычной причине
(`inventory_iab` / `keywords`). Слова в режиме `any` вычисляются, только если тематика не прошла.
Реализация: `targeting.MatchReason` (Go) и `_match_targeting` (Python); общая матрица на 378
кейсов — `testdata/context_cases.json` (генератор `testdata/gen_context_cases.py` считает
ожидания независимым оракулом по этой таблице).

### 1.5 Редактор (блок «Инвентарь»)

* «Показывать, если площадка не передала тематику» (`iab_allow_unknown`) виден при выбранных
  IAB-категориях и режиме не `any`.
* «Как искать слова»: «По началу слова (рекомендуется)» → `word_start`, «Целое слово» →
  `whole_word`, «Часть слова (шире)» → `substring`. У существующей кампании без ключа показан
  пункт «Как раньше (часть слова)», ключ не пишется, пока значение не выбрано явно. В мастере
  новой кампании (`isNew`) первое слово без ключа записывает `keywords_match: "word_start"`.
* «Совпадать должно» (`context_mode`) виден, когда заданы и IAB, и разрешающие слова.
* «Сбросить» в секции сбрасывает и новые ключи. Сводки — чистые функции
  `frontend/lib/targeting-context.ts` (`inventorySummary`: «IAB: N (+без тематики)»,
  «слова: N (режим)», «или» между ними при `any`).

## 2. Redis: кто пишет, кто читает, TTL

| Ключ | Тип | Пишет | Читает | TTL | Назначение |
|---|---|---|---|---|---|
| `spend:camp:{campaign_id}:{YYYY-MM-DD}` | string (money) | Go Lua-гейт, Python `cache.py` | оба, `clear_stale_daily_spend` | до 00:00 Europe/Moscow + 1 ч grace | дневной расход кампании за бизнес-день (дата МСК в ключе) — **единственный источник бюджета на торгах** |
| `spend:li:{li_id}:{YYYY-MM-DD}` | string (money) | Go Lua-гейт, Python | оба | до 00:00 Europe/Moscow + 1 ч grace | дневной расход группы за бизнес-день |
| `bidmeta:{bid_id}` | JSON | Go (`persistBidMeta`), Python-биддер | Python `win/imp/click` (`_find_bid`), Go `loss` | 1 ч | биллинг выигрыша; `is_test` блокирует списание |
| `bidfreq:{bid_id}` | string `device|caps` | Go, Python-биддер | Python `/imp` | 1 ч | инкремент frequency-cap после показа |
| `lossdone:{bid_id}` | flag (SETNX) | Go `loss` | Go | 1 ч | идемпотентность возврата бюджета по loss |
| `pending:burl` | ZSET bid_id → deadline | Python `cache.win_pending` (nurl партнёра `billing_event=burl`) | Python `/cron/sweep-burl` (`expire_burl`), `luaSettleWin` (ZREM, KEYS[13]) | score = now + `BURL_TTL` | R-07: выигрыши, ждущие burl; резерв в `pending:bids` при этом сдвинут на `BURL_TTL + 300 с`, чтобы sweeper Go не снял его раньше |
| `bidstate:{bid_id}` | string `won\|won_expired\|win\|loss\|expired` | Python `win_pending`/`expire_burl`, `luaSettleWin` (оба) | `luaSettleWin` | как бронь | R-07: состояние бида между nurl и burl; `won`+резерв → convert (1), `won` без резерва → recharge (2), `won_expired` → recharge late (3) |
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
| `wins` | `routers/rtb.py` `/win` | win notice; деньги — только при `supply_partners.billing_event = 'win'` |
| `billable_imps`, `spend` | `routers/rtb.py` `/win` (режим `win`) или `/burl` (режим `burl`, R-07) | ровно одно из событий бида несёт `clear_price` (`bid_events.event_type` `win` либо `billable`); `stats_math.SPEND_FROM_EVENTS_SQL`/`BILLABLE_FROM_EVENTS_SQL` — зеркало |
| `impressions`, `clicks` … | `routers/rtb.py` (`/imp`, `/click`) | биллинг через `billing_core.bill_win` (CPC — на клике) |
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
