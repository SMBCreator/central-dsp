import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from backend.database import get_db
from backend import models, geo

router = APIRouter(tags=["tracking"])

_PIXEL_GIF = bytes(b"GIF89a\x01\x00\x01\x00\x00\xff\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x00;")

@router.get("/r/{short_code}", include_in_schema=False)
def redirect_link(short_code: str, request: Request, db: Session = Depends(get_db)):
    link = db.query(models.Link).filter(models.Link.short_code == short_code, models.Link.is_active == True).first()
    if not link:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if link.expires_at and link.expires_at < datetime.now(timezone.utc):
        return JSONResponse({"error": "Expired"}, status_code=410)

    ip = request.client.host
    ua = request.headers.get("User-Agent", "")
    sub_id = request.query_params.get("sub_id") or request.query_params.get("sub")
    creative_id = request.query_params.get("creative_id")
    click_id = uuid.uuid4()
    country = geo.get_country(ip)
    device = geo.detect_device(ua)

    click = models.Click(
        link_id=link.id, sub_id=sub_id, creative_id=creative_id,
        click_id=click_id, ip=ip, country_code=country, device_type=device,
        cost=float(link.cost_per_click or 0),
    )
    db.add(click)

    stat = db.query(models.Stat).filter(
        models.Stat.link_id == link.id,
        models.Stat.sub_id == sub_id,
        models.Stat.creative_id == creative_id,
    ).first()
    if stat:
        stat.clicks += 1
        stat.cost = float(stat.cost or 0) + float(link.cost_per_click or 0)
    else:
        db.add(models.Stat(link_id=link.id, sub_id=sub_id, creative_id=creative_id, clicks=1, cost=float(link.cost_per_click or 0)))

    db.commit()

    dest = link.original_url
    if "{click_id}" in dest:
        dest = dest.replace("{click_id}", str(click_id))
    if "{sub_id}" in dest and sub_id:
        dest = dest.replace("{sub_id}", sub_id)
    return RedirectResponse(url=dest, status_code=302)

@router.get("/p/{pixel_id}", include_in_schema=False)
def fire_pixel(pixel_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        pid = uuid.UUID(pixel_id)
    except ValueError:
        return Response(content=_PIXEL_GIF, media_type="image/gif")

    pixel = db.query(models.Pixel).filter(models.Pixel.pixel_id == pid).first()
    if pixel and pixel.is_active:
        ip = request.client.host
        ua = request.headers.get("User-Agent", "")
        params = dict(request.query_params)
        event = models.Event(
            pixel_id=pid,
            event_name=params.get("event", "pageview"),
            sub_id=params.get("sub_id"),
            creative_id=params.get("creative_id"),
            click_id=params.get("click_id"),
            value=float(params.get("value", 0)),
            ip=ip,
            country_code=geo.get_country(ip),
            device_type=geo.detect_device(ua),
            os=geo.detect_os(ua),
            browser=geo.detect_browser(ua),
            user_agent=ua,
            referer=request.headers.get("Referer", ""),
            metadata_=params,
        )
        db.add(event)
        pixel.total_events = (pixel.total_events or 0) + 1
        pixel.last_fire_at = datetime.now(timezone.utc)
        db.commit()

    return Response(content=_PIXEL_GIF, media_type="image/gif", headers={"Cache-Control": "no-store"})
