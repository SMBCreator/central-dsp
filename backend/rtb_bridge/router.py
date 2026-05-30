from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, func
from typing import Optional
from datetime import datetime, timezone
import logging
import os

from .schemas import (
    CentralCampaignFeed, CentralCampaign, CentralCreative, CentralTargeting,
    WinNotice, LossNotice, RTBStats
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rtb", tags=["CentralBidder Bridge"])

BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://backend:8000")


def get_db():
    """DB session dependency — imported from main.py in real impl"""
    # Placeholder: real impl imports SessionLocal from database.py
    pass


# ─────────────────────────────────────────
# CAMPAIGN FEED
# CentralBidder polls this every N seconds to get active campaigns
# Response is cached in Redis to minimise DB load
# ─────────────────────────────────────────
@router.get("/campaigns", response_model=CentralCampaignFeed)
async def get_campaign_feed(db: Session = Depends(get_db)):
    """
    CentralBidder polls this to get active campaigns with targeting + creatives.
    Cached in Redis for 30 seconds to avoid DB hammering during high QPS.
    """
    from database import SessionLocal, CampaignDB, CreativeDB, TargetingRuleDB
    import redis as redis_client
    import json

    CACHE_KEY = "central:rtb:campaign_feed"
    CACHE_TTL = 30

    r = redis_client.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))

    # Try cache first
    cached = r.get(CACHE_KEY)
    if cached:
        data = json.loads(cached)
        return CentralCampaignFeed(**data)

    # Build feed from DB
    session = SessionLocal()
    try:
        campaigns_db = session.execute(
            select(CampaignDB).where(CampaignDB.status == "active")
        ).scalars().all()

        central_campaigns = []
        for camp in campaigns_db:
            # Get targeting rules
            targeting_db = session.execute(
                select(TargetingRuleDB).where(TargetingRuleDB.campaign_id == camp.id)
            ).scalar_one_or_none()

            targeting = CentralTargeting(
                geo_countries=targeting_db.geo_countries if targeting_db else [],
                device_types=targeting_db.device_types if targeting_db else [],
                os_list=targeting_db.os_list if targeting_db else [],
                browser_list=targeting_db.browser_list if targeting_db else [],
                site_whitelist=targeting_db.site_whitelist if targeting_db else [],
                site_blacklist=targeting_db.site_blacklist if targeting_db else [],
                hour_schedule=targeting_db.hour_schedule if targeting_db else {},
                bid_floor=float(targeting_db.bid_floor) if targeting_db else 0.01,
                bid_ceiling=float(targeting_db.bid_ceiling) if targeting_db else 5.00,
            )

            # Get creatives
            creatives_db = session.execute(
                select(CreativeDB).where(
                    and_(CreativeDB.campaign_id == camp.id, CreativeDB.status == "active")
                )
            ).scalars().all()

            creatives = [
                CentralCreative(
                    id=str(c.id),
                    campaign_id=str(camp.id),
                    format=c.format,
                    width=c.width,
                    height=c.height,
                    image_url=c.image_url,
                    title=c.title,
                    description=c.description,
                    click_url=c.click_url or "",
                    adm=c.adm,
                    price_floor=float(targeting.bid_floor),
                    max_bid=float(targeting.bid_ceiling),
                )
                for c in creatives_db
            ]

            if not creatives:
                continue  # Skip campaigns with no active creatives

            central_campaigns.append(CentralCampaign(
                id=str(camp.id),
                name=camp.name,
                status=camp.status,
                daily_budget=float(camp.daily_budget) if camp.daily_budget else None,
                total_budget=float(camp.total_budget) if camp.total_budget else None,
                targeting=targeting,
                creatives=creatives,
                win_notice_url=f"{BACKEND_BASE_URL}/api/rtb/win-notice",
                loss_notice_url=f"{BACKEND_BASE_URL}/api/rtb/loss-notice",
            ))

        feed = CentralCampaignFeed(
            campaigns=central_campaigns,
            generated_at=datetime.now(timezone.utc),
        )

        # Cache result
        r.setex(CACHE_KEY, CACHE_TTL, feed.model_dump_json())

        return feed
    finally:
        session.close()


# ─────────────────────────────────────────
# WIN NOTICE
# CentralBidder calls this when we WIN an auction
# Records: auction_logs row + impression event in events table
# ─────────────────────────────────────────
@router.post("/win-notice")
async def win_notice(payload: WinNotice, background_tasks: BackgroundTasks):
    """
    Called by CentralBidder when we win an auction.
    Runs DB writes in background so we don't slow down the bidder.
    """
    background_tasks.add_task(_process_win_notice, payload)
    return {"status": "ok"}


async def _process_win_notice(payload: WinNotice):
    """Background task: write win to auction_logs + events"""
    from database import SessionLocal, AuctionLogDB, EventDB

    session = SessionLocal()
    try:
        # 1. Write to auction_logs
        log = AuctionLogDB(
            campaign_id=int(payload.campaign_id),
            creative_id=int(payload.creative_id),
            bid_request_id=payload.bid_request_id,
            imp_id=payload.imp_id,
            bid_price=payload.bid_price,
            win_price=payload.win_price,
            clearing_price=payload.clearing_price,
            won=True,
            ssp_name=payload.ssp_name,
            site_domain=payload.site_domain,
            app_bundle=payload.app_bundle,
            geo_country=payload.geo_country,
            device_type=payload.device_type,
        )
        session.add(log)

        # 2. Write impression event to events table (for unified analytics)
        event = EventDB(
            auction_id=payload.bid_request_id,
            imp_id=payload.imp_id,
            event_name="impression",
            value=payload.win_price,
            currency="USD",
            country_code=payload.geo_country,
            device_type=payload.device_type,
            metadata={
                "ssp": payload.ssp_name,
                "site_domain": payload.site_domain,
                "creative_id": payload.creative_id,
                "campaign_id": payload.campaign_id,
            }
        )
        session.add(event)
        session.commit()

        logger.info(
            f"[CentralBidder WIN] campaign={payload.campaign_id} "
            f"bid={payload.bid_price} win_price={payload.win_price} "
            f"ssp={payload.ssp_name} site={payload.site_domain}"
        )
    except Exception as e:
        session.rollback()
        logger.error(f"[CentralBidder WIN ERROR] {e}")
    finally:
        session.close()


# ─────────────────────────────────────────
# LOSS NOTICE
# CentralBidder calls this when we LOSE an auction
# Records in auction_logs for win_rate calculation
# ─────────────────────────────────────────
@router.post("/loss-notice")
async def loss_notice(payload: LossNotice, background_tasks: BackgroundTasks):
    """
    Called by CentralBidder when we lose an auction.
    Async — never blocks the bidder response cycle.
    """
    background_tasks.add_task(_process_loss_notice, payload)
    return {"status": "ok"}


async def _process_loss_notice(payload: LossNotice):
    """Background task: write loss to auction_logs"""
    from database import SessionLocal, AuctionLogDB

    session = SessionLocal()
    try:
        log = AuctionLogDB(
            campaign_id=int(payload.campaign_id),
            creative_id=int(payload.creative_id),
            bid_request_id=payload.bid_request_id,
            imp_id=payload.imp_id,
            bid_price=payload.bid_price,
            won=False,
            ssp_name=payload.ssp_name,
            site_domain=payload.site_domain,
            geo_country=payload.geo_country,
            device_type=payload.device_type,
        )
        session.add(log)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[CentralBidder LOSS ERROR] {e}")
    finally:
        session.close()


# ─────────────────────────────────────────
# RTB STATS (for the CentralBidder tab in UI)
# ─────────────────────────────────────────
@router.get("/stats", response_model=RTBStats)
async def get_rtb_stats(campaign_id: Optional[int] = None):
    """
    Aggregated RTB stats for today.
    Used by the CentralBidder dashboard tab in the UI.
    """
    from database import SessionLocal, AuctionLogDB
    from sqlalchemy import cast, Date, func

    session = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()

        base_query = select(AuctionLogDB).where(
            func.date(AuctionLogDB.created_at) == today
        )
        if campaign_id:
            base_query = base_query.where(AuctionLogDB.campaign_id == campaign_id)

        rows = session.execute(base_query).scalars().all()

        total = len(rows)
        won_rows = [r for r in rows if r.won]
        won = len(won_rows)

        win_rate = round((won / total * 100), 2) if total > 0 else 0.0
        avg_win_price = round(sum(r.win_price or 0 for r in won_rows) / won, 4) if won else 0.0
        avg_bid_price = round(sum(r.bid_price or 0 for r in rows) / total, 4) if total else 0.0
        spend = round(sum(r.win_price or 0 for r in won_rows), 4)
        cpm = round((spend / won * 1000), 2) if won else 0.0

        return RTBStats(
            auctions_total=total,
            auctions_won=won,
            win_rate=win_rate,
            impressions=won,
            avg_win_price=avg_win_price,
            avg_bid_price=avg_bid_price,
            spend_today=spend,
            cpm=cpm,
        )
    finally:
        session.close()


# ─────────────────────────────────────────
# INVALIDATE campaign cache (called when campaign is updated)
# ─────────────────────────────────────────
@router.post("/cache/invalidate")
async def invalidate_campaign_cache():
    """
    Invalidates Redis campaign feed cache.
    Call this after any campaign/creative/targeting update.
    """
    import redis as redis_client
    r = redis_client.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    r.delete("central:rtb:campaign_feed")
    return {"status": "ok", "message": "CentralBidder campaign cache invalidated"}
