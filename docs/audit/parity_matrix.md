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
| Audience include/exclude (Redis sets) | ✅ | ❌ | 📋 | ifa + eids fallback-цепочка |
| Budget reserve (атомарный Lua, копейки int) | ✅ | 🟡 (CAS-цикл) | 📋 | Go: перенос Lua 1:1, НЕ CAS |
| Freq caps multi-window (до 3 окон, один Lua) | ✅ | 🟡 (одно окно) | 📋 | |
| Bid shading (first-price-only, floor*1.01) | ✅ (GAP-005 fixed) | ❌ | ✅ гейт + 📋 фактор | Фактор обучается в Python cron |
| Deal/PMP (`imp.pmp.deals`) | ✅ | ❌ | 📋 | |
| Валюта RUB + Decimal/копейки | ✅ | ❌ (double USD) | 📋 (int64 копейки) | Java-double — одна из причин деприкации |
| erid (ОРД-маркировка в `bid.ext`) | ✅ | ❌ | ✅ модель + 📋 | |
| Win notice: идемпотентный биллинг | ✅ | 🟡 | 📋 | Redis SETNX + billing_core |
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
| Multi-window frequency caps (Lua) | да | да | e2e: NOBID на 3-м запросе при капе 2/1ч |
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
| pCTR value-множитель (objective traffic/conversions, CPM) | `ml_models` + `ml_pctr_segments` | — | — | ⏳ B8.2 |
| Pacing even | `campaigns.pacing` | feed `pacing` (уже есть), Go не применяет | — | ⏳ B8.3 |
| Тип ставки CPC | `line_items.bid_type` | feed `bid_type` (уже есть), Go считает как CPM | — | ⏳ B8.4 |
| Валюта партнёра | `supply_partners.currency` | — | — | ⏳ B8.5 |
