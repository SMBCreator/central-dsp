# Central DSP — MVP Roadmap (Аудит + План)

> **Обновлено: 2026-07-18** | Полный аудит репо + пошаговый план до рабочего MVP.
> Ведётся как живой документ — отмечаем выполненное чекбоксами по ходу работы.

---

## 🔍 Результаты аудита репо (июль 2026)

### Структура проекта

```
central-dsp/
├── backend/
│   ├── rtb_bridge/     ← RTB-биддер (18KB router + 15KB service) ⭐ главный блок
│   ├── routers/        ← 12 API роутеров
│   ├── tasks/          ← Celery задачи (9 штук)
│   ├── adapters/       ← SSP/сети адаптеры (EvaDav, MGID, PropellerAds, Push.House)
│   ├── main.py         ← 32KB монолит ⚠️
│   ├── models.py       ← ORM модели (18KB)
│   └── schemas.py      ← Pydantic схемы (9KB)
├── bidder/             ← отдельный сервис
├── frontend/           ← Vue/Vuetify фронт
├── docs/
└── docker-compose.yml  ← postgres + redis + celery + flower
```

---

### 🔴 Критические баги (не запустится / падает)

| # | Файл | Проблема | Последствие |
|---|---|---|---|
| 1 | `tasks/bidding.py` | `AuctionLogDB`, `CampaignDB`, `TargetingRuleDB` — классов нет | `ImportError` при старте Celery |
| 2 | `routers/advertisers.py` | Роутер есть, таблицы `Advertiser` в DB нет | 500 на все запросы |
| 3 | `backend/main.py` | `from rtb_bridge.router import router` — path может не резолвиться в docker | Startup fail |

### 🟠 Функциональные баги (запускается, но работает неправильно)

| # | Файл | Проблема |
|---|---|---|
| 4 | `routers/networks.py` + `models.py` | `AdAccount.platform` vs `AdAccount.network` — mismatch полей, INSERT сломан |
| 5 | `models.py` | `targeting_rules.campaign_id: unique=True` — одно правило на кампанию максимум |
| 6 | `rtb_bridge/service.py` | Нет second-price логики. Победитель платит свой bid, не second+0.01 |
| 7 | `rtb_bridge/targeting.py` | Только geo+device. Нет: hour_of_day, domain lists, freq cap, IAB categories |
| 8 | `auth.py` | Refresh token не инвалидируется в Redis — заблокированный юзер сохраняет доступ 30 дней |

### 🟡 Технический долг (работает, но нельзя масштабировать)

| # | Проблема |
|---|---|
| 9 | `main.py` — 32KB монолит, дублирует роутеры из `routers/` |
| 10 | `tasks/rules.py` дублирует `tasks/autopilot.py` на 80% |
| 11 | Нет иерархии `Advertiser → Campaign → LineItem` — Campaign выполняет роль LineItem |
| 12 | Нет Alembic миграций — `create_all()` не для production |
| 13 | Нет `/health` endpoint |
| 14 | CORS `allow_origins=["*"]` — опасно для production |
| 15 | `jwt_secret_key = "change-me..."` — дефолтное значение, нет валидации длины |

### ✅ Что реально хорошо

- OpenRTB 2.5 schemas в `rtb_bridge/schemas.py` — полные и правильные
- Win/Loss notice endpoints — есть
- Budget guard через Redis — концепция правильная
- 4 SSP адаптера (EvaDav, MGID, PropellerAds, Push.House) — рабочая архитектура
- Celery + autopilot с метриками (win_rate, CPA, ROI) — логика верная
- Docker Compose с health checks — хорошая база
- Индексы на `auction_logs` — правильно

---

## 🗺️ MVP Definition

**MVP готов когда выполнено:**

- [ ] Создать рекламодателя → кампанию → привязать креатив
- [ ] SSP шлёт bid request → биддер отвечает корректно за < 100ms
- [ ] Победа в аукционе → spend списывается из бюджета
- [ ] Дневной бюджет исчерпан → кампания паузируется автоматически
- [ ] Frequency cap работает → юзер не видит баннер больше N раз за окно
- [ ] Статистика (impressions, clicks, spend, CTR) обновляется каждый час
- [ ] Admin создаёт рекламодателя, рекламодатель управляет своими кампаниями
- [ ] Базовый UI: login → dashboard → create campaign → upload creative → stats

---

## 📋 ФАЗА 0 — Стабилизация (2–3 дня)

> Ничего не работает корректно пока это не сделано. Делаем первым.

### 0.1 — Починить импорты в `tasks/bidding.py`
- [ ] Заменить `AuctionLogDB` → `AuctionLog`
- [ ] Заменить `CampaignDB` → `Campaign`
- [ ] Заменить `TargetingRuleDB` → `TargetingRule`
- [ ] Проверить: `celery -A celery_app inspect registered` — таск появился

### 0.2 — Удалить дубль `tasks/rules.py`
- [ ] Удалить `backend/tasks/rules.py`
- [ ] В `celery_beat_schedule.py` убрать все ссылки на `tasks.rules`
- [ ] Убедиться что `tasks.autopilot` покрывает всё

### 0.3 — Починить mismatch `AdAccount` полей
- [ ] В `models.py`: привести к единому названию (`network`/`label` или `platform`/`name` — смотреть что использует фронт)
- [ ] В `routers/networks.py`: привести к тому же названию
- [ ] Alembic migration или `ALTER TABLE`

### 0.4 — Добавить таблицу `Advertiser` в `models.py`
```python
class Advertiser(Base):
    __tablename__ = "advertisers"
    id: UUID (PK)
    name: str
    email: str
    balance: Decimal(15,4) default 0
    daily_limit: Decimal(15,4) nullable
    user_id: FK → users.id
    is_active: bool default True
    created_at: datetime
```
- [ ] Написать модель
- [ ] `ALTER TABLE campaigns ADD COLUMN advertiser_id UUID REFERENCES advertisers(id)`
- [ ] Обновить Campaign CRUD — добавить `advertiser_id` в create/update

### 0.5 — Снять `unique=True` с `targeting_rules.campaign_id`
- [ ] `models.py`: убрать `unique=True`
- [ ] Migration: `DROP INDEX uq_targeting_rules_campaign_id`

### 0.6 — Починить import path `rtb_bridge` в `main.py`
- [ ] Проверить как запускается uvicorn в Dockerfile (рабочая директория)
- [ ] Убедиться что `backend/rtb_bridge` находится на python path
- [ ] Тест: `docker-compose up backend` → нет ImportError в логах

### 0.7 — Добавить Alembic
- [ ] `pip install alembic` → добавить в `requirements.txt`
- [ ] `alembic init alembic`
- [ ] Настроить `alembic.ini` + `env.py` на наши модели
- [ ] Сгенерировать initial migration: `alembic revision --autogenerate -m "initial"`
- [ ] `alembic upgrade head` — проверить на чистой БД

**✅ Чекпоинт фазы 0:** `docker-compose up` → все сервисы healthy, нет ошибок в логах

---

## 📋 ФАЗА 1 — RTB Core (4–5 дней)

> Биддер должен делать реальные ставки правильно.

### 1.1 — Second-price аукцион в `rtb_bridge/service.py`
- [ ] `win_price = min(second_highest_bid + 0.01, floor_price)` при втором участнике
- [ ] `win_price = floor_price` если участник один
- [ ] Логировать `clearing_price` в `auction_logs`

### 1.2 — Расширить `rtb_bridge/targeting.py`
- [ ] `hour_of_day` — проверять schedule из `targeting_rules.schedule`
- [ ] `domain_whitelist` / `domain_blacklist` — поле в `targeting_rules`
- [ ] `iab_categories` — матчинг с `site.cat` / `app.cat` из bid request
- [ ] `os_version` range — `device.osv` из bid request

### 1.3 — Frequency Cap через Redis
```python
key = f"fc:{user_id}:{campaign_id}"
count = await redis.incr(key)
if count == 1:
    await redis.expire(key, cap_window_seconds)
if count > cap_impressions:
    return None  # no bid
```
- [ ] Добавить поля в `targeting_rules`: `freq_cap_impressions`, `freq_cap_window_hours`
- [ ] Реализовать проверку в `service.py` ДО формирования bid

### 1.4 — Budget Guard real-time
- [ ] Redis ключ: `f"budget:{campaign_id}:spent_today"` — INCRBYFLOAT при каждом win
- [ ] Проверка ДО bid: `if spent + bid_price > daily_budget: no bid`
- [ ] Сброс в полночь через Celery: `budget:*:spent_today → DEL`
- [ ] Total budget check: `if total_spent > total_budget: pause campaign`

### 1.5 — Win Notice корректная обработка
- [ ] `POST /rtb/win/{auction_id}` → обновить `auction_logs.won = True, clearing_price`
- [ ] INCRBYFLOAT spend counter в Redis
- [ ] Если spend > 90% дневного бюджета → trigger autopilot check

### 1.6 — Pacing (равномерная открутка)
- [ ] `ideal_spend = daily_budget * (current_hour / 24.0)`
- [ ] `actual_spend = redis.get(f"budget:{campaign_id}:spent_today")`
- [ ] Если `actual < ideal * 0.8` → увеличить bid_price на 10%
- [ ] Если `actual > ideal * 1.2` → throttle 50% запросов (случайный skip)

**✅ Чекпоинт фазы 1:** Mock SSP тест — `python tests/mock_ssp.py` → биддер отвечает, бюджет уменьшается, при freq cap перестаёт отвечать

---

## 📋 ФАЗА 2 — API чистка (3 дня)

> `main.py` монолит → нормальная структура. Делаем параллельно с Фазой 1.

### 2.1 — Вынести auth из `main.py`
- [ ] `routers/auth.py` — login, me, refresh, logout (с Redis blacklist)
- [ ] Убрать auth endpoints из `main.py`

### 2.2 — Убрать дублированные endpoints из `main.py`
- [ ] Campaigns → уже есть в `routers/campaigns.py`
- [ ] Links, Pixels, Postback, Tracking → уже есть в роутерах
- [ ] Analytics → `routers/stats.py`
- [ ] `main.py` оставить только: FastAPI init + `include_router` + startup events

### 2.3 — Redis blacklist для refresh tokens
```python
# При logout:
await redis.setex(f"blacklist:token:{jti}", refresh_ttl, "1")
# При валидации refresh:
if await redis.get(f"blacklist:token:{jti}"): raise 401
```
- [ ] Добавить `jti` (uuid) в JWT payload
- [ ] Реализовать blacklist check в `auth.py`

### 2.4 — `/health` endpoint
```python
@app.get("/health")
async def health():
    await db.execute("SELECT 1")
    await redis.ping()
    return {"status": "ok", "db": "ok", "redis": "ok", "version": "1.0.0"}
```

### 2.5 — CORS на конкретные домены
- [ ] `allow_origins` брать из `settings.allow_origins` (список из .env)
- [ ] Дефолт: `["http://localhost:3000"]`

**✅ Чекпоинт фазы 2:** `main.py` < 100 строк. `GET /health` → 200. Все endpoints работают через роутеры.

---

## 📋 ФАЗА 3 — Статистика (2–3 дня)

### 3.1 — Hourly stats aggregation (Celery, каждый час)
```python
# Агрегировать auction_logs за прошедший час → stats_hourly таблица
# Поля: campaign_id, hour, impressions, wins, bids, spend, clicks, ctr, cpm, win_rate
```
- [ ] Таблица `stats_hourly` в models.py
- [ ] Celery task `tasks/stats.py::aggregate_hourly`
- [ ] Schedule: каждый час в :05

### 3.2 — Campaign stats API
```
GET /api/v1/stats/campaign/{id}
  ?from=2026-07-01&to=2026-07-18
  &granularity=day|hour
→ [{date, impressions, wins, spend, ctr, cpm, win_rate}, ...]
```
- [ ] Real-time данные из Redis counters (текущий час)
- [ ] Исторические из `stats_hourly`
- [ ] Merge в единый ответ

### 3.3 — Creative performance
- [ ] `GET /api/v1/stats/creative/{id}` — разбивка по креативам внутри кампании

### 3.4 — SSP breakdown
- [ ] `GET /api/v1/stats/ssp` — откуда идёт трафик, win rate по SSP

**✅ Чекпоинт фазы 3:** API возвращает реальные цифры расхода и показов с разбивкой по дням

---

## 📋 ФАЗА 4 — SSP подключение (3–4 дня)

### 4.1 — Модель `SupplyPartner` в `models.py`
```python
class SupplyPartner(Base):
    name: str
    endpoint_url: str
    inbound_token: str        # они шлют к нам с этим токеном
    mode: str                 # "inbound" | "outbound"
    qps_limit: int default 100
    is_active: bool
    total_requests: int
    total_bids: int
    total_wins: int
```

### 4.2 — Inbound SSP middleware
- [ ] Проверка `X-SSP-Token` header → матчинг с `SupplyPartner.inbound_token`
- [ ] Rate limiting: `redis.incr(f"qps:{ssp_id}:{second}")` + expire 2s
- [ ] Логировать каждый запрос в `auction_logs.ssp_id`

### 4.3 — Mock SSP тест
- [ ] `tests/mock_ssp.py` — генерирует bid requests с реальными OpenRTB полями
- [ ] Тест 1000 запросов → проверить win rate, latency, budget decrease

### 4.4 — Первая реальная SSP
- [ ] Подготовить endpoint документацию (swagger /docs уже есть)
- [ ] Whitelisting нашего IP у SSP
- [ ] Мониторинг: `requests/s`, `win_rate`, `avg_latency`

**✅ Чекпоинт фазы 4:** Реальная SSP шлёт bid requests, мы отвечаем корректными bid responses, видим реальный трафик в stats

---

## 📋 ФАЗА 5 — UI (3–4 дня)

> Только после готовности API. Vue/Vuetify фронт уже есть.

### 5.1 — Login + Dashboard
- [ ] Login форма → JWT → localStorage
- [ ] Dashboard: spend today, impressions, win rate, активные кампании (карточки)
- [ ] Проверить все API calls работают с backend

### 5.2 — Campaign Create/Edit
- [ ] Форма: название, бюджет дневной/общий, даты старт/конец
- [ ] Таргетинг: geo (мультиселект), device type, OS
- [ ] Schedule: дни недели + часы
- [ ] Статус toggle: active/paused
- [ ] Привязка к рекламодателю

### 5.3 — Creative Upload
- [ ] Загрузка изображения (JPEG/PNG/GIF) → S3/MinIO
- [ ] Поля: click URL, alt text, размер
- [ ] Preview баннера
- [ ] Привязка к кампании

### 5.4 — Stats Dashboard
- [ ] Таблица кампаний: impressions, clicks, spend, CTR, CPM
- [ ] Line chart spend по дням (Chart.js или уже есть в Vue)
- [ ] Фильтр по датам

### 5.5 — Advertiser Management (для admin)
- [ ] CRUD рекламодателей
- [ ] Баланс + лимиты
- [ ] Список кампаний рекламодателя

### 5.6 — SSP Management (для superadmin)
- [ ] CRUD SupplyPartner
- [ ] Статус, QPS, win rate по SSP

**✅ Чекпоинт фазы 5:** Полный пользовательский путь через UI: login → create campaign → upload creative → вижу stats

---

## 📋 ФАЗА 6 — Production Hardening (2 дня)

- [ ] Rate limiting на всех публичных endpoints (`slowapi`)
- [ ] HTTPS через Caddy или Nginx + Let's Encrypt
- [ ] Secrets только из `.env`, проверить `.gitignore` — нет ключей в коде
- [ ] JWT secret: минимум 32 символа, валидация при старте
- [ ] Structured JSON logging (`loguru` или `structlog`)
- [ ] Sentry integration для ошибок
- [ ] Pg_dump cron backup
- [ ] Docker resource limits (memory: 512m для backend, 2g для redis)
- [ ] `CORS allow_origins` — конкретные домены, не `["*"]`

---

## 📊 Сводная таблица

| Фаза | Что делаем | Дней | Статус |
|---|---|---|---|
| **0** | Стабилизация — фикс критических багов | 2–3 | ⏳ В работе |
| **1** | RTB Core — second price, freq cap, pacing | 4–5 | 🔜 |
| **2** | API чистка — убрать монолит | 3 | 🔜 |
| **3** | Статистика + Reporting | 2–3 | 🔜 |
| **4** | SSP подключение | 3–4 | 🔜 |
| **5** | UI | 3–4 | 🔜 |
| **6** | Production hardening | 2 | 🔜 |
| **ИТОГО** | | **19–27 дней** | |

---

## 🧱 Принципы работы

1. **Фикс перед фичами** — пока Фаза 0 не сделана, новый код на сломанном фундаменте бесполезен
2. **Чекпоинт в конце каждой фазы** — тест который можно запустить и показать
3. **Redis для real-time** (бюджет, freq cap, QPS), Postgres для персистентного хранения
4. **Budget Guard перед первой реальной SSP** — иначе слив бюджета за минуты
5. **UI последним** — красивый дашборд без работающего биддера = нарисованная машина
6. **Один источник правды** — `main.py` только для init, логика только в роутерах

---

## 📝 Лог изменений

| Дата | Что сделано |
|---|---|
| 2026-07-18 | Полный аудит репо. Выявлено 15 проблем. Обновлён план. |

---

*Документ обновляется по ходу выполнения. Отмечай выполненные шаги чекбоксами.*
