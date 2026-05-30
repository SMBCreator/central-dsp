import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy.orm import Session
from fastapi import Depends
from database import get_db
from models import Campaign, Creative, TargetingRule, AuctionLog, Event, Pixel
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rtb", tags=["RTB Bridge"])


def _campaign_to_rtb4free(campaign: Campaign) -> Optional[dict]:
    """Convert CentralDSP campaign to RTB4FREE campaign format."""
    if campaign.status != "active":
        return None
    tr = campaign.targeting_rules[0] if campaign.targeting_rules else None
    creatives_data = []
    for cr in campaign.creatives:
        if cr.status != "active":
            continue
        creatives_data.append({
            "id": str(cr.id),
            "type": cr.format,
            "w": cr.width,
            "h": cr.height,
            "imageurl": cr.image_url,
            "title": cr.title,
            "adomain": [],
            "adm": cr.adm or "",
            "clickurl": cr.click_url or "",
        })
    if not creatives_data:
        return None
    return {
        "id": str(campaign.id),
        "name": campaign.name,
        "status": "active",
        "bidfloor": float(tr.bid_floor) if tr else settings.RTB_DEFAULT_BID_FLOOR,
        "bidceiling": float(tr.bid_ceiling) if tr else settings.RTB_MAX_BID_PRICE,
        "budget": float(campaign.daily_budget) if campaign.daily_budget else 0,
        "geo_whitelist": list(tr.geo_countries) if tr and tr.geo_countries else [],
        "device_types": list(tr.device_types) if tr and tr.device_types else [],
        "site_blacklist": list(tr.site_blacklist) if tr and tr.site_blacklist else [],
        "site_whitelist": list(tr.site_whitelist) if tr and tr.site_whitelist else [],
        "creatives": creatives_data,
        "win_url": f"http://backend:8000/api/rtb/win-notice",
        "loss_url": f"http://backend:8000/api/rtb/loss-notice",
    }


@router.get("/campaigns")
def get_rtb_campaigns(db: Session = Depends(get_db)):
    """
    RTB4FREE bidder polls this every N seconds to get active campaigns.
    Returns campaigns in RTB4FREE-compatible JSON format.
    """
    campaigns = db.query(Campaign).filter(Campaign.status == "active").all()
    rtb_campaigns = []
    for c in campaigns:
        rtb_c = _campaign_to_rtb4free(c)
        if rtb_c:
            rtb_campaigns.append(rtb_c)
    logger.debug(f"[RTB Bridge] Serving {len(rtb_campaigns)} campaigns")
    return {"campaigns": rtb_campaigns, "count": len(rtb_campaigns)}


@router.get("/creatives/{creative_id}")
def get_creative(creative_id: int, db: Session = Depends(get_db)):
    """Return creative markup for RTB4FREE bidder."""
    cr = db.query(Creative).filter(Creative.id == creative_id, Creative.status == "active").first()
    if not cr:
        raise HTTPException(status_code=404, detail="Creative not found")
    return {
        "id": cr.id, "format": cr.format, "width": cr.width, "height": cr.height,
        "image_url": cr.image_url, "title": cr.title, "description": cr.description,
        "click_url": cr.click_url, "adm": cr.adm,
    }


@router.post("/win-notice")
async def win_notice(request: Request, db: Session = Depends(get_db)):
    """
    Called by RTB4FREE bidder when we WIN an auction.
    Body (RTB4FREE format): {bid_request_id, imp_id, campaign_id, creative_id,
                              bid_price, win_price, ssp, site_domain, geo, device}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    campaign_id = int(body.get("campaign_id", 0))
    creative_id = body.get("creative_id")
    win_price = float(body.get("win_price") or body.get("clearing_price") or 0)
    bid_price = float(body.get("bid_price") or 0)

    # 1. Write to auction_logs
    log = AuctionLog(
        campaign_id=campaign_id if campaign_id else None,
        creative_id=int(creative_id) if creative_id else None,
        bid_request_id=body.get("bid_request_id"),
        imp_id=body.get("imp_id"),
        bid_price=bid_price,
        win_price=win_price,
        clearing_price=win_price,
        won=True,
        ssp_name=body.get("ssp"),
        site_domain=body.get("site_domain"),
        geo_country=body.get("geo"),
        device_type=body.get("device"),
    )
    db.add(log)

    # 2. Write impression event to events table
    if campaign_id:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if campaign and campaign.pixels:
            pixel = campaign.pixels[0]
            event = Event(
                pixel_id=pixel.pixel_id,
                auction_id=body.get("bid_request_id"),
                imp_id=body.get("imp_id"),
                event_name="impression",
                creative_id=str(creative_id) if creative_id else None,
                value=win_price,
                currency="USD",
                country_code=body.get("geo"),
                device_type=body.get("device"),
                metadata_={"source": "rtb", "ssp": body.get("ssp"), "win_price": win_price},
            )
            db.add(event)
            # Update pixel stats
            pixel.total_events = (pixel.total_events or 0) + 1
            pixel.last_fire_at = datetime.now(timezone.utc)
            db.add(pixel)

    db.commit()
    logger.info(f"[RTB WIN] campaign={campaign_id} bid={bid_price:.4f} win={win_price:.4f}")
    return {"status": "ok"}


@router.post("/loss-notice")
async def loss_notice(request: Request, db: Session = Depends(get_db)):
    """Record auction loss for win rate analytics."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    log = AuctionLog(
        campaign_id=int(body.get("campaign_id", 0)) or None,
        bid_request_id=body.get("bid_request_id"),
        imp_id=body.get("imp_id"),
        bid_price=float(body.get("bid_price") or 0),
        won=False,
        ssp_name=body.get("ssp"),
        site_domain=body.get("site_domain"),
        geo_country=body.get("geo"),
        device_type=body.get("device"),
    )
    db.add(log)
    db.commit()
    return {"status": "ok"}


@router.get("/stats/{campaign_id}")
def get_rtb_stats(campaign_id: int, db: Session = Depends(get_db)):
    """Quick RTB stats for a campaign: auctions, wins, win_rate, avg_cpm."""
    total = db.query(AuctionLog).filter(AuctionLog.campaign_id == campaign_id).count()
    wins = db.query(AuctionLog).filter(
        AuctionLog.campaign_id == campaign_id, AuctionLog.won == True
    ).count()
    from sqlalchemy import func as sqlfunc
    avg_win_price = db.query(sqlfunc.avg(AuctionLog.win_price)).filter(
        AuctionLog.campaign_id == campaign_id, AuctionLog.won == True
    ).scalar()
    win_rate = (wins / total * 100) if total else 0
    avg_cpm = float(avg_win_price or 0) * 1000  # CPM = win_price * 1000
    return {
        "campaign_id": campaign_id,
        "total_auctions": total,
        "wins": wins,
        "win_rate": round(win_rate, 2),
        "avg_cpm": round(avg_cpm, 4),
    }
