# 🗺️ ПЛАН РАЗРАБОТКИ DSP — CENTRAL BUYING PLATFORM
## Статус: обновлён 18.07.2026 | Репо: SMBCreator/central-dsp

---

## 📊 АУДИТ ТЕКУЩЕГО СОСТОЯНИЯ

### ✅ Что уже есть (подтверждено по репо)

| Компонент | Файл/Директория | Статус |
|-----------|----------------|--------|
| FastAPI приложение | `backend/main.py` | ✅ Есть |
| SQLAlchemy модели | `backend/models.py` (28KB) | ✅ Есть, нужна проверка |
| Pydantic схемы | `backend/schemas.py` (14KB) | ✅ Есть |
| Auth роутер | `backend/routers/auth.py` | ✅ Есть |
| Advertisers API | `backend/routers/advertisers.py` | ✅ Есть |
| Campaigns API | `backend/routers/campaigns.py` | ✅ Есть |
| Line Items API | `backend/routers/line_items.py` | ✅ Есть |
| Creatives API | `backend/routers/creatives.py` | ✅ Есть |
| Bidder роутер | `backend/routers/bidder.py` (11KB) | ✅ Есть |
| Supply Partners | `backend/routers/supply_partners.py` | ✅ Есть |
| Targeting API | `backend/routers/targeting.py` | ✅ Есть |
| Tracking | `backend/routers/tracking.py` | ✅ Есть |
| Stats | `backend/routers/stats.py` | ✅ Есть |
| Budget tasks | `backend/tasks/budget.py` (9KB) | ✅ Есть |
| Celery | `backend/celery_app.py` | ✅ Есть |
| Central Bidder | `backend/central_bidder/` | ✅ Есть (dir) |
| Alembic миграции | `backend/alembic/` | ✅ Есть |
| Docker Compose | `docker-compose.yml` | ✅ Есть |
| Bidder (RTB4FREE) | `bidder/` | ✅ Есть (dir) |
| Dependencies/RBAC | `backend/dependencies.py` | ✅ Есть |
| Services | `backend/services/` | ✅ Есть (dir) |
| Users API | `backend/routers/users.py` | ✅ Есть |
| Frontend | `frontend/` | ✅ Есть (dir) |
| Docs | `docs/` | ✅ Есть (dir) |

### ⚠️ Что требует аудита и доработки
- `models.py` — нужно проверить полноту RTB-полей (floor price, bid floor, pacing)
- `central_bidder/` — содержимое неизвестно, нужно ревью логики отбора
- `bidder/` — конфигурация RTB4FREE, статус интеграции
- `bidder.py` — нужна проверка OpenRTB 2.5 совместимости
- Нет тестов (unit + integration)
- Нет мониторинга и алертинга

---

## 🔴 ФАЗА 0 — АУДИТ И СТАБИЛИЗАЦИЯ ОСНОВЫ
### Цель: понять что реально работает, зафиксировать baseline
**Ориентир: 3–4 рабочих дня**

### Шаг 0.1 — Прочитать и задокументировать models.py
- [ ] Открыть `backend/models.py`, выписать все таблицы и поля
- [ ] Проверить наличие: `Advertiser`, `Campaign`, `LineItem`, `Creative`, `SupplyPartner`
- [ ] Проверить RTB-поля в LineItem: `max_bid`, `floor_price`, `daily_budget`, `total_budget`, `pacing`
- [ ] Проверить targeting поля: `geo`, `device_type`, `os`, `browser`, `app_bundle`, `domain`
- [ ] Проверить `BidRequest` / `BidResponse` Pydantic-модели в `schemas.py`
- [ ] Зафиксировать список недостающих полей → issue в GitHub

### Шаг 0.2 — Аудит central_bidder/
- [ ] Прочитать все файлы в `backend/central_bidder/`
- [ ] Понять: есть ли логика отбора кандидатов? (candidate selection)
- [ ] Есть ли frequency capping через Redis?
- [ ] Есть ли pacing (равномерная открутка)?
- [ ] Есть ли логика auction (first price / second price)?
- [ ] Составить список пробелов → файл `docs/bidder_gaps.md`

### Шаг 0.3 — Аудит bidder/ (RTB4FREE конфигурация)
- [ ] Прочитать все файлы в `bidder/`
- [ ] Понять: это конфиг RTB4FREE Java-биддера или Python-реализация?
- [ ] Проверить docker-compose.yml — как bidder сервис связан с backend
- [ ] Если RTB4FREE — проверить campaign JSON конфиг формат

### Шаг 0.4 — Аудит bidder.py роутера
- [ ] Проверить endpoint `/bid` — принимает ли корректный OpenRTB 2.5 BidRequest?
- [ ] Проверить структуру BidResponse (корректный `seatbid[].bid[]`)
- [ ] Проверить `/win` endpoint для win notice
- [ ] Проверить обработку No-Bid (204 или пустой ответ)
- [ ] Замерить latency: curl тест, цель < 100ms

### Шаг 0.5 — Docker Compose проверка
- [ ] `docker compose up` — поднимается ли всё без ошибок?
- [ ] Alembic миграции применяются чисто?
- [ ] Celery worker стартует?
- [ ] Redis доступен?
- [ ] Создать тестового суперадмина через `POST /auth/register`

### Шаг 0.6 — Зафиксировать документацию по аудиту
- [ ] `docs/api_status.md` — какие эндпоинты работают, какие нет
- [ ] `docs/bidder_gaps.md` — пробелы в биддере
- [ ] `docs/architecture.md` — схема компонентов системы

---

## 🟡 ФАЗА 1 — МОДЕЛИ И МИГРАЦИИ (если нужны правки)
### Цель: убедиться что схема данных полная для RTB
**Ориентир: 2–3 рабочих дня (пропустить если 0.1 показал что всё ок)**

### Шаг 1.1 — Дополнить модели недостающими RTB полями
- [ ] `LineItem` добавить если нет:
  - `pacing_type` (ENUM: asap, even, ahead)
  - `bid_price_type` (ENUM: fixed, dynamic)
  - `frequency_cap_impressions` / `frequency_cap_period`
  - `start_date` / `end_date`
  - `status` (draft, active, paused, completed, archived)
- [ ] `SupplyPartner` добавить если нет:
  - `endpoint_url`, `timeout_ms`, `qps_limit`, `protocol_version`
  - `active` флаг, `test_mode` флаг
- [ ] `Creative` проверить: `width`, `height`, `mime_type`, `ad_markup`, `click_url`, `tracking_pixels`
- [ ] `BidLog` таблица для хранения результатов аукциона (win/loss/nobid)

### Шаг 1.2 — Alembic миграции
- [ ] `alembic revision --autogenerate -m "rtb_fields_complete"`
- [ ] Проверить сгенерированный файл миграции
- [ ] `alembic upgrade head`
- [ ] Убедиться что данные не потеряны

### Шаг 1.3 — Обновить Pydantic schemas
- [ ] Обновить `LineItemCreate` / `LineItemUpdate` / `LineItemRead`
- [ ] Обновить `SupplyPartnerCreate` и `SupplyPartnerRead`
- [ ] Добавить `BidLogRead` схему для API статистики
- [ ] Проверить все `Optional` поля — не должно быть обязательных полей без дефолтов

---

## 🟡 ФАЗА 2 — БИДДЕР: CORE LOGIC
### Цель: рабочий RTB цикл от BidRequest до Win Notice
**Ориентир: 5–7 рабочих дней | КРИТИЧЕСКИЙ БЛОК**

### Шаг 2.1 — OpenRTB 2.5 парсинг BidRequest
- [ ] Проверить/создать в `backend/schemas.py` OpenRTB разделы:
  - `BidRequest` с полями: `id`, `imp[]`, `site/app`, `device`, `user`, `at` (auction type)
  - `Imp` с полями: `id`, `banner/video/native`, `bidfloor`, `bidfloorcur`
  - `Banner` с `w`, `h`, `format[]`, `pos`
  - `Device` с `ua`, `ip`, `geo`, `devicetype`, `os`
  - `Site/App` с `id`, `domain/bundle`, `cat[]`, `publisher`
- [ ] Тест: отправить реальный OpenRTB JSON, получить корректный ответ

### Шаг 2.2 — Candidate Selection (отбор кандидатов)
- [ ] Логика в `backend/central_bidder/` — функция `select_candidates(bid_request) -> List[LineItem]`
- [ ] Фильтрация по статусу: только `active` line items
- [ ] Фильтрация по бюджету: `spent < daily_budget` AND `spent < total_budget`
- [ ] Фильтрация по расписанию: `start_date <= now <= end_date`
- [ ] Фильтрация по таргетингу:
  - geo (страна, регион, город)
  - device_type (desktop/mobile/tablet)
  - os (iOS/Android/Windows/etc)
  - domain/app_bundle whitelist/blacklist
  - IAB категории (site.cat vs line_item.categories)
- [ ] Фильтрация по размеру: `banner.w x banner.h` совпадает с creative

### Шаг 2.3 — Bid Price Calculation
- [ ] Функция `calculate_bid(line_item, bid_request) -> float`
- [ ] Проверка: `bid_price > bid_request.imp[0].bidfloor`
- [ ] Проверка: `bid_price <= line_item.max_bid`
- [ ] Для fixed: возвращаем `line_item.max_bid`
- [ ] Для dynamic: CPM на основе исторического CTR (заглушка на старте)
- [ ] Логирование: записать попытку ставки в Redis (для дебага)

### Шаг 2.4 — Auction Logic
- [ ] Auction type из `bid_request.at`: 1=first price, 2=second price
- [ ] Функция `run_auction(candidates: List[Bid]) -> Winner | None`
- [ ] First price: winner платит свою ставку
- [ ] Second price: winner платит max(second_bid, floor) + $0.01
- [ ] При 0 кандидатов → No-Bid (HTTP 204)

### Шаг 2.5 — BidResponse формирование
- [ ] Структура по спецификации OpenRTB 2.5:
  ```json
  {
    "id": "{bid_request.id}",
    "seatbid": [{
      "bid": [{
        "id": "{uuid}",
        "impid": "{imp.id}",
        "price": {bid_price},
        "adm": "{ad_markup}",
        "adid": "{creative.id}",
        "nurl": "https://dsp.domain/win/{bid_id}",
        "lurl": "https://dsp.domain/loss/{bid_id}",
        "w": {width},
        "h": {height}
      }],
      "seat": "central_dsp"
    }],
    "cur": "USD"
  }
  ```
- [ ] `nurl` (win notice URL) генерируется динамически
- [ ] `adm` берётся из `creative.ad_markup` с подстановкой макросов

### Шаг 2.6 — Макросы в ad markup
- [ ] Замена стандартных макросов в `adm` перед отправкой:
  - `${AUCTION_ID}` → bid_request.id
  - `${AUCTION_PRICE}` → encrypted winning price (для nurl)
  - `${AUCTION_IMP_ID}` → imp.id
  - `${AUCTION_SEAT_ID}` → "central_dsp"
- [ ] Функция `render_markup(creative, context) -> str`

### Шаг 2.7 — Win Notice endpoint
- [ ] `GET/POST /win/{bid_id}?price={clearing_price}`
- [ ] Запись в `BidLog`: статус=WIN, clearing_price
- [ ] Увеличение `spent` у LineItem и Campaign (атомарно через Redis INCR)
- [ ] Запись impression в stats таблицу

### Шаг 2.8 — Loss Notice endpoint
- [ ] `GET /loss/{bid_id}?reason={loss_reason}`
- [ ] Запись в `BidLog`: статус=LOSS, loss_reason

### Шаг 2.9 — Latency тест биддера
- [ ] Benchmark: 1000 запросов, p50/p95/p99
- [ ] Цель: p99 < 80ms (SSP обычно даёт 100-150ms timeout)
- [ ] Если медленно: добавить Redis кэш для active line items
- [ ] Redis кэш: ключ `line_items:active`, TTL 30 сек, invalidate при изменении

---

## 🟡 ФАЗА 3 — BUDGET CONTROL И FREQUENCY CAPPING
### Цель: финансовая безопасность системы
**Ориентир: 3–4 рабочих дня**

### Шаг 3.1 — Аудит budget.py
- [ ] Прочитать `backend/tasks/budget.py` (9KB) — что уже реализовано
- [ ] Проверить: есть ли daily budget reset в полночь?
- [ ] Проверить: есть ли pacing (равномерная открутка)?
- [ ] Дополнить если нужно

### Шаг 3.2 — Real-time Budget Guard в Redis
- [ ] Ключ: `budget:daily:{line_item_id}:{date}` → потраченная сумма
- [ ] Ключ: `budget:total:{line_item_id}` → общий потраченный бюджет
- [ ] Перед каждой ставкой: проверить `budget_spent < budget_limit`
- [ ] После win notice: `INCRBYFLOAT budget:daily:{id}:{date} {price}`
- [ ] Celery task: синхронизировать Redis → PostgreSQL каждые 60 сек

### Шаг 3.3 — Pacing алгоритм
- [ ] Even pacing: `allowed_per_hour = daily_budget / hours_remaining`
- [ ] Ключ: `pacing:{line_item_id}:{hour}` → потрачено в этот час
- [ ] Если `spent_this_hour >= allowed_per_hour` → throttle (не делаем ставку)
- [ ] ASAP pacing: тратим как можно быстрее (без throttle)

### Шаг 3.4 — Frequency Capping
- [ ] Ключ: `fc:{user_id}:{line_item_id}` → счётчик показов
- [ ] TTL ключа = period (например, 86400 сек для дневного капа)
- [ ] Перед ставкой: `GET fc:{user_id}:{line_item_id}` < `cap_impressions`
- [ ] После win: `INCR fc:{user_id}:{line_item_id}`
- [ ] Если `user_id` нет в bid request → skip frequency check

### Шаг 3.5 — Campaign status automation
- [ ] Celery task каждые 5 мин: проверить бюджет всех active campaigns
- [ ] Если `spent >= total_budget` → автоматически `status = completed`
- [ ] Если `end_date < now` → `status = completed`
- [ ] Если `start_date > now` → не включать в биддинг

---

## 🟡 ФАЗА 4 — SSP ИНТЕГРАЦИИ
### Цель: первые реальные bid requests
**Ориентир: 4–5 рабочих дней**

### Шаг 4.1 — SupplyPartner management
- [ ] Проверить `backend/routers/supply_partners.py` — CRUD полный?
- [ ] Добавить поле `qps_limit` (запросов в секунду)
- [ ] Добавить `timeout_ms` (таймаут биддера для этого SSP)
- [ ] Добавить `test_mode` флаг (принимаем запросы, не делаем реальные ставки)
- [ ] Добавить `auth_type` (none, bearer, api_key, ip_whitelist)
- [ ] API: `GET /supply-partners/{id}/test` — тест соединения

### Шаг 4.2 — Per-SSP endpoint routing
- [ ] Каждый SSP получает уникальный endpoint:
  - `POST /bid/{supply_partner_slug}` или
  - `POST /bid` с идентификацией по API key в header
- [ ] Логирование: какой SSP прислал request
- [ ] Rate limiting по QPS: Redis sliding window counter

### Шаг 4.3 — SSP аутентификация
- [ ] Поддержка: IP whitelist (проверка `X-Forwarded-For`)
- [ ] Поддержка: Bearer token в `Authorization` header
- [ ] Поддержка: custom header (`X-OpenRTB-Version`, `X-Supply-Key`)
- [ ] Middleware для валидации SSP credentials

### Шаг 4.4 — Тест с первым SSP (тестовый трафик)
- [ ] Подключить тестовый SSP или использовать RTB4FREE как SSP-симулятор
- [ ] Запустить тест на 1000 bid requests
- [ ] Проверить win rate, bid rate, latency
- [ ] Проверить что бюджет корректно декрементируется
- [ ] Проверить что impression засчитывается

### Шаг 4.5 — QPS мониторинг и защита
- [ ] Redis counter: `qps:{supply_partner_id}:{second}` → count, TTL=2
- [ ] Если `count > qps_limit` → 429 Too Many Requests
- [ ] Prometheus метрика: `bid_requests_total{ssp="xxx"}`

---

## 🟡 ФАЗА 5 — TRACKING И СТАТИСТИКА
### Цель: видеть что происходит с рекламой
**Ориентир: 3–4 рабочих дня**

### Шаг 5.1 — Аудит tracking.py
- [ ] Прочитать `backend/routers/tracking.py` (8KB)
- [ ] Проверить: click tracking redirect работает?
- [ ] Проверить: impression pixel (1x1 GIF) работает?
- [ ] Проверить: записываются ли события в БД?

### Шаг 5.2 — Impression tracking
- [ ] `GET /t/imp/{creative_id}/{bid_id}` → 1x1 GIF ответ
- [ ] Async запись в PostgreSQL (не блокировать response)
- [ ] Поля: `timestamp`, `creative_id`, `line_item_id`, `campaign_id`, `supply_partner_id`, `user_id`, `ip`, `ua`

### Шаг 5.3 — Click tracking
- [ ] `GET /t/click/{creative_id}/{bid_id}` → 302 redirect на `click_url`
- [ ] Async запись: click event
- [ ] Дедупликация: не считать повторный клик с того же IP за 1 час

### Шаг 5.4 — Stats API
- [ ] Проверить `backend/routers/stats.py` (9KB) — что уже есть
- [ ] Добавить если нет:
  - `GET /stats/campaigns/{id}?from=&to=` → impressions, clicks, spend, CTR, CPM
  - `GET /stats/line_items/{id}?from=&to=`
  - `GET /stats/dashboard` → агрегаты за сегодня
- [ ] Группировка по: день, час, supply_partner, geo, device

### Шаг 5.5 — ClickHouse (опционально для scale)
- [ ] Если объём > 1M events/день → ClickHouse для аналитики
- [ ] Dual write: PostgreSQL для operational, ClickHouse для analytics
- [ ] На старте: PostgreSQL достаточно

---

## 🟡 ФАЗА 6 — ADMIN API И RBAC
### Цель: многопользовательская система с ролями
**Ориентир: 3–4 рабочих дня**

### Шаг 6.1 — Аудит auth.py и dependencies.py
- [ ] Прочитать `backend/routers/auth.py` и `backend/dependencies.py`
- [ ] Какие роли уже есть? (superadmin, admin, advertiser, viewer?)
- [ ] JWT claims содержат роль?
- [ ] Есть ли permission middleware?

### Шаг 6.2 — Роли и разрешения
- [ ] Определить роли:
  - `superadmin` — полный доступ, управление SSP, пользователями
  - `admin` — управление advertisers, campaigns, reporting
  - `advertiser` — только свои campaigns/creatives/stats
  - `viewer` — read-only доступ к статистике
- [ ] Реализовать декораторы: `@require_role("admin")`, `@require_permission("campaigns:write")`
- [ ] Multi-tenancy: advertiser видит только свои данные

### Шаг 6.3 — Advertiser management
- [ ] `POST /advertisers` — создание (только admin/superadmin)
- [ ] Привязка users к advertiser_id
- [ ] Все запросы фильтруются по `advertiser_id` из JWT

### Шаг 6.4 — Финансы (баланс и пополнение)
- [ ] Таблица `AdvertiserBalance` или поле в `Advertiser`
- [ ] `POST /finance/topup` — пополнение баланса (admin only)
- [ ] Проверка при старте campaign: `balance >= daily_budget`
- [ ] Списание при win notice → уменьшение баланса advertiser

---

## 🟡 ФАЗА 7 — ФРОНТЕНД
### Цель: рабочий UI для управления DSP
**Ориентир: 7–10 рабочих дней | Параллельно с Фазами 4–6**

### Шаг 7.1 — Анализ существующего frontend/
- [ ] Изучить структуру `frontend/` директории
- [ ] Составить матрицу: экран → API endpoint → статус реализации

### Шаг 7.2 — Auth pages
- [ ] Login (`POST /auth/login`) — получение JWT
- [ ] Logout — очистка токена
- [ ] Refresh token логика

### Шаг 7.3 — Dashboard
- [ ] KPI карточки: Impressions, Clicks, Spend, CTR сегодня
- [ ] График по часам
- [ ] Топ-5 campaigns по spend
- [ ] Алерты: campaigns без бюджета, close to limit

### Шаг 7.4 — Campaigns management
- [ ] Список кампаний с фильтрами и пагинацией
- [ ] Создание кампании (wizard: настройки → line items → креативы)
- [ ] Детальная страница кампании со статистикой
- [ ] Пауза/активация/архивирование

### Шаг 7.5 — Line Items management
- [ ] Список line items в контексте кампании
- [ ] Форма создания: бюджет, даты, таргетинг, ставка, pacing
- [ ] Targeting builder: geo picker, device checkboxes, OS multiselect
- [ ] Domain/App whitelist/blacklist

### Шаг 7.6 — Creatives management
- [ ] Upload баннера
- [ ] Preview баннера
- [ ] Привязка creative к line item
- [ ] Статус модерации (pending/approved/rejected)

### Шаг 7.7 — SSP Management (admin)
- [ ] Список supply partners
- [ ] Форма добавления SSP: endpoint URL, auth, QPS limit
- [ ] Тест соединения кнопкой

### Шаг 7.8 — Stats & Reporting
- [ ] Таблица с drill-down (campaign → line item → creative)
- [ ] Date range picker
- [ ] Export CSV
- [ ] Charts: impression/click/spend timeline

---

## 🔵 ФАЗА 8 — QUALITY, ТЕСТЫ И БЕЗОПАСНОСТЬ
### Цель: production-ready код
**Ориентир: 4–5 рабочих дней**

### Шаг 8.1 — Unit тесты биддера
- [ ] pytest для `backend/central_bidder/` функций
- [ ] Тест candidate_selection: различные таргетинги
- [ ] Тест bid_calculation: edge cases (ниже floor, выше max_bid)
- [ ] Тест auction: first price, second price, no candidates
- [ ] Тест frequency_cap: счётчик работает, TTL соблюдается

### Шаг 8.2 — Integration тесты API
- [ ] `TestClient` FastAPI для каждого роутера
- [ ] Тест полного RTB цикла: BidRequest → BidResponse → WinNotice → Stats
- [ ] Тест budget exhaustion: кампания останавливается при нулевом бюджете

### Шаг 8.3 — Load тест
- [ ] Locust или k6: 100 rps на `/bid` endpoint
- [ ] Цель: p99 < 100ms при 100 rps
- [ ] Профилирование узких мест

### Шаг 8.4 — Безопасность
- [ ] SQL injection: использовать только параметризованные запросы (SQLAlchemy)
- [ ] Rate limiting на auth endpoints (5 попыток/мин)
- [ ] CORS настройка: только разрешённые домены
- [ ] Secrets: всё в `.env`, ничего в коде
- [ ] JWT secret rotation стратегия

---

## 🔵 ФАЗА 9 — ДЕПЛОЙ И МОНИТОРИНГ
### Цель: работающая система в production
**Ориентир: 3–4 рабочих дня**

### Шаг 9.1 — Production docker-compose
- [ ] `docker-compose.prod.yml` с:
  - `backend` (uvicorn workers = CPU * 2 + 1)
  - `celery_worker` (2 replicas)
  - `celery_beat` (1 replica)
  - `postgres` с persistent volume
  - `redis` с persistent volume
  - `nginx` reverse proxy

### Шаг 9.2 — Nginx конфигурация
- [ ] SSL termination
- [ ] `/bid/*` → backend с keepalive
- [ ] `/api/*` → backend
- [ ] `/` → frontend (SPA)
- [ ] Gzip compression

### Шаг 9.3 — Мониторинг
- [ ] Prometheus метрики из FastAPI (`prometheus-fastapi-instrumentator`)
- [ ] Ключевые метрики:
  - `bid_requests_total` by SSP
  - `bid_responses_total` by type (bid/nobid)
  - `win_notices_total`
  - `bid_latency_seconds` histogram
  - `budget_utilization` gauge
- [ ] Grafana dashboard

### Шаг 9.4 — Алерты
- [ ] Alertmanager: latency p99 > 100ms
- [ ] Alertmanager: win rate drop > 50%
- [ ] Alertmanager: error rate > 1%
- [ ] Alertmanager: Redis memory > 80%

### Шаг 9.5 — Логирование
- [ ] Structured JSON logs (loguru или structlog)
- [ ] Каждый bid request логируется: request_id, ssp, duration, result
- [ ] Loki + Grafana для log aggregation (опционально)

---

## 📅 СУММАРНЫЙ ТАЙМЛАЙН

| Фаза | Задача | Дни | Приоритет |
|------|--------|-----|-----------|
| 0 | Аудит и стабилизация | 3-4 | 🔴 ПЕРВЫЙ |
| 1 | Модели и миграции | 2-3 | 🔴 Если нужно |
| 2 | Биддер core logic | 5-7 | 🔴 КРИТИЧНО |
| 3 | Budget + FreqCap | 3-4 | 🔴 КРИТИЧНО |
| 4 | SSP интеграции | 4-5 | 🟡 Важно |
| 5 | Tracking + Stats | 3-4 | 🟡 Важно |
| 6 | Admin API + RBAC | 3-4 | 🟡 Важно |
| 7 | Frontend | 7-10 | 🟡 Параллельно с 4-6 |
| 8 | Тесты + Security | 4-5 | 🔵 Перед деплоем |
| 9 | Деплой + Мониторинг | 3-4 | 🔵 Финал |
| **ИТОГО** | **MVP** | **37-50 р.дн.** | |

---

## 🎯 ОПРЕДЕЛЕНИЕ MVP (минимальная рабочая DSP)

MVP считается достигнутым когда:

1. ✅ Система принимает OpenRTB 2.5 bid requests от минимум 1 SSP
2. ✅ Биддер отбирает кандидатов по таргетингу и бюджету
3. ✅ Отправляет корректный BidResponse с ad markup
4. ✅ Обрабатывает Win Notice и засчитывает impression
5. ✅ Бюджет корректно контролируется (кампания останавливается при исчерпании)
6. ✅ Frequency capping работает
7. ✅ Статистика доступна через API
8. ✅ Есть минимальный UI для создания campaign/line item/creative
9. ✅ Система выдерживает 50 rps на bid endpoint
10. ✅ Есть мониторинг и алерты

---

*Документ обновляется по мере выполнения шагов. Каждый выполненный шаг — коммит в репо с ссылкой на задачу.*
