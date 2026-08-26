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
