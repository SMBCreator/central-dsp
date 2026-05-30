"""Pixel tracking endpoints."""
import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Pixel, Event, User
from schemas import PixelCreate, PixelOut, EventCreate
from security import get_current_user

router = APIRouter(tags=["pixels"])

# 1x1 transparent gif
_PIXEL_GIF = bytes([
    0x47,0x49,0x46,0x38,0x39,0x61,0x01,0x00,0x01,0x00,0x80,0x00,0x00,
    0xFF,0xFF,0xFF,0x00,0x00,0x00,0x21,0xF9,0x04,0x00,0x00,0x00,0x00,
    0x00,0x2C,0x00,0x00,0x00,0x00,0x01,0x00,0x01,0x00,0x00,0x02,0x02,
    0x44,0x01,0x00,0x3B
])


@router.post("/campaigns/{campaign_id}/pixels", response_model=PixelOut, status_code=201)
def create_pixel(
    campaign_id: int,
    body: PixelCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    p = Pixel(campaign_id=campaign_id, name=body.name)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/campaigns/{campaign_id}/pixels", response_model=List[PixelOut])
def list_pixels(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return db.query(Pixel).filter(Pixel.campaign_id == campaign_id).all()


@router.get("/p/{pixel_id}.gif")
def fire_pixel_gif(pixel_id: str, request: Request, db: Session = Depends(get_db)):
    """Fire pixel via 1x1 GIF (image tag embed)."""
    _record_event(pixel_id, request, db)
    return Response(
        content=_PIXEL_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.post("/p/{pixel_id}/event")
def fire_pixel_event(
    pixel_id: str,
    body: EventCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Fire pixel via JSON POST (JS/server-side)."""
    event = _record_event(pixel_id, request, db, body)
    return {"ok": True, "event_id": event.id}


@router.get("/postback")
def s2s_postback(
    pixel_id: str = None,
    click_id: str = None,
    event: str = "conversion",
    value: float = 0.0,
    sub_id: str = "unknown",
    request: Request = None,
    db: Session = Depends(get_db),
):
    """S2S postback from affiliate network."""
    if not pixel_id and not click_id:
        raise HTTPException(status_code=400, detail="pixel_id or click_id required")

    pixel = None
    if pixel_id:
        try:
            pid = uuid.UUID(pixel_id)
            pixel = db.query(Pixel).filter(Pixel.pixel_id == pid).first()
        except ValueError:
            pass

    ev = Event(
        pixel_id=pixel.pixel_id if pixel else None,
        event_name=event,
        click_id=click_id,
        value=value,
        sub_id=sub_id,
        ip=request.client.host if request and request.client else None,
    )
    db.add(ev)
    if pixel:
        pixel.total_events += 1
        from sqlalchemy.sql import func
        from datetime import datetime, timezone
        pixel.last_fire_at = datetime.now(timezone.utc)
    db.commit()
    return Response(content="OK", media_type="text/plain")


def _record_event(
    pixel_id_str: str,
    request: Request,
    db: Session,
    body: EventCreate = None,
) -> Event:
    try:
        pid = uuid.UUID(pixel_id_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pixel_id")

    pixel = db.query(Pixel).filter(Pixel.pixel_id == pid, Pixel.is_active == True).first()
    if not pixel:
        raise HTTPException(status_code=404, detail="Pixel not found")

    ev = Event(
        pixel_id=pid,
        event_name=body.event_name if body else "pageview",
        sub_id=body.sub_id if body else request.query_params.get("sub_id", "unknown"),
        click_id=body.click_id if body else request.query_params.get("click_id"),
        value=body.value if body else 0.0,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        referer=body.referer if body else request.headers.get("referer"),
        metadata_=body.metadata if body else {},
    )
    db.add(ev)
    pixel.total_events += 1
    from datetime import datetime, timezone
    pixel.last_fire_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ev)
    return ev
