import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from database import get_db
from models import CampaignDB, CreativeDB, TargetingRuleDB, AuctionLogDB, EventDB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rtb", tags=["RTB Bridge"])


# ─────────────────────────────────────────
# Pydantic schemas for RTB4FREE communication
# ─────────────────────────────────────────
class RTBCreative(BaseModel):
    id: int
    format: str
    width: Optional[int] = None
    height: Optional[int] = None
    adm: Optional[str] = None         # ad markup (banner HTML or VAST)
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: Optional[str] = None


class RTBTargeting(BaseModel):
    bid_floor: float = 0.01
    bid_ceiling: float = 5.00
    geo_countries: list[str] = []
    device_types: list[str] = []
    os_list: list[str] = []
    site_whitelist: list[str] = []
    site_blacklist: list[str] = []
    hour_schedule: dict = {}


class RTBCampaign(BaseModel):
    """Campaign format expected by RTB4FREE bidder"""
    id: int
    name: str
    status: str
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    targeting: RTBTargeting
    creatives: list[RTBCreative]


class WinNotice(BaseModel):
    """RTB4FREE win notice payload"""
    bid_request_id: str
    imp_id: Optional[str] = None
    campaign_id: int
    creative_id: Optional[int] = None
    win_price: float
    clearing_price: Optional[float] = None
    ssp_name: Optional[str] = None
    site_domain: Optional[str] = None
    app_bundle: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None
    auction_id: Optional[str] = None


class LossNotice(BaseModel):
    """RTB4FREE loss notice payload"""
    bid_request_id: str
    imp_id: Optional[str] = None
    campaign_id: int
    creative_id: Optional[int] = None
    bid_price: float
    loss_reason: Optional[int] = None   # OpenRTB loss reason code
    ssp_name: Optional[str] = None
    site_domain: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


# ─────────────────────────────────────────
# GET /api/rtb/campaigns
# RTB4FREE polls this to get active campaigns + targeting + creatives
# ─────────────────────────────────────────
@router.get("/campaigns", response_model=list[RTBCampaign])
def get_rtb_campaigns(db: Session = Depends(get_db)):
    """
    Returns ALL active campaigns with their targeting rules and approved creatives.
    RTB4FREE bidder polls this endpoint (configurable interval, default 60s).
    Only campaigns with status='active' and at least one approved creative are returned.
    """
    campaigns = (
        db.query(CampaignDB)
        .filter(CampaignDB.status == "active")
        .all()
    )

    result = []
    for camp in campaigns:
        # Only include campaigns that have at least one approved creative
        approved_creatives = [
            c for c in camp.creatives
            if c.status == "active" and c.audit_status == "approved"
        ]
        if not approved_creatives:
            continue

        targeting = camp.targeting
        rtb_targeting = RTBTargeting(
            bid_floor=float(targeting.bid_floor) if targeting else 0.01,
            bid_ceiling=float(targeting.bid_ceiling) if targeting else 5.00,
            geo_countries=targeting.geo_countries if targeting else [],
            device_types=targeting.device_types if targeting else [],
            os_list=targeting.os_list if targeting else [],
            site_whitelist=targeting.site_whitelist if targeting else [],
            site_blacklist=targeting.site_blacklist if targeting else [],
            hour_schedule=targeting.hour_schedule if targeting else {},
        )

        rtb_creatives = [
            RTBCreative(
                id=c.id,
                format=c.format,
                width=c.width,
                height=c.height,
                adm=c.adm,
                image_url=c.image_url,
                title=c.title,
                description=c.description,
                click_url=c.click_url,
            )
            for c in approved_creatives
        ]

        result.append(RTBCampaign(
            id=camp.id,
            name=camp.name,
            status=camp.status,
            daily_budget=float(camp.daily_budget) if camp.daily_budget else None,
            total_budget=float(camp.total_budget) if camp.total_budget else None,
            targeting=rtb_targeting,
            creatives=rtb_creatives,
        ))

    logger.debug(f"[RTB Bridge] Returning {len(result)} active campaigns to bidder")
    return result


# ─────────────────────────────────────────
# POST /api/rtb/win-notice
# Called by bidder when we WIN an auction
# ─────────────────────────────────────────
@router.post("/win-notice")
def win_notice(notice: WinNotice, db: Session = Depends(get_db)):
    """
    RTB4FREE calls this when our bid wins an auction.
    1. Record in auction_logs (won=True, win_price)
    2. Create 'impression' event in events table for attribution chain
    """
    # 1. Auction log
    log = AuctionLogDB(
        campaign_id=notice.campaign_id,
        creative_id=notice.creative_id,
        bid_request_id=notice.bid_request_id,
        imp_id=notice.imp_id,
        win_price=notice.win_price,
        clearing_price=notice.clearing_price,
        won=True,
        ssp_name=notice.ssp_name,
        site_domain=notice.site_domain,
        app_bundle=notice.app_bundle,
        geo_country=notice.geo_country,
        device_type=notice.device_type,
    )
    db.add(log)

    # 2. Impression event — start of attribution chain: impression → click → postback
    event = EventDB(
        auction_id=notice.auction_id or notice.bid_request_id,
        imp_id=notice.imp_id,
        event_name="impression",
        value=notice.win_price,
        currency="USD",
        country_code=notice.geo_country,
        device_type=notice.device_type,
        metadata={
            "ssp": notice.ssp_name,
            "site_domain": notice.site_domain,
            "creative_id": notice.creative_id,
        },
    )
    db.add(event)
    db.commit()

    logger.info(
        f"[RTB WIN] campaign={notice.campaign_id} win_price={notice.win_price:.4f} "
        f"ssp={notice.ssp_name} geo={notice.geo_country}"
    )
    return {"status": "ok", "auction_log_id": log.id}


# ─────────────────────────────────────────
# POST /api/rtb/loss-notice
# Called by bidder when we LOSE an auction
# ─────────────────────────────────────────
@router.post("/loss-notice")
def loss_notice(notice: LossNotice, db: Session = Depends(get_db)):
    """
    RTB4FREE calls this when our bid loses.
    Records in auction_logs for win_rate calculation by BidOptimizer.
    """
    log = AuctionLogDB(
        campaign_id=notice.campaign_id,
        creative_id=notice.creative_id,
        bid_request_id=notice.bid_request_id,
        imp_id=notice.imp_id,
        bid_price=notice.bid_price,
        won=False,
        ssp_name=notice.ssp_name,
        site_domain=notice.site_domain,
        geo_country=notice.geo_country,
        device_type=notice.device_type,
    )
    db.add(log)
    db.commit()

    logger.debug(
        f"[RTB LOSS] campaign={notice.campaign_id} bid={notice.bid_price:.4f} "
        f"reason={notice.loss_reason} ssp={notice.ssp_name}"
    )
    return {"status": "ok"}


# ─────────────────────────────────────────
# GET /api/rtb/creatives/{creative_id}
# RTB4FREE fetches individual creative markup
# ─────────────────────────────────────────
@router.get("/creatives/{creative_id}", response_model=RTBCreative)
def get_creative(creative_id: int, db: Session = Depends(get_db)):
    creative = db.query(CreativeDB).filter(
        CreativeDB.id == creative_id,
        CreativeDB.status == "active",
    ).first()
    if not creative:
        raise HTTPException(status_code=404, detail="Creative not found")
    return RTBCreative(
        id=creative.id,
        format=creative.format,
        width=creative.width,
        height=creative.height,
        adm=creative.adm,
        image_url=creative.image_url,
        title=creative.title,
        description=creative.description,
        click_url=creative.click_url,
    )


# ─────────────────────────────────────────
# GET /api/rtb/stats/{campaign_id}
# RTB performance summary for a campaign (for UI dashboard)
# ─────────────────────────────────────────
@router.get("/stats/{campaign_id}")
def get_rtb_stats(campaign_id: int, db: Session = Depends(get_db)):
    """
    RTB performance summary for the UI 'RTB Bidder' tab.
    Returns: total auctions, wins, win_rate, avg_win_price, total_spend.
    """
    from sqlalchemy import func as sqlfunc
    from datetime import datetime, timezone
    import datetime as dt

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    row = db.query(
        sqlfunc.count(AuctionLogDB.id).label("total_auctions"),
        sqlfunc.sum(
            sqlfunc.cast(AuctionLogDB.won, db.bind.dialect.type_descriptor(type(True)))
        ).label("wins"),
        sqlfunc.avg(AuctionLogDB.win_price).label("avg_win_price"),
        sqlfunc.sum(AuctionLogDB.win_price).label("total_spend"),
    ).filter(
        AuctionLogDB.campaign_id == campaign_id,
        AuctionLogDB.created_at >= today_start,
    ).first()

    total = row.total_auctions or 0
    wins  = int(row.wins or 0)
    return {
        "campaign_id":    campaign_id,
        "period":         "today",
        "total_auctions": total,
        "wins":           wins,
        "win_rate":        round(wins / total * 100, 2) if total else 0,
        "avg_win_price":  round(float(row.avg_win_price or 0), 6),
        "total_spend":    round(float(row.total_spend or 0), 4),
    }
