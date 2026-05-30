"""Link (SmartLink) CRUD endpoints."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from database import get_db
from models import Campaign, Link, Click, User
from schemas import LinkCreate, LinkOut
from security import get_current_user
import uuid

router = APIRouter(tags=["links"])


@router.get("/campaigns/{campaign_id}/links", response_model=List[LinkOut])
def list_links(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return db.query(Link).filter(Link.campaign_id == campaign_id).all()


@router.post("/campaigns/{campaign_id}/links", response_model=LinkOut, status_code=201)
def create_link(
    campaign_id: int,
    body: LinkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    camp = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")

    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    link = Link(
        campaign_id=campaign_id,
        original_url=body.original_url,
        platform=body.platform,
        external_id=body.external_id,
        custom_slug=body.custom_slug,
        cost_per_click=body.cost_per_click,
        geo_rules=body.geo_rules,
        device_rules=body.device_rules,
        expires_at=expires_at,
        short_code=secrets.token_urlsafe(6),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


@router.delete("/links/{link_id}", status_code=204)
def delete_link(
    link_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    link = db.query(Link).filter(Link.id == link_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    db.delete(link)
    db.commit()


@router.get("/r/{code}")
def redirect_link(code: str, request: Request, db: Session = Depends(get_db)):
    """Public redirect endpoint. Records click, returns 302."""
    link = (
        db.query(Link)
        .filter((Link.short_code == code) | (Link.custom_slug == code))
        .first()
    )
    if not link or not link.is_active:
        raise HTTPException(status_code=404, detail="Link not found")

    if link.expires_at and link.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Link expired")

    # Record click
    click = Click(
        link_id=link.id,
        click_id=uuid.uuid4(),
        ip=request.client.host if request.client else None,
        sub_id=request.query_params.get("sub_id", "unknown"),
        creative_id=request.query_params.get("creative", "unknown"),
        cost=link.cost_per_click,
    )
    db.add(click)
    db.commit()

    # Build redirect URL with click_id macro
    url = link.original_url
    if "{click_id}" in url:
        url = url.replace("{click_id}", str(click.click_id))

    return Response(
        status_code=302,
        headers={"Location": url, "Cache-Control": "no-store"},
    )
