import random, string
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend import models, auth

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

def _gen_code(length=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

class CampaignIn(BaseModel):
    name: str
    status: str = "active"
    is_ab: bool = False
    ab_limit: Optional[int] = None
    offer_value: float = 0
    attribution_model: str = "last_click"
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None

@router.get("")
def list_campaigns(db: Session = Depends(get_db), current_user=Depends(auth.get_current_user)):
    q = db.query(models.Campaign)
    if current_user.role != "admin":
        q = q.filter(models.Campaign.user_id == current_user.id)
    return [{"id": c.id, "name": c.name, "status": c.status, "camp_code": c.camp_code, "created_at": c.created_at} for c in q.order_by(models.Campaign.created_at.desc()).all()]

@router.post("", status_code=201)
def create_campaign(body: CampaignIn, db: Session = Depends(get_db), current_user=Depends(auth.ensure_manager_or_admin)):
    code = _gen_code()
    while db.query(models.Campaign).filter(models.Campaign.camp_code == code).first():
        code = _gen_code()
    c = models.Campaign(user_id=current_user.id, camp_code=code, **body.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": c.id, "camp_code": c.camp_code}

@router.get("/{campaign_id}")
def get_campaign(campaign_id: int, db: Session = Depends(get_db), current_user=Depends(auth.get_current_user)):
    c = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    return c

@router.patch("/{campaign_id}")
def update_campaign(campaign_id: int, body: CampaignIn, db: Session = Depends(get_db), current_user=Depends(auth.ensure_manager_or_admin)):
    c = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    return {"ok": True}

@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, db: Session = Depends(get_db), current_user=Depends(auth.ensure_admin)):
    c = db.query(models.Campaign).filter(models.Campaign.id == campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    db.delete(c)
    db.commit()
