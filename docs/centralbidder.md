# CentralBidder — RTB Engine Documentation

CentralBidder is the real-time bidding engine of Central DSP.
It is based on the RTB4FREE open-source Java bidder, rebranded and integrated
with the CentralDSP backend.

## Component Names

| Component | Description |
|---|---|
| **CentralBidder** | Java OpenRTB 2.x bidding engine (port 8080) |
| **CentralBidder Bridge** | FastAPI router: `/api/rtb/*` — connects bidder with CentralDSP |
| **CentralBidOptimizer** | Python pricing engine: CPA-target driven bid calculation |
| **CentralCampaignFeed** | JSON API that CentralBidder polls for campaigns + creatives |
| **CentralAutoAdjust** | Celery task: recalculates bids every 5 min based on win rate |

## Bid Calculation Formula

```
bid = CPA_target × historical_CVR(context) × safety_margin

where:
  CPA_target     = campaign.offer_value (set by user)
  historical_CVR = conversions / impressions for this context (last 7 days)
  context        = {country, device, hour}
  safety_margin  = dynamic 0.50–0.95 based on win rate (last 24h)

result is clamped to [bid_floor, bid_ceiling] from targeting_rules
```

### Safety Margin by Win Rate

| Win Rate (24h) | Margin | Rationale |
|---|---|---|
| < 1% | 0.95 | Too few wins — bid more aggressively |
| 1–5% | 0.80 | Normal range |
| 5–10% | 0.70 | Slightly high — trim bids |
| 10–20% | 0.65 | Overpaying, reduce |
| > 20% | 0.50 | Winning too easily — cut significantly |

## AutoPilot RTB Rules

The AutoPilot system (existing in CentralDSP v2) has new RTB-specific actions:

| Trigger | Action |
|---|---|
| `win_rate < 1` | `raise_bid` (+20% ceiling) |
| `cpa > threshold` | `lower_bid` (-15% ceiling) |
| `roi < threshold` | `autostop_campaign` |

## Campaign Feed Cache

CentralBidder polls `/api/rtb/campaigns` every ~10 seconds.
The response is cached in Redis (`central:rtb:campaign_feed`) for 30 seconds.
Cache is invalidated:
1. Automatically every 60 seconds by Celery Beat
2. Immediately when a campaign/creative/targeting is updated via API
3. On demand via `POST /api/rtb/cache/invalidate`

## Win / Loss Flow

```
SSP → POST /bidder (CentralBidder)
              ↓ evaluates targeting_rules
              ↓ fetches CentralCampaignFeed from Redis/backend
              ↓ calls CentralBidOptimizer.calculate_bid()
              ↓ responds in <100ms

[WIN]  SSP → Win Notice → POST /api/rtb/win-notice
              ↓ BackgroundTask: write auction_logs (won=True) + events (impression)
              ↓ spend accumulates for ROI/CPA calculation

[LOSS] SSP → Loss Notice → POST /api/rtb/loss-notice
              ↓ BackgroundTask: write auction_logs (won=False)
              ↓ used for win_rate calculation in CentralBidOptimizer
```

## Connecting an SSP

1. Give the SSP your bidder endpoint: `http://your-domain.com:8080/bidder`
2. The SSP will start sending OpenRTB 2.x Bid Requests
3. CentralBidder will respond with Bid Responses in <100ms
4. Configure Win/Loss Notice URLs in the SSP to point to CentralDSP backend

See `docs/rtb4free-setup.md` for initial bidder setup.
