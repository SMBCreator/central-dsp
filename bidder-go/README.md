# bidder-go — Central DSP RTB bidder (Go)

Статус: **скелет** (итерация audit/prod-readiness). Основной трек биддера по
решению владельца продукта: Go заменяет и Java-биддер (`bidder/`, deprecated),
и Python RTB hot path (`backend/routers/rtb.py`, остаётся reference + dev).

- Функциональная спека: `docs/audit/go_bidder_plan.md`
- Матрица паритета: `docs/audit/parity_matrix.md`
- Python `backend/routers/rtb.py` — источник истины по поведению.

## Эндпоинты

| Метод | Путь                    | Назначение                          |
|-------|-------------------------|-------------------------------------|
| POST  | `/rtb/bid/{endpointKey}`| OpenRTB 2.5 bid request             |
| POST  | `/rtb/win`              | win notice (биллинг, идемпотентный) |
| POST  | `/rtb/loss`             | loss notice (release резерва)       |
| POST  | `/rtb/imp`              | impression notice (ОРД-учёт)        |
| GET   | `/healthz`              | liveness + возраст фида кампаний    |

## Переменные окружения

| Переменная             | Назначение                                   |
|------------------------|----------------------------------------------|
| `BIDDER_ADDR`          | listen addr, default `:8090`                 |
| `BACKEND_INTERNAL_URL` | backend для фида, default `http://backend:8000` |
| `INTERNAL_FEED_TOKEN`  | токен `/internal/bidder-feed` (обязателен)   |

## Сборка и тесты

```bash
go build ./... && go test ./...
```
