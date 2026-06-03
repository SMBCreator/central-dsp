# Central DSP — Architecture

## Обзор системы

```
┌─────────────────────────────────────────────────────────────┐
│                    INTERNET / SSP / Exchange                 │
└───────────────────────────┬─────────────────────────────────┘
                            │ Bid Request (OpenRTB 2.5, <50ms)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              RTB4FREE Java Bidder (:8080)                    │
│   - Принимает bid requests от SSP                           │
│   - Запрашивает кампании из RTB Bridge                      │
│   - Отвечает bid response за <50ms                          │
│   - Отправляет win/loss notices                             │
└───────────┬────────────────────────┬────────────────────────┘
            │ GET /api/rtb/campaigns  │ POST /api/rtb/win-notice
            ▼                        ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI Backend (:8000)                         │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  RTB Bridge  │  │   Tracker    │  │   AutoPilot      │  │
│  │  /api/rtb/*  │  │  /s/ /px/    │  │  Celery tasks    │  │
│  │              │  │  /postback   │  │  Rules engine    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────────┘  │
│         │                 │                  │              │
│  ┌──────▼─────────────────▼──────────────────▼───────────┐  │
│  │              PostgreSQL (:5432)                        │  │
│  │  users, campaigns, links, pixels, events,              │  │
│  │  clicks, postbacks, stats, rules, auction_logs         │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  Redis (:6379)                       │    │
│  │  Celery broker + results + bid cache                │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────┬───────────────────────────────┘
                              │ REST API
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Vue 3 Frontend (:3000)                          │
│   Dashboard | Campaigns | Links | Pixels | RTB | Analytics  │
└─────────────────────────────────────────────────────────────┘
```

---

## RTB Flow — полная цепочка

```
1. SSP отправляет Bid Request →
   RTB4FREE Bidder (:8080/bidder)

2. Bidder запрашивает активные кампании →
   GET /api/rtb/campaigns
   ← [{id, name, bid_floor, bid_ceiling, targeting, creatives}]

3. Bidder считает ставку и отвечает SSP ←
   Bid Response (< 50ms)

4. SSP выбирает победителя:
   a) Победа → Win Notice → POST /api/rtb/win-notice
      → Event(event_name='impression', win_price=X)
      → AuctionLog(won=True)
   b) Проигрыш → POST /api/rtb/loss-notice
      → AuctionLog(won=False)

5. Пользователь кликает на баннер →
   /s/{code}?auction_id={auction_id}
   → Click(auction_id=...)

6. Пользователь совершает целевое действие →
   Постбэк от партнёрки → /postback?click_id={click_id}
   → Event(event_name='conversion')
   → Attribution: impression → click → conversion ✅
```

---

## Сервисы и порты

| Сервис | Порт | Описание |
|--------|------|----------|
| `frontend` | 3000 | Vue 3 UI |
| `backend` | 8000 | FastAPI REST API |
| `bidder` | 8080 | RTB4FREE Java Bidder (OpenRTB endpoint) |
| `postgres` | 5432 | PostgreSQL |
| `redis` | 6379 | Redis |
| `flower` | 5555 | Celery мониторинг |
| `caddy` | 80/443 | Reverse proxy + SSL |
| `grafana` | 3001 | Мониторинг |

---

## Модели данных

### Существующие (v2+)
```
User          → id, email, password_hash, role, is_active
Campaign      → id, user_id, name, camp_code, status, budgets
Link          → id, campaign_id, short_code, original_url, geo_rules, device_rules
Pixel         → id, campaign_id, pixel_id, name, total_events
Event         → id, pixel_id, link_id, click_id, event_name, value, geo, device
Click         → id, link_id, click_id(uuid), sub_id, creative_id, ip, country, device, cost
Postback      → id, click_id, payout, currency, status, raw_params
Stat          → id, link_id, sub_id, creative_id, clicks, conversions, cost, revenue
Rule          → id, campaign_id, metric, condition, threshold, action
RuleLog       → id, rule_id, campaign_id, triggered_at, action_taken
AdAccount     → id, user_id, platform, account_id, credentials
Setting       → id, user_id, key, value
```

### Новые RTB (Фаза 2)
```
Advertiser    → id, user_id, name, daily_budget, total_budget, currency, status
Creative      → id, campaign_id, format, width, height, image_url, title, click_url, status
TargetingRule → id, campaign_id, bid_floor, bid_ceiling, geo_countries[], device_types[],
                site_whitelist[], site_blacklist[], hour_schedule(jsonb)
AuctionLog    → id, campaign_id, creative_id, bid_request_id, bid_price, win_price,
                clearing_price, won, site_domain, geo_country, device_type, created_at
BidAdjustment → id, campaign_id, dimension, value, multiplier, created_at
```

---

## RTB Bridge API

### Endpoints

```http
GET  /api/rtb/campaigns
# Возвращает активные кампании в формате RTB4FREE
# Response: [{id, name, bid_floor, bid_ceiling, targeting, creatives, budget_remaining}]

GET  /api/rtb/creatives/{creative_id}
# Отдаёт креатив: banner(w,h,url) / native(title,desc,img) / video(vast_url)

POST /api/rtb/win-notice
# Тело: {auction_id, imp_id, price, campaign_id, creative_id, site_domain, country, device}
# Создаёт: Event(event_name='impression'), AuctionLog(won=True)

POST /api/rtb/loss-notice  
# Тело: {auction_id, imp_id, campaign_id, loss_reason_code}
# Создаёт: AuctionLog(won=False)

GET  /api/rtb/status
# Response: {bidder_alive: bool, qps: int, win_rate: float, avg_latency_ms: int}

GET  /api/rtb/stats/{campaign_id}
# RTB статистика кампании: auctions, wins, spend, win_rate, avg_cpm
```

---

## Bidding Algorithm (MVP)

```python
def calculate_bid(campaign_id: int, context: dict) -> float:
    """
    context = {
        country: str,
        device_type: str,  # mobile|desktop|tablet
        os: str,
        hour: int,         # 0-23 UTC
        site_domain: str
    }
    """
    # Получаем целевой CPA и исторический CVR для данного контекста
    cpa_target = get_cpa_target(campaign_id)        # из campaign settings
    cvr = get_historical_cvr(campaign_id, context)  # из auction_logs + postbacks
    
    # Базовая ставка
    base_bid = cpa_target * cvr
    
    # Применяем bid adjustments по срезам
    multiplier = get_bid_multiplier(campaign_id, context)  # из bid_adjustments
    
    # Ограничения
    bid = base_bid * multiplier * 0.8  # safety margin
    bid_floor = get_bid_floor(campaign_id, context)
    bid_ceiling = get_bid_ceiling(campaign_id)
    
    return max(bid_floor, min(bid, bid_ceiling))
```

---

## Environment Variables

```env
# Database
DATABASE_URL=postgresql://dsp:dsp@postgres:5432/dsp

# Redis
REDIS_URL=redis://redis:6379/0

# Security
SECRET_KEY=your-secret-key-change-in-production
FIRST_ADMIN_EMAIL=admin@example.com
FIRST_ADMIN_PASSWORD=changeme

# RTB Bidder
BIDDER_URL=http://bidder:8080
BIDDER_ENDPOINT=/bidder  # OpenRTB endpoint path

# GeoIP
GEOIP_DB_PATH=/app/geoip/GeoLite2-City.mmdb

# Celery
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
```
