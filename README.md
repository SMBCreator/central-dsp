# 🎯 Central DSP

> Self-hosted Demand Side Platform (DSP) — based on [RTB4FREE](https://github.com/RTB4FREE) open-source stack.

**Stack:** Java (RTB4FREE bidder) + Python/FastAPI (backend) + Vue 3 + Vite + Tailwind (frontend) + PostgreSQL + Redis + Celery

---

## 🏗 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Central DSP                            │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌───────────────┐  │
│  │  RTB4FREE    │   │  FastAPI     │   │   Vue 3 UI    │  │
│  │  Bidder      │◄──│  Backend     │──►│   Frontend    │  │
│  │  (Java:8080) │   │  (:8000)     │   │   (:80)       │  │
│  └──────┬───────┘   └──────┬───────┘   └───────────────┘  │
│         │                  │                                │
│  ┌──────▼───────┐   ┌──────▼───────┐                       │
│  │  Redis 7     │   │  PostgreSQL  │                       │
│  │  (broker)    │   │  15 (main DB)│                       │
│  └──────────────┘   └──────────────┘                       │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐                       │
│  │  Celery      │   │  Flower      │                       │
│  │  Worker+Beat │   │  (:5555)     │                       │
│  └──────────────┘   └──────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start (Development)

```bash
# Clone
git clone https://github.com/SMBCreator/central-dsp.git
cd central-dsp

# Copy env
cp .env.example .env
# Edit .env — set your passwords and secrets

# Start all services
docker-compose up --build
```

Open **http://ads.local** (add `127.0.0.1 ads.local` to `/etc/hosts` or Windows hosts file)

| Service | URL |
|---|---|
| Main UI | http://ads.local |
| API Swagger | http://ads.local:8000/docs |
| Celery Flower | http://ads.local:5555 |
| RTB Bidder | http://ads.local:8080 |

## 📁 Project Structure

```
central-dsp/
├── bidder/              # RTB4FREE Java bidder (forked from RTB4FREE/bidder)
├── backend/             # FastAPI — tracker, SmartLink, AutoPilot, RTB Bridge
├── frontend/            # Vue 3 + Vite + Tailwind — main UI
├── docker-compose.yml   # All services
├── .env.example         # Environment variables template
└── docs/                # Architecture docs, API specs
```

## 🗺 Roadmap

| Phase | What | Status |
|---|---|---|
| Phase 0 | Fork RTB4FREE, monorepo setup | 🔄 In Progress |
| Phase 1 | RTB Bridge (FastAPI ↔ Java bidder) | ⬜ |
| Phase 2 | DB schema: creatives, targeting, auction_logs | ⬜ |
| Phase 3 | Vue UI: RTB tabs (Bidder, Creatives, Targeting) | ⬜ |
| Phase 4 (Step 0) | Pixel Center + S2S Postback + Events TimeSeries | ⬜ |
| Phase 5 (Step 1) | JWT Auth + multi-user + roles | ⬜ |
| Phase 6 | Connect first SSP (Beeswax test endpoint) | ⬜ |
| Phase 7 | Bidding algorithm + AutoPilot RTB rules | ⬜ |
| Phase 8 (Step 2) | Analytics + Charts + Attribution + CSV export | ⬜ |
| Phase 9 (Step 4) | Celery full migration + Flower | ⬜ |
| Phase 10 (Step 3) | Ad Networks API (Yandex, Kadam, PropellerAds) | ⬜ |
| Phase 11 (Step 6) | Production: Caddy SSL, Grafana, MaxMind, Backups | ⬜ |

## 🔗 Based On

- [RTB4FREE/bidder](https://github.com/RTB4FREE/bidder) — OpenRTB 2.0 bidder (Apache 2.0)
- [RTB4FREE/campaign-manager](https://github.com/RTB4FREE/campaign-manager) — Campaign UI reference
- CentralDSP v2 — SmartLink + Tracker + AutoPilot (private, already built)

## 📄 License

Private. All rights reserved.
