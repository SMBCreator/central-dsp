# CentralBidder

OpenRTB 2.x real-time bidding engine for Central DSP.

Forked from [RTB4FREE/bidder](https://github.com/RTB4FREE/bidder) and rebranded as **CentralBidder**.

## Setup

See [docs/rtb4free-setup.md](../docs/rtb4free-setup.md) for initial setup.

After cloning:
```bash
# From the repo root:
git clone https://github.com/RTB4FREE/bidder.git bidder
cd bidder
# All config is in src/main/resources/application.properties
```

## Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/bidder` | POST | OpenRTB 2.x Bid Request entry point |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

## Configuration

All via environment variables (set in docker-compose.yml):

| Variable | Default | Description |
|---|---|---|
| `CAMPAIGN_MANAGER_URL` | `http://backend:8000/api/rtb/campaigns` | CentralDSP campaign feed |
| `REDIS_HOST` | `redis` | Redis for campaign caching |
| `BID_FLOOR` | `0.01` | Minimum bid price (USD) |
| `MAX_BID` | `5.00` | Maximum bid price (USD) |
| `RESPONSE_TIMEOUT` | `100` | Max response time in ms |
| `WIN_NOTICE_URL` | `http://backend:8000/api/rtb/win-notice` | Win callback |
| `LOSS_NOTICE_URL` | `http://backend:8000/api/rtb/loss-notice` | Loss callback |
