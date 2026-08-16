from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from database import SessionLocal
from auth import get_current_user
from models import Creative, Campaign, User

router = APIRouter(prefix="/api/creatives", tags=["Creatives"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CreativeCreate(BaseModel):
    campaign_id: int
    format: str          # banner | native | video
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: str


class CreativeUpdate(BaseModel):
    format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: Optional[str] = None
    status: Optional[str] = None   # active | paused | archived


class CreativeOut(BaseModel):
    id: int
    campaign_id: int
    format: str
    width: Optional[int]
    height: Optional[int]
    image_url: Optional[str]
    title: Optional[str]
    description: Optional[str]
    click_url: str
    status: str
    audit_status: Optional[str]

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_campaign_or_404(db: Session, campaign_id: int, user: User) -> Campaign:
    c = db.execute(
        select(Campaign).where(Campaign.id == campaign_id, Campaign.user_id == user.id)
    ).scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


def _get_creative_or_404(db: Session, creative_id: int, user: User) -> Creative:
    cr = db.execute(
        select(Creative).where(Creative.id == creative_id)
    ).scalar_one_or_none()
    if not cr:
        raise HTTPException(status_code=404, detail="Creative not found")
    _get_campaign_or_404(db, cr.campaign_id, user)   # ownership check
    return cr


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", response_model=CreativeOut, status_code=status.HTTP_201_CREATED)
def create_creative(
    payload: CreativeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_campaign_or_404(db, payload.campaign_id, current_user)
    cr = Creative(
        campaign_id=payload.campaign_id,
        format=payload.format,
        width=payload.width,
        height=payload.height,
        image_url=payload.image_url,
        title=payload.title,
        description=payload.description,
        click_url=payload.click_url,
        status="active",
        audit_status="pending",
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return cr


@router.get("", response_model=List[CreativeOut])
def list_creatives(
    campaign_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = select(Creative).join(Campaign, Campaign.id == Creative.campaign_id).where(
        Campaign.user_id == current_user.id
    )
    if campaign_id:
        q = q.where(Creative.campaign_id == campaign_id)
    return db.execute(q).scalars().all()


@router.get("/{creative_id}", response_model=CreativeOut)
def get_creative(
    creative_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_creative_or_404(db, creative_id, current_user)


@router.patch("/{creative_id}", response_model=CreativeOut)
def update_creative(
    creative_id: int,
    payload: CreativeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cr = _get_creative_or_404(db, creative_id, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(cr, field, value)
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return cr


@router.delete("/{creative_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_creative(
    creative_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cr = _get_creative_or_404(db, creative_id, current_user)
    cr.status = "archived"
    db.add(cr)
    db.commit()
