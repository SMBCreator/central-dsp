# 🗺️ Central DSP — Мастер-план до уровня GetIntent

**Репо:** [SMBCreator/central-dsp](https://github.com/SMBCreator/central-dsp)

> Это живой навигатор. Каждый раз когда начинаем новый шаг — читаем репо и знаем точно где мы.
> Цель: конкурент GetIntent / DV360 / myTarget уровня.

---

## 📊 Текущий прогресс

| Этап | Название | Шагов | % готовности |
|---|---|---|---|
| **1** | Backend жив | 7 | 15% |
| **2** | Frontend | 10 | 0% |
| **3** | RTB интеграция | 6 | 5% |
| **4** | Умные ставки | 4 | 20% |
| **5** | Продакшен | 4 | 0% |
| **6** | GetIntent уровень | 6 | 0% |
| **ИТОГО** | | **37** | **~32%** |

---

## 🗂️ Карта состояния репо

```
central-dsp/
├── backend/
│   ├── main.py          ✅ 32KB — роутеры есть, логика частично
│   ├── models.py        ✅ 18KB — все 15 таблиц
│   ├── schemas.py       ✅ 9KB  — Pydantic схемы
│   ├── auth.py          ✅ JWT готов
│   ├── bid_optimizer.py ✅ исправлен
│   ├── routers/
│   │   ├── auth.py      ✅ готов
│   │   ├── campaigns.py ⚠️  скелет — логика неполная
│   │   ├── creatives.py ⚠️  скелет
│   │   ├── pixels.py    ⚠️  скелет
│   │   ├── tracking.py  ⚠️  скелет
│   │   ├── postback.py  ⚠️  скелет
│   │   ├── stats.py     ⚠️  скелет
│   │   ├── rules.py     ⚠️  скелет
│   │   ├── targeting.py ⚠️  скелет
│   │   └── links.py     ⚠️  скелет
│   ├── rtb_bridge/      ⚠️  не подключён к main.py
│   ├── tasks/           ⚠️  Celery задачи — скелет
│   └── db/              ✅ Alembic миграция
├── frontend/
│   ├── package.json     ✅ Vue3+Vite+Tailwind
│   ├── src/App.vue      ⚠️  базовый layout
│   ├── src/views/       🔴 ПУСТО
│   └── src/components/  🔴 ПУСТО
├── bidder/              ⚠️  форк RTB4FREE — нет Dockerfile
└── docker-compose.yml   ⚠️  bidder секция — нет build
```

---

## ЭТАП 1 — Живой Backend

> **Цель:** `docker-compose up` → всё стартует → можно делать API запросы

### ☐ Шаг 1.1 — Dockerfile для Java bidder
- **Файл:** `bidder/Dockerfile`
- **Что:** Multi-stage Maven build → JRE-slim образ
- **Почему критично:** без него `docker-compose up` падает на build bidder
- **Статус:** 🔴 Не сделан

### ☐ Шаг 1.2 — Подключить rtb_bridge в main.py
- **Файл:** `backend/main.py`
- **Что:** `from rtb_bridge.router import router as rtb_router` + `app.include_router(..., prefix="/api/rtb")`
- **Почему критично:** биддер делает запрос `/api/rtb/campaigns` → сейчас 404
- **Статус:** 🔴 Не сделан

### ☐ Шаг 1.3 — Полный CRUD campaigns.py
- **Файл:** `backend/routers/campaigns.py`
- **Эндпоинты:**
  - `POST   /api/campaigns` — создать
  - `GET    /api/campaigns` — список + пагинация + фильтры
  - `GET    /api/campaigns/{id}` — детальная
  - `PATCH  /api/campaigns/{id}` — обновить
  - `DELETE /api/campaigns/{id}` — мягкое удаление
  - `POST   /api/campaigns/{id}/pause` — пауза
  - `POST   /api/campaigns/{id}/resume` — запуск
- **Статус:** 🔴 Не сделан

### ☐ Шаг 1.4 — Pixel трекинг (реальный)
- **Файл:** `backend/routers/tracking.py`
- **Эндпоинты:**
  - `GET /px/{pixel_id}` → 1×1 GIF (43 байта) + запись в `events`
  - `GET /click/{link_id}` → запись клика + редирект на `offer_url`
  - `GET /imp/{creative_id}` → impression pixel для RTB показов
- **Статус:** 🔴 Не сделан

### ☐ Шаг 1.5 — Postback S2S (реальный)
- **Файл:** `backend/routers/postback.py`
- **Что:** `GET /postback?click_id={}&payout={}&status={}` → запись конверсии в `events` + обновление `stats`
- **Статус:** 🔴 Не сделан

### ☐ Шаг 1.6 — Redis Budget Guard
- **Файл:** `backend/rtb_bridge/router.py` (win notice handler)
- **Что:** при win notice → `INCRBYFLOAT budget:spent:{campaign_id}:{date}` → если > daily_budget → `SET campaign:paused:{id} 1`
- **Почему критично:** без этого можно потратить весь бюджет за минуту в RTB
- **Статус:** 🔴 Не сделан

### ☐ Шаг 1.7 — Celery задачи (регистрация)
- **Файлы:** `backend/tasks/__init__.py` + `backend/celery_app.py`
- **Что:** явно зарегистрировать все `@celery_app.task` чтобы Beat их видел
- **Статус:** 🔴 Не сделан

---

## ЭТАП 2 — Живой Frontend

> **Цель:** открыть браузер → войти → создать кампанию → видеть статистику
> **Стек:** Vue 3 + Vite + Tailwind (уже в package.json) + Pinia + Vue Router 4

### ☐ Шаг 2.1 — Router + Store + API layer
- **Файлы:**
  - `frontend/src/router/index.js` — Vue Router 4, защищённые роуты
  - `frontend/src/stores/auth.js` — Pinia store (JWT, user)
  - `frontend/src/api/index.js` — axios с interceptors (авто-refresh токена)
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.2 — Login страница
- **Файл:** `frontend/src/views/LoginView.vue`
- **Что:** форма email/пароль → POST `/api/auth/login` → JWT → редирект на Dashboard
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.3 — Dashboard (главная)
- **Файл:** `frontend/src/views/DashboardView.vue`
- **Метрики:** Показы, Клики, Конверсии, Расход $, Win Rate %, ROI %
- **Графики:** расход по часам (Chart.js), воронка imp→click→conv
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.4 — Campaigns (список + wizard создания)
- **Файлы:**
  - `frontend/src/views/CampaignsView.vue` — таблица с фильтрами
  - `frontend/src/views/CampaignCreateView.vue` — 4-шаговый wizard
  - `frontend/src/views/CampaignDetailView.vue` — детали + статистика
- **Wizard шаги:**
  1. Основное (название, тип, бюджет, даты)
  2. Таргетинг (гео, девайс, ОС, часы, сайты)
  3. Ставки (bid floor/ceiling, стратегия)
  4. Креативы (загрузка баннеров/нативов)
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.5 — Creatives Manager
- **Файл:** `frontend/src/views/CreativesView.vue`
- **Что:** загрузка изображений, превью баннеров, статус модерации, привязка к кампаниям
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.6 — Pixel Center
- **Файл:** `frontend/src/views/PixelsView.vue`
- **Что:** создать пиксель → скопировать код (`<img>` и JS) → статистика
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.7 — Analytics (детальная)
- **Файл:** `frontend/src/views/AnalyticsView.vue`
- **Разрезы:** день/час/гео/девайс/сайт/OS/браузер
- **Метрики:** Показы, Клики, CTR, Конверсии, CR, Расход, CPM, CPC, CPA, ROI
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.8 — SmartLinks
- **Файл:** `frontend/src/views/SmartLinksView.vue`
- **Что:** создать ссылку → ротация офферов → трекинг URL
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.9 — AutoPilot Rules UI
- **Файл:** `frontend/src/views/AutoPilotView.vue`
- **Что:** UI для создания правил (IF метрика > значение THEN действие)
- **Статус:** 🔴 Не сделан

### ☐ Шаг 2.10 — RTB Bidder панель
- **Файл:** `frontend/src/views/BidderView.vue`
- **Что:** статус биддера, QPS, win rate в реальном времени (WebSocket), список SSP
- **Статус:** 🔴 Не сделан

---

## ЭТАП 3 — RTB интеграция

> **Цель:** реальный трафик от SSP → реальные ставки → реальные показы

### ☐ Шаг 3.1 — CampaignLoader в Java bidder
- **Файл:** `bidder/src/main/java/.../RestCampaignLoader.java`
- **Что:** заменить Aerospike → HTTP GET `http://backend:8000/api/rtb/campaigns`
- **Объём:** ~150 строк Java (OkHttp клиент уже в зависимостях)
- **Статус:** 🔴 Не сделан

### ☐ Шаг 3.2 — Mock SSP для тестирования
- **Файл:** `backend/tests/mock_ssp.py`
- **Что:** скрипт шлёт реальные bid requests на наш биддер, проверяет ответ
- **Статус:** 🔴 Не сделан

### ☐ Шаг 3.3 — Адаптер первого SSP
- **Файл:** `backend/adapters/beeswax.py` (или OpenX)
- **Что:** регистрация нашего endpoint → получать реальный тестовый трафик
- **Статус:** 🔴 Не сделан

### ☐ Шаг 3.4 — Win/Loss notice полный цикл
- **Файл:** `backend/rtb_bridge/router.py`
- **Что:** Win → impression + бюджет списан + stats обновлены. Loss → auction_logs
- **Статус:** 🔴 Не сделан

### ☐ Шаг 3.5 — Frequency Capping
- **Файл:** `backend/rtb_bridge/targeting.py`
- **Что:** Redis счётчик `fc:{user_id}:{campaign_id}:{date}` → не показывать > N раз/день
- **Статус:** 🔴 Не сделан

### ☐ Шаг 3.6 — Native Ad формат
- **Что:** обработка `native` объекта в bid request → шаблоны нативных объявлений
- **Статус:** 🔴 Не сделан

---

## ЭТАП 4 — Умные ставки (ML-слой)

> **Цель:** bid_optimizer работает на реальных данных

### ☐ Шаг 4.1 — Исторический CVR из auction_logs
- **Файл:** `backend/bid_optimizer.py`
- **Что:** когда данных > 100 показов → CVR по срезу (гео+девайс+час)
- **Статус:** 🟡 Заготовка есть

### ☐ Шаг 4.2 — Fallback bid при нехватке данных
- **Что:** при < 100 показах → `campaign.bid_ceiling * 0.5` как безопасный дефолт
- **Статус:** 🔴 Не сделан

### ☐ Шаг 4.3 — Bid Adjustments автозапись
- **Что:** Celery задача пишет множители в `bid_adjustments` по (гео, девайс, час, сайт) на основе ROI
- **Статус:** 🔴 Не сделан

### ☐ Шаг 4.4 — AutoPilot RTB правила
- **Файл:** `backend/tasks/autopilot.py`
- **Правила:**
  - Win Rate < 1% за 1ч → поднять bid ceiling +20%
  - CPA > target×1.5 → снизить ставку -15%
  - Сайт > 30% бюджета → добавить в blacklist
  - Budget utilization < 50% к 18:00 → burst mode
  - Исторически убыточный час → снизить ставку
- **Статус:** 🟡 Заготовка есть

---

## ЭТАП 5 — Продакшен

> **Цель:** задеплоить на VPS, SSL, мониторинг

### ☐ Шаг 5.1 — Caddy reverse proxy
- **Файл:** `Caddyfile`
- **Что:** `ads.domain.com` → backend, `app.domain.com` → frontend, авто-SSL
- **Статус:** 🔴 Не сделан

### ☐ Шаг 5.2 — Prometheus + Grafana
- **Файлы:** `monitoring/prometheus.yml` + `monitoring/grafana/dashboards/rtb.json`
- **Метрики:** QPS биддера, p99 latency, win rate, budget burn rate
- **Статус:** 🔴 Не сделан

### ☐ Шаг 5.3 — PostgreSQL Backup
- **Файл:** `scripts/backup.sh`
- **Что:** ежедневный pg_dump → S3/Backblaze B2
- **Статус:** 🔴 Не сделан

### ☐ Шаг 5.4 — Тесты (критичные)
- **Файлы:** `backend/tests/test_bid_optimizer.py`, `test_budget_guard.py`, `test_tracking.py`
- **Статус:** 🔴 Не сделан

---

## ЭТАП 6 — GetIntent уровень

> **Цель:** функциональный паритет с профессиональными DSP

### ☐ Шаг 6.1 — Мультитенантность
- **Что:** `Advertiser` модель уже есть → UI управления клиентами, изоляция бюджетов и данных
- **Статус:** 🟡 Модель есть, логика нет

### ☐ Шаг 6.2 — Deal ID / PMP (Private Marketplace)
- **Что:** поддержка `pmp` объекта в bid request → приватные аукционы с премиум SSP
- **Статус:** 🔴 Не сделан

### ☐ Шаг 6.3 — Video (VAST)
- **Что:** `video` объект в bid request + генерация VAST XML ответа → In-stream видео
- **Статус:** 🔴 Не сделан

### ☐ Шаг 6.4 — Аудиторные сегменты / DMP
- **Что:** загрузка сегментов (CSV) → таргетинг по аудиториям, look-alike моделирование
- **Статус:** 🔴 Не сделан

### ☐ Шаг 6.5 — White-label
- **Что:** кастомный домен + логотип для каждого клиента, изолированные дашборды
- **Статус:** 🔴 Не сделан

### ☐ Шаг 6.6 — API для внешних интеграций
- **Что:** публичный REST API с API ключами для интеграции клиентов
- **Статус:** 🔴 Не сделан

---

## 📋 Правило работы с планом

Каждый раз когда говоришь **"делай шаг X.X"**:
1. Читаю актуальные файлы из репо
2. Пишу код точно под то что уже есть
3. Пушу в ветку `feature/step-X.X`
4. Обновляю статус шага в этом файле

---

## 🚀 С чего начать прямо сейчас

Рекомендуемый порядок первых шагов:
```
1.1 → Dockerfile bidder     (разблокирует docker-compose up)
1.2 → rtb_bridge в main.py  (разблокирует RTB цикл)
1.4 → Pixel трекинг         (разблокирует attribution)
2.1 → Router+Store+API      (фундамент всего frontend)
2.2 → Login страница        (первая видимая страница)
```
