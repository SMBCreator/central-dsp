# Central DSP — Architecture

## Overview

Central DSP is a self-hosted programmatic advertising platform combining:

1. **SmartLink Tracker** — short link with geo/device redirect, A/B rotation, QR codes
2. **Campaign Tracker** — full attribution: click → postback → ROI analytics
3. **AutoPilot** — rule-based automation: auto-stop, bid adjustments, Telegram alerts
4. **RTB Bidder** — real-time bidding via RTB4FREE Java engine (OpenRTB 2.x)

## Data Flow

### SmartLink Flow
```
User clicks tracking link
  → GET /s/{code}?sub_id=X&creative_id=Y
  → Record click in clicks table (generate click_id UUID)
  → Substitute macros in target URL ({click_id}, {sub_id}, {country}, ...)
  → Redirect user to target URL
  → [Optional] Fire impression pixel 1x1.gif
```

### Postback (S2S Conversion) Flow
```
User converts on offer
  → CPA network fires: GET /postback?click_id={UUID}&payout={N}
  → Find click by click_id in clicks table
  → Write to postbacks table (audit)
  → Write conversion event to events table
  → Recalculate ROI for campaign
```

### RTB Flow
```
SSP sends Bid Request (OpenRTB 2.x JSON) → POST bidder:8080/bidder
  → RTB4FREE Bidder evaluates targeting_rules
  → Fetches campaign/creative from backend GET /api/rtb/campaigns
  → Calculates bid price (BidOptimizer)
  → Responds with Bid Response in <100ms
  → [Win] SSP sends Win Notice → POST /api/rtb/win-notice
                               → Record in auction_logs + events
  → [Loss] Record in auction_logs for win rate stats
```

## Database Design Principles

- **TimeSeries tables** (`events`, `clicks`, `auction_logs`): indexed on `created_at DESC`
  - Never do full table scans — always filter by date range
  - Consider partitioning by month when > 100M rows
- **Backward compatibility**: legacy `stats` table kept for v2 API compat
- **JSONB for flexible data**: `geo_rules`, `device_rules`, `hour_schedule`, `metadata`
- **Encrypted credentials**: `ad_accounts.credentials` encrypted at application layer

## Latency Budget (RTB)

| Step | Budget |
|---|---|
| Network (SSP → bidder) | ~10ms |
| RTB4FREE bid evaluation | ~20ms |
| Campaign fetch from backend | ~5ms |
| Bid price calculation | ~5ms |
| Total (must be < 100ms) | < 40ms |

## Services

| Service | Port | Tech | Purpose |
|---|---|---|---|
| frontend | 80 | nginx + Vue 3 | Main UI |
| backend | 8000 | FastAPI | API, tracker, RTB bridge |
| bidder | 8080 | Java (RTB4FREE) | OpenRTB 2.x bidding |
| postgres | 5432 | PostgreSQL 15 | Main database |
| redis | 6379 | Redis 7 | Celery broker + cache |
| worker | — | Celery | Background tasks |
| beat | — | Celery Beat | Scheduled tasks |
| flower | 5555 | Flower | Celery monitoring |
