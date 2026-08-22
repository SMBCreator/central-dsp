"""CentralDSP backend: management API + OpenRTB 2.5 bidder.

Routes are defined WITHOUT the /api prefix; Vercel strips it before
forwarding (see vercel.json experimentalServices).
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from routers.advertisers import router as advertisers_router
from routers.auth_router import router as auth_router
from routers.billing import router as billing_router
from routers.campaigns import router as campaigns_router
from routers.creatives import router as creatives_router
from routers.cron import router as cron_router
from routers.groups import router as groups_router
from routers.line_items import router as line_items_router
from routers.pixel import router as pixel_router
from routers.rtb import router as rtb_router
from routers.ord_router import router as ord_router
from routers.rules import router as rules_router
from routers.share import router as share_router
from routers.simulator import router as simulator_router
from routers.stats import router as stats_router
from routers.supply_partners import router as supply_partners_router

from audit import AuditMiddleware

app = FastAPI(
    title="CentralDSP API",
    version="1.0.0",
    description="Demand-Side Platform: management API + OpenRTB 2.5 bidder",
)

app.add_middleware(AuditMiddleware)

app.include_router(auth_router)
app.include_router(advertisers_router)
app.include_router(campaigns_router)
app.include_router(line_items_router)
app.include_router(creatives_router)
app.include_router(supply_partners_router)
app.include_router(rules_router)
app.include_router(stats_router)
app.include_router(rtb_router)
app.include_router(ord_router)
app.include_router(simulator_router)
app.include_router(cron_router)
app.include_router(billing_router)
app.include_router(share_router)
app.include_router(pixel_router)
app.include_router(groups_router)


_VIEWER_WRITE_ALLOWED = {"/auth/logout", "/auth/login"}


@app.middleware("http")
async def viewer_readonly_guard(request: Request, call_next):
    """Guest (viewer) accounts are read-only: block all mutating requests."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path not in _VIEWER_WRITE_ALLOWED:
        token = request.cookies.get("cdsp_session")
        if token:
            from db import get_pool
            pool = await get_pool()
            role = await pool.fetchval(
                "SELECT u.role FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = $1",
                token,
            )
            if role == "viewer":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Гостевой доступ доступен только для просмотра"},
                )
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "centraldsp"}
