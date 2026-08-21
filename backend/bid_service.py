"""Standalone bid service: OpenRTB hot path only (always-on deploy).

Runs ONLY the RTB router + health. Management API, cron, simulator and the
rest keep living on Vercel; both processes share the same Neon database and
the same Redis, so pausing a campaign in the UI is visible to the bidder
within the candidate-cache TTL.

Run locally:   uvicorn bid_service:app --port 8080
Docker image:  see Dockerfile.bid
"""
from fastapi import FastAPI

from routers.rtb import router as rtb_router

app = FastAPI(
    title="CentralDSP Bid Service",
    version="1.0.0",
    description="OpenRTB 2.5 bidder hot path (bid/win/imp/click/vevent)",
)

app.include_router(rtb_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "centraldsp-bidder"}
