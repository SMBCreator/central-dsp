# Setting Up RTB4FREE Bidder

## Step 1: Add RTB4FREE as upstream remote

```bash
# Navigate to the bidder directory
cd bidder/

# The upstream (original RTB4FREE) should be configured to pull updates
git remote add upstream https://github.com/RTB4FREE/bidder.git

# Pull the original RTB4FREE bidder code
git pull upstream master
```

## Step 2: Clone RTB4FREE bidder into the /bidder folder

```bash
# From the root of the central-dsp repo
git submodule add https://github.com/RTB4FREE/bidder.git bidder

# OR — if you prefer a direct copy (recommended for heavy customization)
git clone https://github.com/RTB4FREE/bidder.git /tmp/rtb4free-bidder
cp -r /tmp/rtb4free-bidder/* ./bidder/
```

## Step 3: Configure RTB4FREE to use CentralDSP as Campaign Manager

Edit `bidder/src/main/resources/application.properties`:

```properties
# Point to CentralDSP backend RTB bridge
campaign.manager.url=http://backend:8000/api/rtb/campaigns

# Redis for caching
spring.redis.host=redis
spring.redis.port=6379

# Bidder settings
bidder.response.timeout.ms=100
bidder.bid.floor=0.01
bidder.max.bid=5.00

# Win/Loss notice URLs
bidder.win.url=http://backend:8000/api/rtb/win-notice
bidder.loss.url=http://backend:8000/api/rtb/loss-notice
```

## Step 4: Build and test

```bash
# Build all services
docker-compose up --build

# Test bidder health
curl http://localhost:8080/health

# Send a test Bid Request
curl -X POST http://localhost:8080/bidder \
  -H "Content-Type: application/json" \
  -d '{
    "id": "test-req-001",
    "imp": [{
      "id": "1",
      "banner": {"w": 300, "h": 250},
      "bidfloor": 0.01
    }],
    "site": {
      "domain": "example.com",
      "page": "https://example.com/article"
    },
    "device": {
      "ua": "Mozilla/5.0 ...",
      "ip": "1.2.3.4",
      "geo": {"country": "DEU"}
    }
  }'

# Expected response: OpenRTB 2.x Bid Response JSON with our bid
```

## Step 5: Test with RTB4FREE campaign-manager reference UI

```bash
# Optional — spin up the original Rails campaign manager to see RTB4FREE in action
git clone https://github.com/RTB4FREE/campaign-manager.git /tmp/rtb4free-cm
cd /tmp/rtb4free-cm
docker-compose up
# Open http://localhost:3000
# Note: we will replace this with our Vue 3 UI in Phase 3
```

## Updating RTB4FREE bidder

When RTB4FREE releases updates:

```bash
cd bidder/
git fetch upstream
git merge upstream/master
# Resolve conflicts (our customizations are in application.properties + win/loss URLs)
git push origin main
```
