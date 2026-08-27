# Central DSP — Audit Summary (audit/prod-readiness)

Дата: 2026-08-26. Ветка: `audit/prod-readiness` (от `feat/prod-hardening`).
Основание: ТЗ «CentralDSP Audit and Production Readiness» + решения владельца
продукта (Go — основной трек биддера, рыночные фичи — частично в работу).

## Вердикт

**Продукт близок к production-ready по backend-ядру, но НЕ готов к прод-трафику
до закрытия high-приоритетных гэпов** (см. `gap_register.md`). Денежный путь
(бюджеты, биллинг, freq caps) в Python-бэкенде реализован корректно
(атомарные Lua-скрипты, копейки в int, идемпотентность win). Критичные
находки этой итерации исправлены и покрыты тестами.

## Что сделано в этой итерации

| # | Работа | Коммит |
|---|--------|--------|
| 1 | GAP-001 (blocker): `get_pool()` без `await` в bidder_feed/shading — рантайм-падение фида и обучения shading | `8379d8d` |
| 2 | GAP-002: brute-force rate limiting на `/auth/login` (Redis, per-IP+username) | `f756e64` |
| 3 | GAP-003: запрет dev-fallback секрета токенов в production | `80005b1` |
| 4 | GAP-004: убраны небезопасные дефолты из docker-compose (пароли БД/Flower, RTB-токен) | `c2fe99e` |
| 5 | GAP-005: bid shading применялся при `at=2` (second price) — теперь только явный first price | `d6f72d8` |
| 6 | Go-биддер: скелет `bidder-go/` (OpenRTB 2.5 модели с `user.eids`, хэндлеры, feed-loader, targeting с тестами) | `d051afa` |
| 7 | Java-биддер помечен deprecated; `bidder-go` в compose за профилем | `b657587` |
| 8 | `RTB_PYTHON_SERVING` — Python bid path выведен из прод-топологии флагом | `288f84e` |

## Тесты

- Backend: **110 passed, 7 skipped** (skip — интеграционные, требуют live Postgres).
- Go: `go vet` чистый, `go build` + `go test` зелёные (пакеты rtb, targeting, shading).
- Java: тестов в репозитории нет; JDK/Maven в песочнице недоступны — сборка не
  проверялась. Некритично: Java-биддер deprecated.

Полный разбор — `test_report.md`.

## Ключевые архитектурные решения (зафиксированы)

1. **Биддер = Go** (`bidder-go/`). Java заморожен как reference, Python RTB —
   reference-имплементация + dev/симулятор за флагом `RTB_PYTHON_SERVING`.
   План и rollout: `go_bidder_plan.md`.
2. **Источник истины по поведению** — Python `backend/routers/rtb.py`
   (+ `cache.py`, `shading.py`, `billing_core.py`). Матрица: `parity_matrix.md`.
3. **Рыночные фичи**: shading-гейт улучшен сейчас; UID2/ID5 (`user.eids`)
   заложены в Go-модели; CTV/Retail Media/ML-shading — `roadmap_market.md`.

## Что осталось до прода (высокоуровнево)

1. Имплементация Go-биддера до паритета (budget/freq/shading/audiences) — фаза 2 плана.
2. Contract-тесты Go vs Python на едином корпусе bid request'ов.
3. Shadow-режим на stage → canary 5% → cutover (критерии в `go_bidder_plan.md`).
4. Закрытие high-гэпов из `gap_register.md` (метрики/алерты, ОРД-ретраи, backup-план БД).
5. Нагрузочное тестирование на stage (в песочнице недоступно — команды в `test_report.md`).
