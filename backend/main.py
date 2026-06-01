"""Central DSP — FastAPI application entry point.

Router map:
  /auth/*          → routers/auth.py
  /api/users/*     → inline (admin only)
  /api/campaigns/* → routers/campaigns.py
  /api/links/*     → routers/links.py
  /api/pixels/*    → routers/pixels.py
  /api/rules/*     → routers/rules.py
  /api/targeting/* → routers/targeting.py
  /api/creatives/* → routers/creatives.py
  /api/stats/*     → routers/stats.py
  /api/ssp/*       → routers/ssp.py
  /s/* /c/* /px/*  → routers/tracking.py
  /postback        → routers/postback.py
  /rtb/*           → rtb_bridge/router.py
"""
import logging
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from database import get_db, engine
from models import (
    Base, User, Campaign, Link, Pixel, Event, Click, Postback,
    Stat, Rule, RuleLog, Setting, AdAccount,
    Creative, TargetingRule, AuctionLog,
    SSPConnection,
)
from auth import hash_password, get_current_user, require_role

# ── Routers ────────────────────────────────────────────────────────────────
from routers.auth import router as auth_router
from routers.campaigns import router as campaigns_router
from routers.links import router as links_router
from routers.pixels import router as pixels_router
from routers.tracking import router as tracking_router
from routers.postback import router as postback_router
from routers.rules import router as rules_router
from routers.targeting import router as targeting_router
from routers.creatives import router as creatives_router
from routers.stats import router as stats_router
from routers.ssp import router as ssp_router
from rtb_bridge.router import router as rtb_router

logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
app = FastAPI(
    title="Central DSP API",
    version="3.0.0",
    description="Self-hosted DSP: SmartLink + Tracker + AutoPilot + RTB (OpenRTB 2.5)",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Include all routers ─────────────────────────────────────────────────
app.include_router(auth_router)       # /auth/login, /auth/me, /auth/refresh
app.include_router(campaigns_router)  # /api/campaigns/*
app.include_router(links_router)      # /api/campaigns/{id}/links/*
app.include_router(pixels_router)     # /api/pixels/*
app.include_router(tracking_router)   # /s/{code}, /c/{code}, /px/*
app.include_router(postback_router)   # /postback
app.include_router(rules_router)      # /api/campaigns/{id}/rules/*
app.include_router(targeting_router)  # /api/campaigns/{id}/targeting
app.include_router(creatives_router)  # /api/campaigns/{id}/creatives/*
app.include_router(stats_router)      # /api/analytics/*
app.include_router(ssp_router)        # /api/ssp/*  — SSP connection management
app.include_router(rtb_router)        # /rtb/*      — OpenRTB 2.5 bid endpoint


# ── Startup ────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    # Create all tables (including SSPConnection)
    Base.metadata.create_all(bind=engine)
    db = next(get_db())
    try:
        # Create first admin if DB is empty
        if db.query(User).count() == 0:
            admin = User(
                email=settings.FIRST_ADMIN_EMAIL,
                password_hash=hash_password(settings.FIRST_ADMIN_PASSWORD),
                full_name="Admin",
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            logger.warning(
                f"[STARTUP] First admin created: {settings.FIRST_ADMIN_EMAIL} "
                f"/ {settings.FIRST_ADMIN_PASSWORD} — CHANGE PASSWORD!"
            )

        # Load SSP connections into manager
        from ssp.manager import ssp_manager
        ssp_manager.refresh(db)
        logger.info("[STARTUP] SSP manager loaded")

    finally:
        db.close()


# ── Health ────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {
        "status": "ok",
        "version": "3.0.0",
        "service": "central-dsp",
        "rtb_endpoint": f"{settings.BACKEND_BASE_URL}/rtb/bid",
    }


# ── Users (admin only) ──────────────────────────────────────────────────
class UserOut(BaseModel):
    id: int
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    class Config:
        from_attributes = True

class UserCreate(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    role: str = "viewer"


@app.get("/api/users", response_model=List[UserOut], tags=["Users"])
def list_users(
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    return db.query(User).order_by(User.id).all()


@app.post("/api/users", response_model=UserOut, status_code=201, tags=["Users"])
def create_user(
    body: UserCreate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email already exists")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.patch("/api/users/{user_id}", response_model=UserOut, tags=["Users"])
def update_user(
    user_id: int,
    body: UserCreate,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role:
        user.role = body.role
    if body.password:
        user.password_hash = hash_password(body.password)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/users/{user_id}", tags=["Users"])
def delete_user(
    user_id: int,
    admin: User = Depends(require_role("admin")),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}
