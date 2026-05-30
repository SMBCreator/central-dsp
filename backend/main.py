import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from database import engine
from models import Base
from rtb_bridge.router import router as rtb_router

logging.basicConfig(level=os.getenv("LOG_LEVEL", "info").upper())
logger = logging.getLogger("centraldsp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create all tables if not exist"""
    logger.info("Central DSP starting up...")
    Base.metadata.create_all(bind=engine)
    logger.info("DB tables verified.")
    yield
    logger.info("Central DSP shutting down.")


app = FastAPI(
    title="Central DSP API",
    version="0.3.0",
    description="Self-hosted DSP: SmartLink Tracker + AutoPilot + RTB4FREE Bidder",
    lifespan=lifespan,
)

# CORS — allow Vue frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # narrow down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# Routers
# ─────────────────────────────────────────
app.include_router(rtb_router)

# TODO: add routers as implemented:
# from routers.auth     import router as auth_router;     app.include_router(auth_router)
# from routers.campaigns import router as campaigns_router; app.include_router(campaigns_router)
# from routers.links     import router as links_router;     app.include_router(links_router)
# from routers.pixels    import router as pixels_router;    app.include_router(pixels_router)
# from routers.analytics import router as analytics_router; app.include_router(analytics_router)
# from routers.rules     import router as rules_router;     app.include_router(rules_router)
# from routers.adnetworks import router as adnet_router;   app.include_router(adnet_router)


# ─────────────────────────────────────────
# Health check — no auth required, used by Docker healthcheck
# ─────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "version": app.version}


# ─────────────────────────────────────────
# Global error handler
# ─────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
