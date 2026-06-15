# DSP Platform — Полный план разработки до MVP
### Версия 2.0 | На основе анализа существующего фронтенда (16 файлов store)

---

## ЧТО МЫ ЗНАЕМ ИЗ ФРОНТЕНДА — ФАКТЫ

Анализ 16 store-модулей дал нам **точную карту API**, которую нам нужно построить:

| Store-модуль | API endpoint (из кода) | Что это значит для бэка |
|---|---|---|
| `auth.js` | `auth/login`, `auth/refresh` | JWT + permissions array |
| `campaign.js` | `campaign/all`, `campaign/create`, `campaign/update/{id}` | Полный CRUD кампаний |
| `creative.js` | `creative/all`, `creative/create`, `creative/moderate/{id}` | CRUD + модерация + VAST/HTML5/JS типы |
| `target.js` | `target/all`, `target/create`, `target/duplicate/{id}` | Таргетинги с дублированием |
| `integration.js` | `integration/all`, `integration/create` + `dictionary/integration/ssp-specific-processing-key/read-all` | SSP интеграции + SSP-специфичные ключи |
| `statistic.js` | `statistic/creative/{id}/{period}` | Статистика по периодам |
| `finance.js` | (биллинг/баланс) | Пополнение, транзакции |
| `notification.js` | `notification/all` + **WebSocket** `/ws-notifications` + `/topic/notifications/{login}` | SockJS/STOMP real-time |
| `rule.js` | `rule/*` | Frequency cap, pacing |
| `rate.js` | `exchange-rate/create`, `exchange-rate/update/{id}`, `dictionary/currency/read-all` | Мультивалютность |
| `dictionary.js` | `dictionary/creative/iab-categories`, `dictionary/creative/kktu`, `dictionary/currency/read-all` | Справочники |
| `filters.js` | `ui/state/{page}` | Сохранение состояния UI на сервере |
| `security.js` | (2FA, смена пароля) | Безопасность аккаунта |
| `company.js` | (компании) | Multi-tenant структура |
| `client.js` | (клиенты под компанией) | Управление клиентами |
| `dashboard.js` | (сводная статистика) | Дашборд метрики |

### Система Permissions (из creative.js + target.js)
```
CREATIVE_READ, CREATIVE_READ_ALL, CREATIVE_CREATE, CREATIVE_CREATE_FOR_CLIENT,
CREATIVE_UPDATE, CREATIVE_MODERATE, CREATIVE_REMOVE,
CREATIVE_ADD_VAST_DAAST, CREATIVE_ADD_HTML5_BANNER, CREATIVE_ADD_JS_BANNER,
CREATIVE_UPDATE_JS_BANNER_FINAL_URL, CREATIVE_PERIOD_STATISTIC

TARGET_READ, TARGET_READ_ALL, TARGET_CREATE, TARGET_CREATE_FOR_CLIENT,
TARGET_UPDATE, TARGET_REMOVE, TARGET_ADD_ADVANCED,
TARGET_ADD_ADVANCED_VERIFICATION_SERVICES

INTEGRATION_READ, INTEGRATION_READ_ALL, INTEGRATION_CREATE, INTEGRATION_UPDATE, INTEGRATION_REMOVE

EXCHANGE_RATE_READ_ALL, EXCHANGE_RATE_CREATE, EXCHANGE_RATE_UPDATE
```

### Иерархия ролей (вывод из *_FOR_CLIENT permissions)
```
SUPER_ADMIN  → видит всё, readAll везде
MANAGER      → управляет клиентами, создаёт за клиента (CREATE_FOR_CLIENT)
ADVERTISER   → управляет своими кампаниями/креативами/таргетингами
```

---

## АРХИТЕКТУРА DSP (ФИНАЛЬНАЯ)

```
┌─────────────────────────────────────────────────────────────┐
│                     FRONTEND (Vue 2 + Vuetify)              │
│                   существующий — подключаем к нашему API     │
└─────────────────────┬───────────────────────────────────────┘
                      │ REST + WebSocket (SockJS/STOMP)
┌─────────────────────▼───────────────────────────────────────┐
│              MANAGEMENT API (FastAPI / Python)               │
│  auth · campaigns · creatives · targets · integrations       │
│  finance · statistic · notifications · dictionary           │
└──────┬──────────────────────────────────┬───────────────────┘
       │                                  │
┌──────▼──────────┐              ┌────────▼────────────────────┐
│   PostgreSQL    │              │   Redis                     │
│ основные данные │              │ freq cap · budget · cache   │
└─────────────────┘              └─────────────────────────────┘
                      ▲
┌─────────────────────┴───────────────────────────────────────┐
│              BIDDER SERVICE (RTB4FREE Java)                  │
│  OpenRTB 2.5 · bid request/response · win notice            │
│  Читает campaign config из Redis/PostgreSQL                  │
└──────┬──────────────────────────────────────────────────────┘
       │ HTTP OpenRTB
┌──────▼──────────┐
│   SSP Partners  │
└─────────────────┘
```

---

## ПЛАН ПО МИКРОШАГАМ

---

## 🔴 ФАЗА 0 — ФУНДАМЕНТ (3-4 дня)
### «Без этого ничего не строится»

### Шаг 0.1 — Чистка и переименование репо
- [ ] Удалить старые модели трекера ссылок (если есть конфликт)
- [ ] Настроить `.env` с переменными: `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `JWT_ALGORITHM`
- [ ] Убедиться что `docker-compose up` поднимает Postgres + Redis без ошибок
- [ ] Проверить Alembic — `alembic revision --autogenerate` работает

### Шаг 0.2 — Базовые модели БД (Alembic миграции)

Создать в **ЭТОМ** порядке (foreign keys):

```sql
1.  companies       (id, name, status, currency, created_at)
2.  users           (id, company_id, email, password_hash, role, permissions[], status)
3.  advertisers     (id, company_id, user_id, name, balance, currency)
4.  campaigns       (id, advertiser_id, name, status, budget_total, budget_daily,
                     start_date, end_date, created_at)
5.  line_items      (id, campaign_id, name, status, bid_price, budget,
                     pacing, start_date, end_date)
6.  creatives       (id, line_item_id, advertiser_id, name, type[BANNER/VAST/HTML5/JS],
                     width, height, url, click_url, status, iab_category, kktu)
7.  targets         (id, line_item_id, name, geo[], device_type[], os[], browser[],
                     domain_list[], app_list[], ip_list[], advanced_json)
8.  integrations    (id, company_id, name, type[SSP], endpoint_url, qps_limit,
                     api_key, status, ssp_specific_key)
9.  transactions    (id, advertiser_id, type[DEPOSIT/SPEND/REFUND], amount,
                     currency, created_at, description)
10. notifications   (id, user_id, message_code, data_json, is_read, date, time)
11. exchange_rates  (id, from_currency, to_currency, rate, created_at)
12. ui_states       (id, user_id, page_key, state_json)
13. statistics      (id, creative_id, line_item_id, campaign_id, date,
                     impressions, clicks, spend, wins, bids, ctr, cpm)
```

**Критерий завершения:** `alembic upgrade head` отрабатывает без ошибок, все таблицы созданы.

### Шаг 0.3 — Pydantic-схемы
- [ ] Создать `schemas/` директорию
- [ ] Для каждой модели: `XxxCreate`, `XxxUpdate`, `XxxResponse`
- [ ] Особое внимание: `permissions: List[str]` в UserResponse — именно так читает фронт

**Критерий завершения:** `from app.schemas import *` не падает, все поля совпадают с тем что ждёт фронт.

---

## 🟠 ФАЗА 1 — MANAGEMENT API (7-9 дней)
### «Фронт подключается и работает»

### Шаг 1.1 — Auth (1 день)
- [ ] `POST /auth/login` → возвращает `{accessToken, csrfToken, login, permissions[]}`
  - ⚠️ фронт ждёт именно эти поля — из `notification.js`: `{csrfToken, accessToken, login}`
- [ ] `POST /auth/refresh` → обновление токена
- [ ] `POST /auth/logout`
- [ ] Middleware: декодирует JWT, кладёт `request.user` с `permissions`
- [ ] Dependency: `require_permission("CREATIVE_READ")` — декоратор проверки

**Тест:** curl login → получить токен → curl protected endpoint.

### Шаг 1.2 — Dictionary endpoints (0.5 дня)
- [ ] `GET /dictionary/creative/iab-categories` (paginated)
- [ ] `GET /dictionary/creative/kktu`
- [ ] `GET /dictionary/currency/read-all`
- [ ] `GET /dictionary/integration/ssp-specific-processing-key/read-all`

> Без справочников форма создания креатива/интеграции не откроется на фронте.

### Шаг 1.3 — Companies + Users CRUD (1 день)
- [ ] `GET/POST /company/all`, `/company/create`, `/company/update/{id}`
- [ ] `GET/POST /users/all`, `/users/create`, `/users/update/{id}`
- [ ] Эндпоинты с разделением по ролям (SUPER_ADMIN vs MANAGER)

### Шаг 1.4 — Campaigns CRUD (1 день)
- [ ] `GET /campaign/all` (с пагинацией, sort, filter — фронт передаёт `page`, `size`, `sort`)
- [ ] `POST /campaign/create`, `POST /campaign/create/{userId}` (создание за клиента)
- [ ] `PUT /campaign/update/{id}`
- [ ] `DELETE /campaign/remove/{id}`
- [ ] `GET /campaign/view/{id}`
- [ ] Статусы: `ACTIVE`, `PAUSED`, `COMPLETED`, `ARCHIVED`

**Тест:** создать кампанию через curl, увидеть в таблице на фронте.

### Шаг 1.5 — Creatives CRUD + Moderation (1.5 дня)
- [ ] `GET /creative/all`, `GET /creative/view/{id}`
- [ ] `POST /creative/create`, `POST /creative/create/{userId}`
- [ ] `POST /creative/multi/create`, `POST /creative/multi/create/{userId}` (multi=true)
- [ ] `PUT /creative/update/{id}`
- [ ] `PUT /creative/moderate/{id}` (params: `{status: APPROVED|REJECTED, comment}`)
- [ ] `DELETE /creative/remove/{id}`
- [ ] Типы: `BANNER`, `VAST_DAAST`, `HTML5_BANNER`, `JS_BANNER`
- [ ] Поле `finalUrl` только для JS_BANNER (permission: `CREATIVE_UPDATE_JS_BANNER_FINAL_URL`)

### Шаг 1.6 — Targets CRUD (1 день)
- [ ] `GET /target/all`, `GET /target/view/{id}`
- [ ] `POST /target/create`, `POST /target/create/{userId}`
- [ ] `PUT /target/update/{id}`
- [ ] `DELETE /target/remove/{id}`
- [ ] `POST /target/duplicate/{id}` ← фронт ждёт этот endpoint!
- [ ] Advanced targeting (за permission `TARGET_ADD_ADVANCED`)

### Шаг 1.7 — Integrations CRUD (0.5 дня)
- [ ] `GET /integration/all`, `GET /integration/view/{id}`
- [ ] `POST /integration/create`
- [ ] `PUT /integration/update/{id}`
- [ ] `DELETE /integration/delete/{id}` ← именно `delete`, не `remove`!

### Шаг 1.8 — Finance (1 день)
- [ ] `GET /finance/balance`
- [ ] `POST /finance/deposit`
- [ ] `GET /finance/transactions`
- [ ] Атомарные транзакции через PostgreSQL (BEGIN/COMMIT)
- [ ] Проверка баланса перед созданием кампании

### Шаг 1.9 — Exchange Rates (0.5 дня)
- [ ] `POST /exchange-rate/create`
- [ ] `PUT /exchange-rate/update/{id}`
- [ ] Конвертация при списании (spend в USD → в валюту клиента)

### Шаг 1.10 — UI State (0.5 дня)
- [ ] `GET /ui/state/{page}` → возвращает сохранённые фильтры
- [ ] `PUT /ui/state/{page}` → сохраняет состояние таблицы

### Шаг 1.11 — WebSocket Notifications (1 день)
- [ ] Поднять SockJS/STOMP сервер
- [ ] Endpoint: `GET /ws-notifications` (SockJS handshake)
- [ ] Topic: `/topic/notifications/{login}`
- [ ] Message codes: `USER_BALANCE_REPLENISHED`, `CAMPAIGN_STATUS_CHANGED`,
  `CREATIVE_MODERATION_RESULT`, `BUDGET_EXHAUSTED`
- [ ] Headers при подключении: `X-CSRF-TOKEN` + `Authorization`

**Критерий завершения Фазы 1:** Весь существующий фронт подключается, все таблицы грузятся, CRUD работает.

---

## 🟡 ФАЗА 2 — BIDDER (5-7 дней)
### «Деньги пошли в работу»

### Шаг 2.1 — RTB4FREE в Docker (1 день)
```yaml
# docker-compose.yml добавить:
bidder:
  image: rtb4free/bidder:latest
  ports:
    - "8080:8080"
  environment:
    - REDIS_HOST=redis
    - DB_HOST=postgres
  depends_on:
    - redis
    - postgres
```
- [ ] Проверить: `curl http://localhost:8080/health`
- [ ] Изучить формат campaign config для RTB4FREE

### Шаг 2.2 — Campaign Config Sync (1.5 дня)
- [ ] При `campaign.status = ACTIVE` → генерировать JSON-конфиг → записывать в Redis `campaign:{id}:config`
- [ ] При паузе/остановке → удалять из Redis
- [ ] Пример структуры конфига:
```json
{
  "id": "uuid",
  "price": 2.50,
  "adomain": ["example.com"],
  "attributes": {
    "geo": ["RU", "KZ"],
    "device": ["mobile"],
    "sizes": [{"w": 320, "h": 50}]
  },
  "creatives": [{"url": "...", "type": "banner"}],
  "budget": {"daily": 1000, "total": 50000}
}
```

### Шаг 2.3 — Win Notice Endpoint (1 день)
- [ ] `POST /win/{impId}` — SSP вызывает при победе в аукционе
- [ ] Логика:
  1. Найти impression по `impId` в Redis (TTL 30 мин)
  2. Списать `clearingPrice` с баланса (атомарно в транзакции)
  3. Записать в `statistics` (impressions++)
  4. Обновить Redis-счётчик бюджета
  5. Если баланс < threshold → WebSocket уведомление
- [ ] `POST /nurl/{impId}` — Notification URL

### Шаг 2.4 — Budget Guard (1 день)
- [ ] Redis-ключ `budget:daily:{campaign_id}` с TTL до полуночи
- [ ] Celery Beat каждые 5 минут: проверить все активные кампании
- [ ] `spent >= daily_budget` → статус `PAUSED_BUDGET`, удалить конфиг из Redis
- [ ] `spent >= total_budget` → статус `COMPLETED`
- [ ] В полночь: сбросить daily counter, вернуть статус `ACTIVE`

### Шаг 2.5 — Frequency Capping (1 день)
- [ ] Redis INCR: `freq:{userId}:{campaignId}` с TTL = cap_window
- [ ] Биддер проверяет до отправки ответа
- [ ] Настройка cap через `rule.js` → таблица `rules`

### Шаг 2.6 — Статистика (1 день)
- [ ] `GET /statistic/creative/{id}/{period}` (фронт уже вызывает)
- [ ] Periods: `TODAY`, `YESTERDAY`, `LAST_7_DAYS`, `LAST_30_DAYS`, `CUSTOM`
- [ ] Поля: `impressions`, `clicks`, `spend`, `ctr`, `cpm`
- [ ] `GET /dashboard/summary` — сводка по всем кампаниям

**Критерий завершения Фазы 2:** mock bid request → bid response → win notice → деньги списались → статистика обновилась.

---

## 🟢 ФАЗА 3 — SSP ИНТЕГРАЦИЯ (3-4 дня)
### «Реальный трафик»

### Шаг 3.1 — Mock SSP тест (1 день)
- [ ] Python-скрипт имитирующий SSP:
  - Генерирует OpenRTB 2.5 bid request
  - Отправляет на `http://bidder:8080/bid`
  - Вызывает `/win/{impId}` при победе
- [ ] Прогнать 1000 запросов, проверить корректность списания и статистики

### Шаг 3.2 — Реальная SSP интеграция (2-3 дня)
- [ ] Выбрать первую SSP (данные в `integration` + `ssp-specific-processing-key`)
- [ ] Настроить endpoint биддера в записи integration
- [ ] Проверить форматы (некоторые SSP отклоняются от стандарта OpenRTB)
- [ ] QPS limiter: Redis `INCR ssp:{id}:qps` с TTL 1 сек

---

## 🔵 ФАЗА 4 — ПОЛИРОВКА MVP (2-3 дня)

### Шаг 4.1 — Security
- [ ] `POST /security/change-password`, `POST /security/2fa/enable`
- [ ] Rate limiting на auth: Redis 5 попыток / 15 мин
- [ ] CSRF validation на WebSocket

### Шаг 4.2 — Monitoring
- [ ] Prometheus метрики: bid QPS, win rate, budget utilization
- [ ] `GET /health` endpoint
- [ ] Structured JSON logging

### Шаг 4.3 — Нагрузочный тест
- [ ] `locust` или `k6`: 100 bid requests/sec
- [ ] Убедиться что Management API не деградирует под нагрузкой биддера
- [ ] Проверить атомарность списания при параллельных win notice

---

## ИТОГОВЫЙ TIMELINE

| Фаза | Дней | Результат |
|---|---|---|
| Фаза 0: Фундамент | 3-4 | БД, схемы, Docker работает |
| Фаза 1: Management API | 7-9 | Фронт полностью подключён |
| Фаза 2: Bidder | 5-7 | Первый bid, деньги списываются |
| Фаза 3: SSP | 3-4 | Реальный трафик |
| Фаза 4: Полировка | 2-3 | Продакшн-готовность |
| **ИТОГО** | **20-27 рабочих дней** | **Рабочая DSP** |

---

## КРИТИЧЕСКИЕ ПРАВИЛА (НЕ НАРУШАТЬ)

1. **Никакого нового фронта до завершения Фазы 1** — фронт уже есть, он ждёт API
2. **Каждый шаг = отдельный тест** — не переходить к следующему без curl/pytest проверки
3. **Win notice атомарен** — всегда в транзакции, иначе деньги уйдут без записи
4. **Budget guard запускается каждые 5 минут** — не раз в день, не в реальном времени
5. **Permissions проверяются на бэке** — фронт их только показывает/скрывает, не защищает
6. **`campaign.status` в Redis** — биддер не ходит в Postgres на каждый bid request
7. **Статистика пишется асинхронно** — через Celery/Redis queue, не блокирует win notice

---

## СТЕК (ФИНАЛЬНЫЙ)

| Компонент | Технология |
|---|---|
| Backend API | FastAPI + SQLAlchemy 2.0 + Alembic |
| Database | PostgreSQL 15 |
| Cache / Queue broker | Redis 7 |
| Bidder | RTB4FREE (Java, Docker) |
| Async tasks | Celery + Redis |
| WebSocket | Starlette + SockJS/STOMP |
| Auth | JWT (python-jose) + bcrypt |
| Frontend | Vue 2 + Vuetify (существующий) |
| DevOps | Docker Compose |
