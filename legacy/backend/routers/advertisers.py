"""
Advertisers router — CRUD для рекламодателей.
Рекламодатель — юридическое/финансовое лицо, которому принадлежат кампании.
URL prefix: /api/advertisers
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from pydantic import BaseModel, Field
from decimal import Decimal
from datetime import datetime

from database import get_db
from auth import get_current_user
from models import User

router = APIRouter(prefix="/api/advertisers", tags=["advertisers"])


# ─── Pydantic schemas ────────────────────────────────────────────────────────

class AdvertiserCreate(BaseModel):
    name:         str             = Field(..., min_length=1, max_length=200)
    daily_budget: Optional[Decimal] = Field(None, ge=0)
    total_budget: Optional[Decimal] = Field(None, ge=0)
    currency:     str             = Field("USD", min_length=3, max_length=3)


class AdvertiserUpdate(BaseModel):
    name:         Optional[str]     = Field(None, min_length=1, max_length=200)
    daily_budget: Optional[Decimal] = Field(None, ge=0)
    total_budget: Optional[Decimal] = Field(None, ge=0)
    currency:     Optional[str]     = Field(None, min_length=3, max_length=3)
    status:       Optional[str]     = Field(None, pattern="^(active|paused|archived)$")


class AdvertiserOut(BaseModel):
    id:           int
    user_id:      int
    name:         str
    daily_budget: Optional[Decimal]
    total_budget: Optional[Decimal]
    currency:     str
    status:       str
    created_at:   datetime
    campaign_count: Optional[int] = 0

    class Config:
        from_attributes = True


# ─── Helpers ─────────────────────────────────────────────────────────────────

def get_advertiser_or_404(advertiser_id: int, user: User, db: Session):
    from models import Advertiser
    adv = db.query(Advertiser).filter(
        Advertiser.id == advertiser_id,
        Advertiser.user_id == user.id,
    ).first()
    if not adv:
        raise HTTPException(status_code=404, detail="Advertiser not found")
    return adv


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("", response_model=List[AdvertiserOut])
def list_advertisers(
    db:      Session = Depends(get_db),
    current: User    = Depends(get_current_user),
):
    """Список всех рекламодателей текущего пользователя."""
    from models import Advertiser, Campaign
    advertisers = db.query(Advertiser).filter(
        Advertiser.user_id == current.id
    ).order_by(Advertiser.created_at.desc()).all()

    result = []
    for adv in advertisers:
        count = db.query(func.count(Campaign.id)).filter(
            Campaign.advertiser_id == adv.id
        ).scalar() or 0
        out = AdvertiserOut.from_orm(adv)
        out.campaign_count = count
        result.append(out)
    return result


@router.post("", response_model=AdvertiserOut, status_code=status.HTTP_201_CREATED)
def create_advertiser(
    payload: AdvertiserCreate,
    db:      Session = Depends(get_db),
    current: User    = Depends(get_current_user),
):
    from models import Advertiser
    adv = Advertiser(
        user_id=current.id,
        name=payload.name,
        daily_budget=payload.daily_budget,
        total_budget=payload.total_budget,
        currency=payload.currency,
    )
    db.add(adv)
    db.commit()
    db.refresh(adv)
    out = AdvertiserOut.from_orm(adv)
    out.campaign_count = 0
    return out


@router.get("/{advertiser_id}", response_model=AdvertiserOut)
def get_advertiser(
    advertiser_id: int,
    db:      Session = Depends(get_db),
    current: User    = Depends(get_current_user),
):
    from models import Campaign
    adv = get_advertiser_or_404(advertiser_id, current, db)
    count = db.query(func.count(Campaign.id)).filter(
        Campaign.advertiser_id == adv.id
    ).scalar() or 0
    out = AdvertiserOut.from_orm(adv)
    out.campaign_count = count
    return out


@router.patch("/{advertiser_id}", response_model=AdvertiserOut)
def update_advertiser(
    advertiser_id: int,
    payload: AdvertiserUpdate,
    db:      Session = Depends(get_db),
    current: User    = Depends(get_current_user),
):
    adv = get_advertiser_or_404(advertiser_id, current, db)
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(adv, field, value)
    db.commit()
    db.refresh(adv)
    out = AdvertiserOut.from_orm(adv)
    return out


@router.delete("/{advertiser_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_advertiser(
    advertiser_id: int,
    db:      Session = Depends(get_db),
    current: User    = Depends(get_current_user),
):
    adv = get_advertiser_or_404(advertiser_id, current, db)
    db.delete(adv)
    db.commit()
