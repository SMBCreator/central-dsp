# Central DSP — Development Roadmap

> **Цель:** Построить лучший self-hosted DSP на базе RTB4FREE + FastAPI + Vue 3.  
> **Кейс:** Как AI помог создать полноценный AdTech продукт с нуля.

---

## Структура плана

| Фаза | Название | Статус |
|------|----------|--------|
| 0 | Форк RTB4FREE и базовая инфраструктура | ✅ Сделано |
| 1 | RTB Bridge — связующий слой Python↔Java | 🔄 В работе |
| 2 | Модели данных для RTB (creatives, targeting, auctions) | ⏳ Следующий |
| 3 | Vue 3 UI — RTB вкладки | ⏳ |
| 4 | Pixel Center + S2S + RTB Attribution | ⏳ |
| 5 | Подключение SSP/Exchange | ⏳ |
| 6 | Bidding Algorithms + AutoPilot RTB | ⏳ |
| 7 | Production: SSL, Monitoring, Scaling | ⏳ |

---

## Фаза 0 — Инфраструктура ✅

### 0.1 Приватное репо `central-dsp`
- [x] Создан приватный репозиторий `SMBCreator/central-dsp`
- [x] Структура: `backend/`, `bidder/`, `frontend/`, `docs/`
- [x] `docker-compose.yml` — единый файл запуска всех сервисов
- [x] `.env.example` с переменными окружения
- [x] `.gitignore` настроен

### 0.2 Backend FastAPI (v3.0.0)
- [x] FastAPI приложение с JWT авторизацией
- [x] PostgreSQL + SQLAlchemy ORM
- [x] Redis (Celery очереди)
- [x] Модели: User, Campaign, Link, Pixel, Event, Click, Postback, Stat, Rule, RuleLog, Setting, AdAccount
- [x] Трекинг: SmartLink `/s/{code}`, A/B ротатор `/c/{camp_code}`
- [x] Pixel Center: JS пиксель, 1x1 GIF, события
- [x] S2S Postback: `/postback` с click_id attribution
- [x] AutoPilot Rules: CRUD правил + Celery задачи
- [x] RTB Bridge router подключён: `from rtb_bridge.router import router as rtb_router`

### 0.3 Bidder (RTB4FREE Java)
- [x] Папка `bidder/` создана
- [ ] Dockerfile для Java 11 + Maven сборки
- [ ] Конфигурация: endpoint URL, Redis, campaign API

### 0.4 Frontend Vue 3
- [x] Папка `frontend/` создана
- [ ] Vite + Vue 3 + Pinia + Vue Router
- [ ] Страницы: Dashboard, Campaigns, Links, Pixels, Analytics, RTB

---

## Фаза 1 — RTB Bridge 🔄

> RTB Bridge — Python FastAPI модуль, который связывает Java биддер (RTB4FREE) с CentralDSP базой данных.

### 1.1 `backend/rtb_bridge/` — структура модуля
- [ ] `__init__.py`
- [ ] `router.py` — FastAPI router, подключён в main.py
- [ ] `schemas.py` — Pydantic модели OpenRTB 2.5 (BidRequest, BidResponse)
- [ ] `service.py` — бизнес-логика: выбор кампаний, расчёт ставки
- [ ] `models.py` — новые SQLAlchemy модели: Creative, TargetingRule, AuctionLog

### 1.2 Endpoints RTB Bridge
- [ ] `GET /api/rtb/campaigns` → список кампаний в формате RTB4FREE
- [ ] `GET /api/rtb/creatives/{id}` → отдаёт креативы (banner/native/video)
- [ ] `POST /api/rtb/win-notice` → Win Notice от биддера → Event('impression')
- [ ] `POST /api/rtb/loss-notice` → Loss Notice → AuctionLog
- [ ] `GET /api/rtb/status` → статус биддера (QPS, win rate, latency)

### 1.3 OpenRTB 2.5 Schemas (Pydantic)
- [ ] `BidRequest` (imp[], site, app, device, user)
- [ ] `BidResponse` (id, seatbid[], cur)
- [ ] `Impression` (id, banner, video, native, bidfloor)
- [ ] `Banner` (w, h, format[])
- [ ] `WinNotice` (auction_id, imp_id, price)

---

## Фаза 2 — Модели данных RTB ⏳

### 2.1 Новые таблицы PostgreSQL

```sql
-- Рекламодатели
advertisers (id, user_id, name, daily_budget, total_budget, currency, status)

-- Креативы
creatives (
  id, campaign_id, format,        -- banner|native|video
  width, height,
  image_url, title, description, click_url,
  status,                          -- active|paused|rejected
  audit_status,                    -- pending|approved|rejected
  created_at
)

-- Targeting правила
targeting_rules (
  id, campaign_id,
  bid_floor, bid_ceiling,
  geo_countries[],
  device_types[],                  -- mobile|desktop|tablet
  os_list[],
  browser_list[],
  site_whitelist[],
  site_blacklist[],
  hour_schedule jsonb               -- {"0":{"0":1.0,"1":0.5,...},...} (день→час→множитель)
)

-- Логи аукционов (TimeSeries)
auction_logs (
  id, campaign_id, creative_id,
  bid_request_id, bid_price, win_price, clearing_price,
  won bool,
  site_domain, app_bundle,
  geo_country, device_type,
  created_at   -- индекс по этому полю!
)

-- Bid adjustments по срезам
bid_adjustments (
  id, campaign_id,
  dimension,                       -- country|device|os|hour|site
  value,                           -- значение среза (например, "US" или "mobile")
  multiplier float,                -- 0.5 = -50%, 1.5 = +50%
  created_at
)
```

### 2.2 Миграции Alembic
- [ ] `alembic revision --autogenerate -m "add_rtb_tables"`
- [ ] Применить: `alembic upgrade head`

---

## Фаза 3 — Vue 3 UI ⏳

### 3.1 Новые вкладки
- [ ] `🎨 Креативы` — загрузка баннеров, VAST, native
- [ ] `🎯 Таргетинг` — geo, device, OS, whitelist/blacklist, расписание
- [ ] `⚡ RTB Биддер` — статус, метрики, тест bid request
- [ ] `📊 Аукционы` — логи, win rate, CPM по срезам

### 3.2 RTB Dashboard виджеты
- [ ] Win Rate (%) — реальное время
- [ ] QPS биддера — gauge
- [ ] Средняя цена победы — trend
- [ ] Top сайты по расходу — таблица
- [ ] Latency p99 — gauge (цель < 100ms)

---

## Фаза 4 — Pixel + Attribution ⏳

### 4.1 RTB Attribution Chain
```
Win Notice (RTB) → impression event (auction_id)
     ↓
Click (/s/{code}?auction_id=...)
     ↓  
Postback (/postback?click_id=...)
     ↓
Conversion event → атрибутирован к impression
```
- [ ] Добавить `auction_id` и `imp_id` в модели Event и Click
- [ ] Win Notice создаёт Event(event_name='impression', auction_id=...)
- [ ] Postback матчится по click_id → находит impression → полная цепочка

### 4.2 S2S Postback расширение
- [ ] Принимать `auction_id` в postback параметрах
- [ ] ViewThrough attribution (показ без клика)

---

## Фаза 5 — Подключение SSP/Exchange ⏳

| SSP | Протокол | Приоритет | Статус |
|-----|----------|-----------|--------|
| Beeswax (тест) | OpenRTB 2.5 | 🔴 Первый | ⏳ |
| Epom | OpenRTB 2.x | 🟠 | ⏳ |
| Mintegral | OpenRTB 2.5 | 🟠 | ⏳ |
| Smaato | OpenRTB 2.5 | 🟡 | ⏳ |

---

## Фаза 6 — Bidding Algorithms ⏳

### 6.1 Base Bidder
```python
bid = CPA_target × historical_CVR(context) × safety_margin
```
- [ ] `BidOptimizer` класс в `backend/tasks/bidding.py`
- [ ] Celery задача: пересчёт bid_adjustments каждые 5 минут

### 6.2 AutoPilot RTB правила
| Триггер | Действие |
|---------|----------|
| Win Rate < 1% за 1ч | Поднять bid ceiling +20% |
| CPA > target × 1.5 | Снизить ставку -15% |
| Сайт > 30% бюджета | Добавить в site_blacklist |
| Budget < 50% к 18:00 | Burst bidding mode |
| Час с ROI < -20% | Снизить ставку в этот час |

---

## Фаза 7 — Production ⏳

- [ ] Caddy reverse proxy + SSL (Let's Encrypt)
- [ ] Grafana дашборды: QPS, win rate, latency p99
- [ ] Prometheus метрики из биддера
- [ ] MaxMind GeoLite2 офлайн геолокация
- [ ] Горизонтальное масштабирование биддера (несколько инстансов)
- [ ] pg_cron для архивации auction_logs
- [ ] Бэкапы: pg_dump в S3 каждые 6 часов

---

## Технический стек

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| RTB Bidder | Java + RTB4FREE | 11 |
| Backend API | FastAPI + Python | 3.11 |
| ORM | SQLAlchemy | 2.x |
| Task Queue | Celery + Redis | — |
| Database | PostgreSQL | 15 |
| Cache/Broker | Redis | 7 |
| Frontend | Vue 3 + Vite | — |
| State | Pinia | — |
| Charts | Chart.js | 4 |
| Infra | Docker Compose | — |
| Proxy | Caddy | 2 |
| Monitoring | Grafana + Prometheus | — |
