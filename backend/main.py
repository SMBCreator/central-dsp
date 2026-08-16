"""CentralDSP backend: management API + OpenRTB 2.5 bidder.

Routes are defined WITHOUT the /api prefix; Vercel strips it before
forwarding (see vercel.json experimentalServices).
"""
from fastapi import FastAPI

from routers.advertisers import router as advertisers_router
from routers.auth_router import router as auth_router
from routers.campaigns import router as campaigns_router
from routers.creatives import router as creatives_router
from routers.line_items import router as line_items_router
from routers.rtb import router as rtb_router
from routers.rules import router as rules_router
from routers.simulator import router as simulator_router
from routers.stats import router as stats_router
from routers.supply_partners import router as supply_partners_router

app = FastAPI(
    title="CentralDSP API",
    version="1.0.0",
    description="Demand-Side Platform: management API + OpenRTB 2.5 bidder",
)

app.include_router(auth_router)
app.include_router(advertisers_router)
app.include_router(campaigns_router)
app.include_router(line_items_router)
app.include_router(creatives_router)
app.include_router(supply_partners_router)
app.include_router(rules_router)
app.include_router(stats_router)
app.include_router(rtb_router)
app.include_router(simulator_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "centraldsp"}
