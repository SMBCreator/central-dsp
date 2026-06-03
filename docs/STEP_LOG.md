# Central DSP — Step Log

> Лог разработки: что сделано, что в очереди, кто делал.

---

## ✅ Сделано

### 2026-06-03
- ✅ Создан приватный репозиторий `SMBCreator/central-dsp`
- ✅ Структура монорепо: `backend/`, `bidder/`, `frontend/`, `docs/`
- ✅ `docker-compose.yml` с полным стеком (postgres, redis, backend, worker, beat, flower, frontend, bidder)
- ✅ `.env.example` с переменными окружения
- ✅ `.gitignore` настроен
- ✅ `backend/main.py` — FastAPI v3.0.0 с полным API
  - JWT авторизация (access + refresh tokens)
  - CRUD: Users, Campaigns, Links, Pixels, Rules
  - Tracking: SmartLink, A/B ротатор, S2S Postback, JS Pixel
  - AutoPilot: правила + логи
  - Analytics: summary, by_source
  - RTB Bridge router подключён
- ✅ `docs/ROADMAP.md` — полный план разработки
- ✅ `docs/ARCHITECTURE.md` — архитектура системы
- ✅ `docs/STEP_LOG.md` — этот файл

---

## 🔄 В работе сейчас

### Фаза 1 — RTB Bridge

**Следующий шаг:** создать модуль `backend/rtb_bridge/`

Файлы к созданию:
```
backend/rtb_bridge/
├── __init__.py
├── router.py      ← FastAPI endpoints: /api/rtb/*
├── schemas.py     ← OpenRTB 2.5 Pydantic модели
├── service.py     ← бизнес-логика (выбор кампаний, расчёт ставки)
└── models.py      ← SQLAlchemy: Creative, TargetingRule, AuctionLog, BidAdjustment
```

**Команда разработчику:**
> "Делаем Фазу 1.1" → создаю `backend/rtb_bridge/` полностью

---

## ⏳ Очередь

| № | Что | Ветка | Заблокировано |
|---|-----|-------|---------------|
| 1.1 | `backend/rtb_bridge/` — полный модуль | `feature/rtb-bridge` | — |
| 1.2 | `bidder/Dockerfile` + конфигурация | `feature/bidder-docker` | — |
| 2.1 | Alembic миграция RTB таблиц | `feature/rtb-models` | 1.1 |
| 3.1 | Vue компоненты RTB вкладок | `feature/rtb-ui` | 2.1 |
| 4.1 | Attribution chain auction_id | `feature/attribution` | 1.1 |
| 5.1 | Подключение Beeswax SSP | `feature/ssp-beeswax` | 1.2 |
| 6.1 | BidOptimizer + AutoPilot RTB | `feature/bidding-algo` | 5.1 |

---

## Правила работы с логом

1. Каждый push → добавить запись в "Сделано" с датой
2. Начало работы над задачей → перенести в "В работе"
3. Завершение → переместить в "Сделано" с галочкой ✅
4. Новые идеи/задачи → добавлять в "Очередь"

---

## Статистика

| Метрика | Значение |
|---------|----------|
| Файлов в репо | ~25 |
| Строк кода backend | ~700+ |
| API endpoints | 30+ |
| Фаз в плане | 8 |
| Завершено фаз | 0.5 (Фаза 0 в процессе) |
| Дата начала | 2026-06-03 |
