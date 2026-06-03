# 🏗️ Central DSP — Архитектура

## Стек

| Слой | Технология | Назначение |
|---|---|---|
| **API** | FastAPI (Python 3.11) | REST API, трекинг, RTB bridge |
| **ORM** | SQLAlchemy 2.x + Alembic | Модели и миграции |
| **БД** | PostgreSQL 15 | Основное хранилище |
| **Кэш/очередь** | Redis 7 | Celery брокер, budget guard, freq cap |
| **Задачи** | Celery + Beat | Фоновые задачи, автопилот |
| **Биддер** | Java 11 (RTB4FREE форк) | OpenRTB 2.x ставки |
| **Frontend** | Vue 3 + Vite + Tailwind | UI дашборд |
| **Proxy** | Nginx (dev) / Caddy (prod) | Reverse proxy, SSL |
| **Мониторинг** | Prometheus + Grafana | Метрики, алерты |

## Схема потока данных RTB

```
SSP/Ad Exchange
      │
      │ Bid Request (OpenRTB 2.x JSON)
      ▼
RTB4FREE Bidder (Java, :8080)
      │ GET /api/rtb/campaigns (каждые 30 сек)
      │
      ▼
RTB Bridge (FastAPI, :8000/api/rtb)
      │ читает Campaign + Creative + Targeting из PostgreSQL
      │
      ▼
Bid Response → SSP (< 100ms)
      │
      │ Win Notice (при победе в аукционе)
      ▼
RTB Bridge
      ├── INCRBYFLOAT budget:spent:{id} (Redis)
      ├── INSERT impression → events (PostgreSQL)
      └── UPDATE stats (PostgreSQL)
```

## Attribution цепочка

```
RTB Win Notice
      │
      ▼
GET /imp/{creative_id}?auction_id={}&click_id={}    ← impression pixel
      │
      ▼
GET /click/{link_id}?click_id={}&auction_id={}      ← клик
      │
      ▼
Redirect → Offer URL
      │
      ▼ (конверсия на стороне партнёра)
GET /postback?click_id={}&payout={}&status=1        ← S2S постбэк
      │
      ▼
Event записан → Stats обновлены → AutoPilot пересчитывает ROI
```

## Структура таблиц БД

```
users
  └── campaigns ──────────────────────┐
        ├── creatives                  │
        │     └── auction_logs         │
        ├── targeting_rules            │
        ├── bid_adjustments            │
        ├── links                      │
        ├── pixels                     │
        ├── events ← clicks ← postback │
        ├── rules → rule_logs          │
        └── stats ◄────────────────────┘

users
  └── ad_accounts (SSP подключения)

users
  └── settings
```

## Порты (development)

| Сервис | Порт | Описание |
|---|---|---|
| backend | 8000 | FastAPI API + RTB Bridge |
| bidder | 8080 | OpenRTB endpoint для SSP |
| frontend | 3000 | Vue dev server |
| postgres | 5432 | PostgreSQL |
| redis | 6379 | Redis |
| flower | 5555 | Celery мониторинг |
| prometheus | 9090 | Метрики биддера |
| grafana | 3001 | Дашборды |
