# Test Report — audit/prod-readiness (2026-08-26)

## Backend (Python)

Команда: `cd backend && .venv/bin/python -m pytest tests/ -q`

Итог: **110 passed, 7 skipped, 0 failed**.

Рост за итерацию: 97 → 110 (+13 тестов: rate-limit логина ×6, production-guard
токенов ×1, shading-гейт ×5, RTB_PYTHON_SERVING ×1).

### Покрытие по областям

| Область | Файлы тестов | Статус |
|---------|--------------|--------|
| Бюджеты (Lua reserve/release, копейки) | test_budget*.py | ✅ зелёные (fakeredis) |
| Freq caps (multi-window, legacy) | test_freq_caps.py | ✅ |
| Bid shading (факторы, гейт at=1, floor-защита) | test_shading.py | ✅ 12 тестов |
| Auth (токены, rate limit, prod-guard) | test_tokens.py | ✅ |
| RTB (parse, таргетинг, no-bid) | test_rtb*.py | ✅ |
| Биллинг/win идемпотентность | test_billing*.py | ✅ |

### 7 skipped

Интеграционные тесты, требующие live Postgres (`DATABASE_URL`); в песочнице
БД нет. Закрыть через CI-job с сервисным Postgres (GAP-014). Локально:

```bash
docker compose up -d db && cd backend && DATABASE_URL=postgresql://... pytest tests/ -q
```

## Go (bidder-go)

Команда: `cd bidder-go && go vet ./... && go build ./... && go test ./...`

Итог: **vet чистый, build OK, все тесты зелёные** (rtb: 4, targeting: 5, shading: 1;
budget/freq — спеки без имплементации, тестов пока нет).

## Java (bidder, deprecated)

- Тестов в репозитории нет (`bidder/src/test` отсутствует).
- JDK/Maven в песочнице недоступны — сборка не проверялась.
- Решение: не вкладываться, биддер deprecated (см. `parity_matrix.md`).

## Не покрыто в песочнице (выполнить на stage)

| Проверка | Команда/инструмент |
|----------|--------------------|
| Интеграционные тесты с Postgres | CI-job или `docker compose up -d db` + pytest |
| E2E: кампания → бид → win → биллинг → отчёт | `docker compose --profile bidder-go up` + simulator (`backend/simulator/`) |
| Нагрузочное: p99 latency биддера под 1-5k QPS | `k6` или `vegeta` против `/rtb/bid/{key}` на stage |
| Failover Redis/Postgres (fail-closed бюджеты) | Отключение контейнеров под нагрузкой |
| ОРД-интеграция (реальный API) | stage-ключи ОРД, проверка erid в ответах |
