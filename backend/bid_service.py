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
    """Liveness: process is up (used by Fly.io/VPS restart policy)."""
    return {"status": "ok", "service": "centraldsp-bidder"}


@app.get("/ready")
async def ready():
    """Readiness: dependencies reachable (DB + Redis). NOT on the hot path."""
    checks = {}
    try:
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:  # pragma: no cover
        checks["database"] = f"error: {type(e).__name__}"
    try:
        from cache import get_redis
        r = get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:  # pragma: no cover
        checks["redis"] = f"error: {type(e).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=200 if healthy else 503,
                        content={"status": "ok" if healthy else "degraded", **checks})
