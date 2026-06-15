# Central DSP — Roadmap to MVP

> Трезвая оценка: что есть, чего нет, и как дойти до полностью рабочей независимой DSP.
> Срок до рабочего MVP: **17–25 рабочих дней** при полной загрузке.

---

## Реальная картина на старте

| Что есть | Что это на самом деле |
|---|---|
| Docker Compose (Postgres + Redis) | ✅ Хорошая база, оставляем |
| FastAPI скелет | ⚠️ Есть, но модели — UTM-трекер, не DSP |
| Next.js UI | ⚠️ Шелл без DSP-логики |
| RTB-биддер | ❌ Нет совсем |
| SSP-интеграции | ❌ Нет |
| Campaign/LineItem/Creative иерархия | ❌ Нет, нужно строить с нуля |

**Главное правило:** UI не пишем до готовности API. Красивый дашборд без биддера — нарисованная машина.

---

## Фаза 0 — Фундамент моделей (2–3 дня)

Без этого любой следующий код придётся переписывать.

### 0.1 — Переработать Pydantic/SQLAlchemy модели под DSP-иерархию

```
Advertiser
  └── Campaign          (бюджет, даты, статус)
        └── LineItem     (таргетинг, ставка, pacing)
              └── Creative (banner/video/native, размеры, VAST)
```

**Файлы:** `backend/models/advertiser.py`, `campaign.py`, `line_item.py`, `creative.py`

**Поля LineItem (минимум):**
- `bid_price` (CPM), `daily_budget`, `total_budget`
- `targeting`: geo, device, os, browser, domain_list, app_bundle, category_iab
- `frequency_cap`: impressions / window_hours
- `pacing`: ASAP / EVEN
- `status`: draft / active / paused / exhausted

### 0.2 — Alembic миграции

```bash
alembic revision --autogenerate -m "dsp_core_models"
alembic upgrade head
```

### 0.3 — Заполнить Redis-схему для биддера

```
campaign:{id}:budget_remaining   → DECR при каждом win
campaign:{id}:freq:{user_hash}   → INCR, TTL = freq_window
lineitem:{id}:active             → bool флаг (для быстрой проверки)
```

**Чекпоинт:** `pytest tests/models/` — все модели создаются, связи работают.

---

## Фаза 1 — CRUD API (3–4 дня)

### 1.1 — Advertiser CRUD
`POST /api/v1/advertisers` → `GET` → `PATCH` → `DELETE`

### 1.2 — Campaign CRUD
`POST /api/v1/campaigns` с вложенными line_items.
Валидация: `end_date > start_date`, `daily_budget <= total_budget`.

### 1.3 — LineItem CRUD
`POST /api/v1/line-items` — отдельный эндпоинт + вложенный в кампанию.
Валидация таргетинга через `TargetingSchema`.

### 1.4 — Creative CRUD + Upload
`POST /api/v1/creatives/upload` → S3/MinIO → сохранить URL в БД.
Поддержка: image (JPEG/PNG/GIF), HTML5 (ZIP), VAST XML (video).

### 1.5 — Auth + Roles
Роли: `superadmin`, `admin`, `advertiser`, `viewer`.
JWT middleware уже есть — добавить `role` в payload и `@require_role()` декоратор.

**Чекпоинт:** Postman Collection — все CRUD работают, auth проверен.

---

## Фаза 2 — RTB4FREE Bidder (4–5 дней)

RTB4FREE — Java-биддер, production-tested, 2–5ms per bid request. Python в hot-path не ставим.

### 2.1 — Добавить RTB4FREE в Docker Compose

```yaml
# docker-compose.yml
rtb4free-bidder:
  image: rtb4free/bidder:latest
  ports:
    - "8080:8080"   # bid endpoint
    - "8081:8081"   # admin API
  environment:
    - REDIS_HOST=redis
    - DB_HOST=postgres
  depends_on:
    - redis
    - postgres
```

### 2.2 — Синхронизация кампаний из нашей БД в RTB4FREE

Написать `sync_service.py`:
```python
# При каждом save/update Campaign/LineItem:
# POST http://rtb4free-bidder:8081/api/campaign → RTB4FREE JSON-формат
```

Маппинг наших моделей → RTB4FREE Campaign JSON (см. их документацию `/api/campaign`).

### 2.3 — Bid Endpoint

RTB4FREE принимает OpenRTB 2.5 bid requests на `POST /bid`.
Наш Python-сервис **не участвует** в hot-path. Только:
- Проксирует если нужна кастомная логика
- Обрабатывает win notice (см. Фаза 3)

### 2.4 — Тест с mock SSP

```bash
# Отправить тестовый bid request
curl -X POST http://localhost:8080/bid \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/openrtb_banner_request.json
```

Ожидаемый ответ: `200 OK` с bid response, или `204 No Content` (no bid).

**Чекпоинт:** Биддер отвечает на bid requests, выигрывает аукционы в тестах.

---

## Фаза 3 — Win Notice + Budget Guard (2–3 дня)

**Обязательно до первой реальной SSP-интеграции** — иначе открутишь весь бюджет.

### 3.1 — Win Notice Endpoint

```python
# POST /api/v1/win/{imp_id}
# SSP вызывает этот URL при победе в аукционе
async def win_notice(imp_id: str, clearing_price: float):
    impression = await get_impression(imp_id)
    await redis.decrby(f"campaign:{impression.campaign_id}:budget_remaining",
                       int(clearing_price * 1000))
    await log_win_event(impression, clearing_price)
```

### 3.2 — Budget Guard Worker

```python
# Celery task, каждые 60 секунд:
async def budget_guard():
    campaigns = await get_active_campaigns()
    for c in campaigns:
        remaining = await redis.get(f"campaign:{c.id}:budget_remaining")
        if remaining <= 0:
            await pause_campaign(c.id)
            await redis.set(f"lineitem:{c.lineitem_id}:active", 0)
            # → синхронизировать в RTB4FREE (деактивировать)
```

### 3.3 — Frequency Cap

```python
# При каждом показе:
key = f"freq:{user_id}:{lineitem_id}"
count = await redis.incr(key)
await redis.expire(key, lineitem.freq_window_hours * 3600)
if count > lineitem.freq_cap:
    # no bid
```

**Чекпоинт:** После победы бюджет уменьшается, при исчерпании кампания паузируется автоматически.

---

## Фаза 4 — SSP Интеграции (3–4 дня)

### 4.1 — Модель SupplyPartner

```python
class SupplyPartner(Base):
    name: str
    endpoint_url: str       # куда мы шлём bid requests (для outbound DSP)
    inbound_token: str      # или они шлют к нам (для inbound)
    mode: Literal["inbound", "outbound"]
    qps_limit: int          # max bid requests per second
    is_active: bool
```

### 4.2 — Inbound SSP (они шлют к нам)

SSP присылает OpenRTB bid request → наш `/bid` эндпоинт → RTB4FREE → ответ.

Добавить middleware:
```python
# Проверка токена SSP
# Логирование каждого запроса (для дебага)
# Rate limiting по QPS
```

### 4.3 — Outbound SSP (мы шлём к ним) — опционально для MVP

```python
# Celery beat каждые N ms:
# GET доступные impression opportunities → POST bid request → обработать bid response
```

**Чекпоинт:** Первая реальная тестовая SSP присылает bid requests, мы отвечаем корректным bid response.

---

## Фаза 5 — Статистика и Reporting (2–3 дня)

### 5.1 — События в ClickHouse (или Postgres для MVP)

```sql
CREATE TABLE events (
    event_type  VARCHAR(20),  -- bid, win, impression, click, conversion
    campaign_id UUID,
    lineitem_id UUID,
    creative_id UUID,
    ssp_id      UUID,
    price       DECIMAL(10,6),
    timestamp   TIMESTAMPTZ,
    geo_country CHAR(2),
    device_type VARCHAR(20)
);
```

### 5.2 — Aggregation Worker

Каждые 5 минут агрегировать события → `stats_hourly` таблица.
Метрики: impressions, wins, bids, spend, CPM, win_rate.

### 5.3 — Stats API

```
GET /api/v1/stats/campaign/{id}?from=2026-06-01&to=2026-06-15&granularity=day
GET /api/v1/stats/ssp/{id}?...
```

**Чекпоинт:** API возвращает реальные цифры расхода и показов.

---

## Фаза 6 — UI (3–4 дня)

Только после готовности API. Next.js уже есть, подключаем к API.

### Приоритет экранов:
1. **Dashboard** — spend today, impressions, win rate, активные кампании
2. **Campaigns List** → **Campaign Create/Edit** (wizard: General → Targeting → Creatives → Budget)
3. **Creatives** — upload, preview, статус модерации
4. **SSP Integrations** — добавить/удалить, статус, QPS
5. **Reports** — кастомные отчёты с фильтрами

### Роли в UI:
- `superadmin` — всё включая SSP-интеграции и пользователей
- `admin` — управление кампаниями и креативами
- `advertiser` — только свои кампании и статистика
- `viewer` — только чтение статистики

**Чекпоинт:** Можно создать кампанию через UI, она попадает в биддер, появляется статистика.

---

## Фаза 7 — Hardening перед production (2 дня)

- [ ] Rate limiting на всех публичных эндпоинтах
- [ ] HTTPS (Let's Encrypt / Caddy reverse proxy)
- [ ] Secrets в `.env` (не в коде), проверить `.gitignore`
- [ ] Health check эндпоинты (`/health`, `/ready`)
- [ ] Structured logging (JSON) + Sentry для ошибок
- [ ] Alembic миграции идемпотентны
- [ ] Backup Postgres (pg_dump cron или WAL-G)
- [ ] Docker resource limits (memory, CPU)

---

## Сводная таблица фаз

| Фаза | Что делаем | Дней | Выход |
|---|---|---|---|
| 0 | Модели DSP-иерархии + Redis-схема | 2–3 | Правильный фундамент |
| 1 | CRUD API + Auth + Roles | 3–4 | Рабочее API |
| 2 | RTB4FREE в Docker + синхронизация | 4–5 | Живой биддер |
| 3 | Win Notice + Budget Guard + FreqCap | 2–3 | Контроль расхода |
| 4 | SSP Интеграции (inbound) | 3–4 | Первые реальные биды |
| 5 | Статистика + Reporting API | 2–3 | Видим деньги |
| 6 | UI (Next.js) | 3–4 | Полный продукт |
| 7 | Hardening | 2 | Production-ready |
| **ИТОГО** | | **21–28 дней** | **MVP** |

---

## Принципы разработки

1. **Никакого UI до готовности API** — фронт пишется в последнюю очередь
2. **RTB hot-path = Java (RTB4FREE)**, Python только для управления и аналитики
3. **Budget Guard обязателен** перед первой реальной SSP — иначе слив бюджета
4. **Каждая фаза заканчивается чекпоинтом** — тестом, который можно запустить
5. **Redis для real-time** (бюджет, freq cap, активность), Postgres для персистентного хранения

---

*Обновлено: 2026-06-15*
