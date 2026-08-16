from __future__ import annotations
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from database import SessionLocal
from auth import get_current_user
from models import TargetingRule, Campaign, User

router = APIRouter(prefix="/api/targeting", tags=["Targeting"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TargetingUpsert(BaseModel):
    bid_floor: Optional[float] = 0.0
    bid_ceiling: Optional[float] = 10.0
    geo_countries: Optional[List[str]] = []       # ["RU", "US", "DE"]
    device_types: Optional[List[str]] = []         # ["mobile", "desktop"]
    os_list: Optional[List[str]] = []              # ["android", "ios"]
    browser_list: Optional[List[str]] = []         # ["chrome", "safari"]
    site_whitelist: Optional[List[str]] = []       # domains
    site_blacklist: Optional[List[str]] = []
    hour_schedule: Optional[Dict[str, List[int]]] = {}   # {"0":[8,9,10,...], "1":[...]}


class TargetingOut(BaseModel):
    id: int
    campaign_id: int
    bid_floor: Optional[float]
    bid_ceiling: Optional[float]
    geo_countries: Optional[List[str]]
    device_types: Optional[List[str]]
    os_list: Optional[List[str]]
    browser_list: Optional[List[str]]
    site_whitelist: Optional[List[str]]
    site_blacklist: Optional[List[str]]
    hour_schedule: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _own_campaign(db: Session, campaign_id: int, user: User) -> Campaign:
    c = db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.user_id == user.id)
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.put("/{campaign_id}", response_model=TargetingOut)
def upsert_targeting(
    campaign_id: int,
    payload: TargetingUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create or fully replace targeting rules for a campaign."""
    _own_campaign(db, campaign_id, current_user)
    t = db.execute(
        select(TargetingRule).where(TargetingRule.campaign_id == campaign_id)
    ).scalar_one_or_none()
    if t is None:
        t = TargetingRule(campaign_id=campaign_id)
    for field, value in payload.model_dump().items():
        setattr(t, field, value)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/{campaign_id}", response_model=TargetingOut)
def get_targeting(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _own_campaign(db, campaign_id, current_user)
    t = db.execute(
        select(TargetingRule).where(TargetingRule.campaign_id == campaign_id)
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="No targeting rules found")
    return t


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_targeting(
    campaign_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _own_campaign(db, campaign_id, current_user)
    t = db.execute(
        select(TargetingRule).where(TargetingRule.campaign_id == campaign_id)
    ).scalar_one_or_none()
    if t:
        db.delete(t)
        db.commit()
