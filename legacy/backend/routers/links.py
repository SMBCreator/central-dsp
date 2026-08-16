import random, string
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend import models, auth

router = APIRouter(prefix="/links", tags=["links"])

def _gen_code(length=6):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))

class LinkIn(BaseModel):
    campaign_id: int
    original_url: str
    platform: Optional[str] = None
    external_id: Optional[str] = None
    custom_slug: Optional[str] = None
    cost_per_click: float = 0
    geo_rules: dict = {}
    device_rules: dict = {}

@router.get("")
def list_links(campaign_id: Optional[int] = None, db: Session = Depends(get_db), current_user=Depends(auth.get_current_user)):
    q = db.query(models.Link)
    if campaign_id:
        q = q.filter(models.Link.campaign_id == campaign_id)
    return q.order_by(models.Link.id.desc()).limit(500).all()

@router.post("", status_code=201)
def create_link(body: LinkIn, db: Session = Depends(get_db), current_user=Depends(auth.ensure_manager_or_admin)):
    c = db.query(models.Campaign).filter(models.Campaign.id == body.campaign_id).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    short_code = body.custom_slug or _gen_code()
    while db.query(models.Link).filter(models.Link.short_code == short_code).first():
        short_code = _gen_code()
    link = models.Link(
        campaign_id=body.campaign_id,
        original_url=body.original_url,
        platform=body.platform,
        external_id=body.external_id,
        custom_slug=body.custom_slug,
        short_code=short_code,
        cost_per_click=body.cost_per_click,
        geo_rules=body.geo_rules,
        device_rules=body.device_rules,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return {"id": link.id, "short_code": link.short_code}

@router.delete("/{link_id}", status_code=204)
def delete_link(link_id: int, db: Session = Depends(get_db), current_user=Depends(auth.ensure_manager_or_admin)):
    link = db.query(models.Link).filter(models.Link.id == link_id).first()
    if not link:
        raise HTTPException(404, "Link not found")
    db.delete(link)
    db.commit()
