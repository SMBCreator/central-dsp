# Parity Matrix — Python RTB (истина) vs Java (deprecated) vs Go (целевой)

Источник истины по поведению: `backend/routers/rtb.py`, `backend/cache.py`,
`backend/shading.py`, `backend/billing_core.py`.

Обозначения: ✅ есть / 🟡 частично / ❌ нет / 📋 спека в пакете (TODO impl).

| Фича | Python | Java | Go (скелет) | Комментарий |
|------|--------|------|-------------|-------------|
| OpenRTB 2.5 parse/validate | ✅ | ✅ | ✅ | Go-модели включают `user.eids` |
| Endpoint key routing (`/rtb/bid/{key}`) | ✅ | ✅ | ✅ | |
| SSP-токен (`x-centraldsp-token`) | ✅ | ❌ | 📋 | В Go — при имплементации handler |
| Campaign feed (`/internal/bidder-feed`) | ✅ (выдаёт) | ✅ | ✅ | Тот же контракт `X-Internal-Token` |
| Domain white/blacklist (suffix, blacklist wins) | ✅ | 🟡 (без global) | ✅ + тесты | |
| Geo country (alpha-3→alpha-2) | ✅ | 🟡 | 🟡 (без нормализации) | TODO в Go: `_normalize_country` |
| OS / device type targeting | ✅ | ✅ | 📋 | |
| SSP allowlist per campaign | ✅ | ❌ | 📋 | |
| Audience include/exclude (`audz:` ZSET, per-member expiry) | ✅ | ❌ | ✅ + тесты | R-06: ZSCORE > now в обоих; goal-сегменты наполняются из `_match_goals`; cron `/cron/audience-sync` + `scripts/redis_rebuild_audiences.py` |
| Budget reserve (атомарный Lua, копейки int) | ✅ | 🟡 (CAS-цикл) | 📋 | Go: перенос Lua 1:1, НЕ CAS |
| Freq caps multi-window (до 3 окон, один Lua) | ✅ | 🟡 (одно окно) | ✅ + тесты | R-06: гейт = confirmed (`freq:*`) + active reservations (`freqinf` ZSET: in-flight биды и `w:{bid}` выигранные, env `WIN_IMP_TTL`, дефолт 120 с, одно значение у backend и Go) < cap; win → бронь, `/rtb/imp` → `luaConfirmImp` (0/1/3, 3 = засчитано без брони → `rtb_imp_late_after_reservation_total`); `supply_partners.freq_cap_on_win` для SSP без пикселя; imp после истечения `bidmeta` (1 ч) кап не бампает (`rtb_imp_freq_uncounted_total`, известное ограничение); Lua байт-в-байт (`test_redis_contracts.py`), `redisx/freqcap_test.go` ↔ `test_freq_caps.py` |
| Bid shading (first-price-only, floor*1.01) | ✅ (GAP-005 fixed) | ❌ | ✅ гейт + 📋 фактор | Фактор обучается в Python cron |
| Deal/PMP (`imp.pmp.deals`) | ✅ | ❌ | ✅ | R-04: deal floor/at/private_auction — см. строку OpenRTB-совместимости ниже |
| Валюта RUB + Decimal/копейки | ✅ | ❌ (double USD) | 📋 (int64 копейки) | Java-double — одна из причин деприкации |
| erid (ОРД-маркировка в `bid.ext`) | ✅ | ❌ | ✅ модель + 📋 | B13a: версии erid и привязка статистики к версии — только backend (`ord_spec.md §2`), биддер читает `creatives.erid` текущей версии, Go не менялся |
| Win notice: идемпотентный биллинг | ✅ | 🟡 | 📋 | Redis SETNX + billing_core |
| Billing notice `burl` (R-07) | ✅ (`/api/rtb/burl`, `cache.win_pending/expire_burl`, `/cron/sweep-burl`) | ❌ | ✅ `BURL` в bid + `luaSettleWin` (KEYS[13] `pending:burl`, состояния `won`/`won_expired`) байт в байт с Python | `supply_partners.billing_event` ∈ {win, burl} (миграция 046; старые — win, новые — burl); `stats_hourly.billable_imps`, `bid_events.late`; env `BURL_TTL` (600 с) + grace 300 с к дедлайну в `pending:bids`; `ecpm = spend / billable_imps`. Тесты: Go `redisx/burl_test.go` (4) ↔ Python `test_burl_billing.py` (Lua + state machine), `test_burl_handlers.py`, `test_burl_billing_db.py`; алерты `WinWithoutBurlRateHigh`/`PendingBurlQueueStuck`, runbook `billing-burl` |
| Loss notice: release резерва | ✅ | ❌ | 📋 | |
| Impression / click трекинг | ✅ | 🟡 | 📋 | |
| Prometheus-метрики | 🟡 | ✅ | 📋 | Go: с первого дня имплементации |
| GeoIP (GeoLite2 mmdb) | ❌ (берёт из req) | ✅ | 📋 | GAP-017 |

## Вывод

Java-биддер отстаёт от Python по деньгам (double вместо копеек, CAS вместо Lua),
по таргетингу (нет audiences/SSP-allowlist) и по ОРД (нет erid) — доводить его
до паритета дороже, чем реализовать Go по спеке. Решение владельца продукта:
Go — основной трек (`go_bidder_plan.md`), Java заморожен, Python выключается
в проде флагом.

## Обновление: Фаза 2 Go-биддера (2026-08-26)

Go-биддер (`bidder-go/`) доведён до функционального паритета с Python по hot path и проверен end-to-end на живом стеке (Postgres 16 + Redis + backend):

| Возможность | Python | Go | Проверка |
|---|---|---|---|
| Banner bid (targeting, размер, floor) | да | да | contract-тест 5/5 MATCH |
| Multi-window frequency caps (Lua) | да | да | e2e: NOBID на 3-м запросе при капе 2/1ч; R-06: NOBID на 3-м параллельном биде без единого показа (in-flight), win без imp освобождает слот через WIN_IMP_TTL |
| Bid shading gate (first-price only) | да | да | фид отдаёт `bid_shading`, порт shading.go |
| HMAC nurl/imp/click токены | да | да | win=200, imp=200 через Python-обработчики |
| Денежный контур (bill_win, ledger) | да | да | transactions -0.0012, balance/spent_total обновлены |
| Freq device_id = ifa \|\| ip | да | да | порт 1:1 |
| Фид: frequency_caps + bid_shading | да | да | `/rtb-feed/campaigns` дополнен (коммит 469a7b6) |

Контрактное сравнение (одинаковые BidRequest в оба биддера): banner_ok, wrong_size, high_floor, video_request, no_imp — 5/5 MATCH (цены и коды идентичны).

Тесты: Python 110 passed / 7 skipped; `go vet` чисто, `go test ./...` — rtb, shading, targeting ok.

## Audit R-03 (2026-09-07): настройки кабинета ↔ Go — исполняемая матрица

Истина — `backend/routers/rtb.py` (`process_bid_request`). Каждая строка
подтверждается Go-тестом «одна и та же заявка: настройка выключена → бид,
включена → no-bid / другая цена». Файл: `bidder-go/internal/rtb/parity_r03_test.go`.
Контракт фида (что backend обязан отдать) — `backend/tests/test_bidder_feed_r03_db.py`.

| Настройка кабинета | Источник в БД | Как едет в Go | Go-тест | Статус |
|---|---|---|---|---|
| Оптимизатор: блок domain / supply_partner / device_type / geo_country / os / browser / ad_type / creative / line_item / hour | `optimizer_blocks` | feed `optimizer_blocks[]` → `Snapshot.OptBlocks` | `TestParity_OptimizerBlocksEveryDimension`, `…IsPerCampaign` | ✅ |
| Clean traffic (тумблер кампании) | `campaigns.clean_traffic_only` + `domain_quality.score < 0.5` | feed `clean_traffic_only`, `dirty_domains[]` (только если есть opt-in кампания) | `TestParity_CleanTrafficOnly` | ✅ |
| Scoring-профиль (enforce) | `scoring_blocklist` | feed `scoring_blocks[]` → `Snapshot.ScoringBlocks` | `TestParity_ScoringBlocklist` | ✅ |
| Глобальные white/blacklist аккаунта | `domain_lists` | feed `domain_lists[]` | targeting `parity_test.go` | ✅ (ранее) |
| Предфильтр: bot UA / geo_unserved / no_device_id / datacenter_ip | env `THROTTLE_*`, Redis SET `prefilter:dc_subnets` (`backend/prefilter.py`) | порт `bidder-go/internal/prefilter` (тот же порядок проверок, те же причины, fail-open); `bidder_prefilter_drops_total{reason}` | `prefilter/prefilter_test.go`, `TestParity_PrefilterDropsBotBeforeAuction` | ✅ |
| pCTR value-множитель (objective traffic/conversions, CPM): цена × clamp(pctr/global, 0.7, 1.3), floor/max_bid соблюдаются | `ml_models` (active pctr) + `ml_pctr_segments`; Python `ml.pctr.predict` | feed `pctr_model{version, global_ctr, segments[]}` → `internal/pctr` (тот же лог-odds predict in-process, без сети); `bid_events.pctr` через stream-поле `pctr` → `events_consumer` | `pctr/pctr_test.go` (эталон из Python), `TestParity_PCTRMultiplier` (9 кейсов) | ✅ |
| Pacing even: дневной бюджет × min(1, доля бизнес-дня МСК + 0.1) | `campaigns.pacing`; Python `business_day.even_pacing_budget` (rtb.py) | feed `pacing` → `redisx.EvenPacingBudget` подменяет `CampBudget` в Lua-резерве; та же граница дня, что у `spend:*` | `TestEvenPacingBudget_MirrorsPython`, `TestParity_EvenPacingThrottlesDailyBudget`; Python `test_business_day.py` (те же числа) | ✅ |
| Тип ставки CPC: в аукцион `min(bid_price, max_bid) × CTR × 1000` (CTR — pCTR-модель, без неё 0.002); win не списывает; первый click списывает цену клика | `line_items.bid_type`, `bid_price` = цена клика; Python `ml.pctr.expected_ctr/cpc_to_cpm`, `rtb.win_notice`/`click` | feed `bid_type` → `pctr.ExpectedCTR/CPCToCPM` в цене; bidmeta `bid_type`, `bid_cpc` → Python `click()` биллит `bill_win(cost=bid_cpc)`; `bid_events.click.clear_price`, `stats_math.SPEND_FROM_EVENTS_SQL` учитывает клики | `TestCPCToCPM_MirrorsPython`, `TestExpectedCTR_FallbackAndModel`, `TestBid_CPC*`; Python `test_cpc_bidding.py` (те же числа + DB-воронка win→click→click) | ✅ |
| Валюта партнёра и курс: внутри всё в RUB; ответ в валюте партнёра, если `req.cur` её принимает, иначе RUB, иначе no-bid; один курс ЦБ × (1 + наценка владельца) на бид: `imp.bidfloor` (`bidfloorcur`/`cur[0]`/валюта партнёра) и `bid_floor` партнёра → RUB, цена RUB → валюта ответа, `${AUCTION_PRICE}` → RUB при win; нет курса → no-bid | `supply_partners.currency`, `fx_rates`, `app_settings.fx_markup_percent`; Python `fx.choose_currency/floor_currency/effective_rate`, `rtb._to_rub_price` | feed `partners[].currency`, `fx_rates{}`, `candidates[].fx_markup_percent` → `internal/fx` (тот же порт); bidmeta `cur`, `fx_rate` → Python `win()` конвертирует клиринг; метрики `rtb_no_bid_total{currency}`, `*_nobid_fx_rate_missing_total` | `TestFX_*` (11); Python `test_fx_bidding.py` (те же числа: 120 ₽, USD 90 × 1.03 = 92.7 → 1.2945; floor 1.2/1.3 USD; partner floor 1.5 USD; markup 10%) | ✅ |
| OpenRTB-совместимость (audit R-04): PMP — `private_auction=1` → только сделки из `deal_ids`, `deal.bidfloor/bidfloorcur/at` заменяют floor/тип аукциона imp; `seatbid.seat` = `seat_id` партнёра; native assets строго по `imp.native.request` (id/required/len, request строкой или объектом), обязательный asset без данных → отказ; video — MIME (расширение `video_url` или `MediaFile` из `vast_xml`) ∈ `mimes`, VAST-версия ∈ `protocols`, длительность ∈ [`minduration`,`maxduration`]; VAST `Duration` HH:MM:SS, `MediaFile type` реальный. Несовместимо → кандидат пропущен с причиной | `supply_partners.seat_id`, `line_items.targeting.deal_ids`, `creatives.video_url/vast_xml/video_duration/native_*`; Python `rtb_compat.py` (эталон) | feed `partners[].seat_id`; порт `internal/rtb/openrtb.go`; метрики `rtb_openrtb_incompat_total{reason}` ↔ `bidder_openrtb_incompat_total{reason}` (native_request_malformed / native_asset_missing / video_mime / video_protocol / video_duration / private_auction) | Контрактные фикстуры `bidder-go/testdata/openrtb/*.json` (10) гоняются обоими: `TestOpenRTB_Fixtures` + `TestOpenRTB_Seat*/PrivateAuction*/DealFloor*/NativeAdm*`; Python `test_openrtb_compat.py` (те же фикстуры + проверка, что список причин совпадает с Go) | ✅ |

## Audit R-05 (2026-09-10): adm / VAST / escaping / viewability ↔ Go

Истина — `backend/routers/rtb.py::_build_adm` + `backend/rtb_compat.py`
(`safe_url`, `adm_incompat`, `substitute_click_macros`, `js_string_escape`,
`inject_vast_trackers`); порт — `bidder-go/internal/rtb/adm.go` и `vast.go`.
Тесты: Python `tests/test_adm_safety.py` (46), Go `adm_r05_test.go`; одинаковые
фикстуры (custom InLine / Wrapper без Creatives / pod / malformed).

| Поведение | Python | Go | Проверка |
|---|---|---|---|
| Все URL креатива (asset/click/icon/video/imp_tracker/click_tracker) — только абсолютный http(s), ≤2048, без пробелов/кавычек/угловых скобок; иначе кандидат пропущен (`*_openrtb_incompat_total{reason="adm_url_invalid"}`) | `rtb_compat.safe_url/adm_incompat` в `_creative_fits_imp` | `safeURL/admIncompat` перед scoring | `TestSafeURL`, `TestAdmIncompat_Reasons` / `TestSafeURL`, `TestAdmIncompat` |
| Экранирование по контексту: HTML-атрибут (`&`→`&amp;`), JS-строка в `onclick` (`\xNN`), CDATA (URL без `]]>` гарантирован валидатором) | `_html_escape`, `js_string_escape` | `htmlEscape`, `jsStringEscape` | `TestBanner_NoOpenRedirectParamAndEscapedThirdParty` |
| Клик: `/api/rtb/click?bid=TOKEN` без `&r=` — цель редиректа только `click_url` креатива из БД; `beacon=1` → GIF без перехода; `cdsp_clid` дописывается в query до `#fragment` | `click()`, `_with_clid` | clickURL/clickBeacon в adm | `TestClickEndpoint.*` |
| HTML5-сниппет: макросы `${CLICK_URL}` `%%CLICK_URL_UNESC%%` `{CLICK_URL}` `[CLICK_URL]` → наш URL; `${CLICK_URL_ENC}` `%%CLICK_URL_ESC%%` → URL-encoded; без макроса — capture-фаза click-beacon на `#cdsp_<tok>_c` (бейдж «Реклама» снаружи) | `substitute_click_macros`, `_view_script(click_beacon)` | `substituteClickMacros`, `viewScript` | `TestBannerHTML5_*` |
| Viewability banner: обёртка `#cdsp_<tok>` + IntersectionObserver ≥50 % / 1 с → `/api/rtb/view`; native/push: `eventtrackers[{event:2, methods:[1]}]` | было | **добавлено в Go** (раньше `viewable_imps`=0 для Go-трафика) | `TestBanner_*`, `TestNative_EventtrackersViewableAndNoRedirectParam` |
| Video generated VAST: `ClickThrough` = наш редирект (клик + `cdsp_clid`), `ClickTracking` = наш beacon + сторонний | было `ClickThrough`=click_url напрямую | то же | `TestVideoGenerated_ClickThroughIsOurRedirectAndBeacon` |
| Custom `vast_xml`: well-formed XML с `<VAST>` и `InLine`/`Wrapper`, иначе `adm_vast_malformed`; вставка `Impression` (после существующих), `TrackingEvents` (6 событий; в существующий контейнер или новый в каждом `Linear`), `ClickTracking` (в `VideoClicks` или новый), `Extensions` erid (если нет `<Erid>`); Wrapper без Creatives получает `Creatives/Creative/Linear` блок; pod — каждый Ad; исходная разметка не переписывается (вставка по смещениям токенов) | `inject_vast_trackers` (expat) | `injectVastTrackers` (`encoding/xml`, `HTMLEntity`) | `TestCustomVAST_*` в обоих |
| Video `start` — только `video_starts`, не viewable (MRC: 50 % пикселей 2 с) | `video_event` | n/a (backend) | `TestVideoStartNotViewable` |
| Валидация на загрузке: `CreativeIn/CreativePatch` — URL-поля через `validate_creative_url`, `vast_xml` через `validate_vast_xml` (≤64 KiB), `html_snippet` ≤100 KB; `""` → `None` | `schemas.py` | n/a | `TestCreativeSchemas` |
| Отчётность F-02 (B14a): spend net в БД, наценка `present_spend` одной точкой, `derive` одна арифметика, totals без LIMIT + «Прочее», «Не указано», конверсии из `conversions` в raw-отчёте | ✅ (`stats_math.py`, `routers/stats.py`) | — | n/a | Только read-path backend; Go пишет `bid_events`/`stats_hourly` net и не участвует в представлении. `tests/test_stats_presentation.py` |
| Наценка на share/guest F-02 (B14b): `markup_mult_snapshot = chain_mult(создатель) × (1+extra/100)` фиксируется при создании; `extra>0` только `rbac.MARKUP_ADD_ROLES` (superadmin/admin/agency); `public_stats`/viewer читают snapshot; блокировка владельца → `share.revoke_all_access_of` (ссылки, гости, сессии гостей) | ✅ (`migrations/044`, `routers/share.py`, `rbac.spend_multiplier`) | — | n/a | Только read-path backend + миграция; Go не участвует. `tests/test_share_markup_snapshot.py`, `tests/test_share_markup_snapshot_db.py` |
| Атрибуция конверсий F-02 (B14c): `conversion_attributions` 1:1 (`attributed|out_of_window|unattributed`, last_click, все измерения бида, `external_conversion_id`=beacon `oid`, `raw_payload`); отчёт — конверсии по всем измерениям кроме `loss_reason`, строки «Вне окна»/«Без атрибуции» на уровне рекламодателя, `meta.conversions.coverage[dim]`/`confidence`; `<0.8` → rules_engine/optimizer не действуют по CPA/CVR/conversions; `conversions_unattributed_total{status}`, `conversion_attribution_coverage{dimension}` + алерт `ConversionAttributionCoverageLow` | ✅ (`migrations/045`, `conversion_attribution.py`, `routers/pixel.py`, `routers/stats.py`) | — | n/a | Атрибуция живёт в pixel/collect (Python); Go пишет только bid_events (источник измерений). `tests/test_conversion_attribution.py`, `tests/test_conversion_attribution_db.py`, `tests/test_stats_presentation.py` |
| Scope владельцев S-02/S-03 (B12): один `rbac.Scope` для CRUD/billing (`scope_for`, `Scope.where`) и отчётов (`report_scope_for`, `Scope.owner_ids_param`/`owner_in`: `NULL`=вся платформа, `[]`=никого); граница гостя `advertiser_id` во всех `/stats/*` и `/stats/report`; `/scoring/report` в кабинете владельца кампании | ✅ (`rbac.py`, `routers/*`, `routers/stats.py`, `routers/scoring.py`) | — | n/a | RBAC/scope живут только в backend API; Go-биддер данных по владельцам не отдаёт. `tests/test_scope_matrix_db.py`, `tests/test_report_scope_db.py` |

`[V]` при онбординге SSP: подтвердить, что площадка исполняет `<script>` в
banner adm (иначе viewability для banner недоступна — только native eventtrackers).
