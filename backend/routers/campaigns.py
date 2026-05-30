"""Campaign CRUD endpoints."""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Campaign, User
from schemas import CampaignCreate, CampaignOut, CampaignUpdate
from security import get_current_user

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _get_campaign_or_404(campaign_id: int, user: User, db: Session) -> Campaign:
    q = db.query(Campaign).filter(Campaign.id == campaign_id)
    if user.role != "admin":
        q = q.filter(Campaign.user_id == user.id)
    c = q.first()
    if not c:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return c


@router.get("/", response_model=List[CampaignOut])
def list_campaigns(
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(Campaign)
    if user.role != "admin":
        q = q.filter(Campaign.user_id == user.id)
    return q.offset(skip).limit(limit).all()


@router.post("/", response_model=CampaignOut, status_code=201)
def create_campaign(
    body: CampaignCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    camp = Campaign(
        user_id=user.id,
        **body.model_dump(),
        camp_code=secrets.token_urlsafe(8),
    )
    db.add(camp)
    db.commit()
    db.refresh(camp)
    return camp


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _get_campaign_or_404(campaign_id, user, db)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: int,
    body: CampaignUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    camp = _get_campaign_or_404(campaign_id, user, db)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(camp, field, val)
    db.commit()
    db.refresh(camp)
    return camp


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    camp = _get_campaign_or_404(campaign_id, user, db)
    db.delete(camp)
    db.commit()
