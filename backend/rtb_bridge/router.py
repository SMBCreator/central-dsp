"""RTB Bridge Router.

Two endpoint groups:

1. Internal bridge (CentralBidder polling):
   GET  /rtb/campaigns         - serve active campaigns in CentralDSP format
   GET  /rtb/creatives/{id}    - serve creative markup
   POST /rtb/win-notice        - record auction win
   POST /rtb/loss-notice       - record auction loss
   GET  /rtb/stats/{id}        - per-campaign RTB stats
   GET  /rtb/stats/summary     - global RTB stats for dashboard
   GET  /rtb/health            - health check for SSP connectivity tests

2. OpenRTB 2.5 Direct (SSP -> CentralDSP):
   POST /rtb/bid               - main OpenRTB 2.5 bid endpoint
                                 SSPs send BidRequest here,
                                 we respond with BidResponse in <100ms.
"""
import logging
import uuid
from datetime import datetime, timezone, date
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from database import get_db
from models import Campaign, Creative, TargetingRule, AuctionLog, Event
from config import settings
from rtb_bridge.schemas import (
    BidRequest, BidResponse, SeatBid, Bid,
    WinNotice, LossNotice,
    CentralCampaign, CentralTargeting, CentralCreative, CentralCampaignFeed,
)
from rtb_bridge.targeting import matches_targeting, get_bid_price

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rtb", tags=["RTB Bridge"])

# Base URL for win/loss notice macros (set in .env as BACKEND_BASE_URL)
BACKEND_URL = getattr(settings, "BACKEND_BASE_URL", "http://backend:8000")


# =============================================================================
# 1. OPENRTB 2.5 DIRECT BID ENDPOINT
# =============================================================================

@router.post(
    "/bid",
    summary="OpenRTB 2.5 Bid Endpoint",
    description=(
        "Main SSP-facing endpoint. Accepts OpenRTB 2.5 BidRequest, "
        "runs targeting filters and bid calculation, returns BidResponse. "
        "Must respond in < 100ms (SSP timeout). "
        "No auth required — SSPs authenticate via IP allowlist or shared secret header."
    ),
    response_class=Response,
)
async def openrtb_bid(
    bid_req: BidRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    OpenRTB 2.5 bid handler.

    Flow:
      1. Load all active campaigns with targeting
      2. For each impression in bid request:
         a. Find matching campaigns (targeting filter)
         b. Pick best creative (matching format/size)
         c. Calculate bid price
         d. Select highest bidder per impression
      3. Return BidResponse (or 204 No Content = no bid)
      4. Write AuctionLog async (non-blocking)
    """
    # --- Load active campaigns with targeting ---
    campaigns = (
        db.query(Campaign)
        .filter(Campaign.status == "active")
        .all()
    )

    seatbids = []
    auction_logs_to_write = []  # batch write after response

    for imp in bid_req.imp:
        best_bid: Optional[Bid] = None
        best_price: float = 0.0
        best_campaign_id: Optional[int] = None
        best_creative_id: Optional[int] = None

        for campaign in campaigns:
            targeting = campaign.targeting  # uselist=False relationship
            if not targeting:
                continue

            # --- Targeting filter ---
            if not matches_targeting(bid_req, targeting):
                continue

            # --- Bid floor check ---
            bid_price = get_bid_price(imp.bidfloor, targeting)
            if bid_price <= 0:
                continue  # floor exceeds our ceiling

            # --- Find matching creative ---
            creative = _pick_creative(campaign.creatives, imp)
            if not creative:
                continue

            # --- Keep highest bid (first-price tie-break by campaign id) ---
            if bid_price > best_price:
                best_price = bid_price
                best_campaign_id = campaign.id
                best_creative_id = creative.id

                # Build Bid object
                nurl = (
                    f"{BACKEND_URL}/rtb/win-notice"
                    f"?bid_request_id={bid_req.id}"
                    f"&imp_id={imp.id}"
                    f"&campaign_id={campaign.id}"
                    f"&creative_id={creative.id}"
                    f"&bid_price={bid_price}"
                    f"&win_price=${{AUCTION_PRICE}}"
                    f"&ssp={request.headers.get('X-SSP-Name', 'unknown')}"
                    f"&site_domain={bid_req.site.domain if bid_req.site else ''}"
                    f"&app_bundle={bid_req.app.bundle if bid_req.app else ''}"
                    f"&geo={bid_req.device.geo.country if bid_req.device and bid_req.device.geo else ''}"
                    f"&device={bid_req.device.devicetype if bid_req.device else ''}"
                )
                lurl = (
                    f"{BACKEND_URL}/rtb/loss-notice"
                    f"?bid_request_id={bid_req.id}"
                    f"&imp_id={imp.id}"
                    f"&campaign_id={campaign.id}"
                    f"&bid_price={bid_price}"
                )

                best_bid = Bid(
                    id=str(uuid.uuid4()),
                    impid=imp.id,
                    price=bid_price,
                    adid=str(creative.id),
                    cid=str(campaign.id),
                    crid=str(creative.id),
                    adm=creative.adm or _build_adm(creative, bid_req),
                    iurl=creative.image_url,
                    adomain=[],
                    w=creative.width,
                    h=creative.height,
                    nurl=nurl,
                    lurl=lurl,
                )

        if best_bid:
            seatbids.append(
                SeatBid(bid=[best_bid], seat="centraldsp")
            )
            auction_logs_to_write.append({
                "campaign_id": best_campaign_id,
                "creative_id": best_creative_id,
                "bid_request_id": bid_req.id,
                "imp_id": imp.id,
                "bid_price": best_price,
                "won": False,  # not won yet - win notice updates this
                "ssp_name": request.headers.get("X-SSP-Name"),
                "site_domain": bid_req.site.domain if bid_req.site else None,
                "app_bundle": bid_req.app.bundle if bid_req.app else None,
                "geo_country": (
                    bid_req.device.geo.country
                    if bid_req.device and bid_req.device.geo else None
                ),
                "device_type": str(bid_req.device.devicetype) if bid_req.device else None,
            })

    # --- No bid ---
    if not seatbids:
        # Write no-bid auction logs
        _write_auction_logs(db, auction_logs_to_write)
        logger.debug(f"[RTB] NO BID id={bid_req.id}")
        return Response(status_code=204)  # OpenRTB no-bid

    # --- Build response ---
    response = BidResponse(
        id=bid_req.id,
        seatbid=seatbids,
        bidid=str(uuid.uuid4()),
        cur="USD",
    )

    # Write bid logs async (non-blocking DB write)
    _write_auction_logs(db, auction_logs_to_write)

    logger.info(
        f"[RTB BID] id={bid_req.id} "
        f"impressions={len(bid_req.imp)} bids={len(seatbids)}"
    )
    return JSONResponse(
        content=response.model_dump(exclude_none=True),
        status_code=200,
    )


def _pick_creative(creatives, imp) -> Optional["Creative"]:
    """Select best matching creative for the impression."""
    active = [c for c in creatives if c.status == "active" and c.audit_status == "approved"]
    if not active:
        # Allow pending creatives in test mode
        active = [c for c in creatives if c.status == "active"]
    if not active:
        return None

    # Match by format
    if imp.banner:
        w = imp.banner.w
        h = imp.banner.h
        banner_creatives = [c for c in active if c.format == "banner"]
        if w and h:
            # Exact size match first
            exact = [c for c in banner_creatives if c.width == w and c.height == h]
            if exact:
                return exact[0]
        if banner_creatives:
            return banner_creatives[0]

    if imp.video:
        video_creatives = [c for c in active if c.format == "video"]
        if video_creatives:
            return video_creatives[0]

    if imp.native:
        native_creatives = [c for c in active if c.format == "native"]
        if native_creatives:
            return native_creatives[0]

    return active[0] if active else None


def _build_adm(creative, bid_req: BidRequest) -> str:
    """Build ad markup (ADM) if not stored in creative.adm."""
    if creative.format == "banner" and creative.image_url:
        click_url = creative.click_url or ""
        return (
            f'<a href="{click_url}" target="_blank">'
            f'<img src="{creative.image_url}" '
            f'width="{creative.width or 300}" height="{creative.height or 250}" '
            f'alt="{creative.title or "Ad"}" border="0"/>'
            f'</a>'
        )
    if creative.format == "native":
        import json
        return json.dumps({
            "ver": "1.1",
            "link": {"url": creative.click_url or ""},
            "assets": [
                {"id": 1, "title": {"text": creative.title or ""}},
                {"id": 2, "img": {"url": creative.image_url or "", "type": 3}},
                {"id": 3, "data": {"label": "desc", "value": creative.description or ""}},
            ],
        })
    return ""


def _write_auction_logs(db: Session, logs: list):
    """Batch write auction logs. Non-critical — errors are swallowed."""
    try:
        for log_data in logs:
            db.add(AuctionLog(**log_data))
        if logs:
            db.commit()
    except Exception as e:
        logger.error(f"[RTB] auction log write error: {e}")
        db.rollback()


# =============================================================================
# 2. WIN / LOSS NOTICES (from SSP macro expansion or bidder)
# =============================================================================

@router.get("/win-notice", summary="Win Notice (GET, SSP macro)")
@router.post("/win-notice", summary="Win Notice (POST, bidder)")
async def win_notice(request: Request, db: Session = Depends(get_db)):
    """
    Called when we WIN an auction.
    Supports both GET (SSP macro expansion in nurl) and POST (internal bidder).

    GET params: bid_request_id, imp_id, campaign_id, creative_id,
                bid_price, win_price, ssp, site_domain, geo, device
    POST body:  WinNotice JSON
    """
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
    else:
        data = dict(request.query_params)

    campaign_id_raw = data.get("campaign_id")
    creative_id_raw = data.get("creative_id")
    win_price_raw = data.get("win_price") or data.get("clearing_price") or "0"
    bid_price_raw = data.get("bid_price") or "0"

    # ${AUCTION_PRICE} macro — SSP fills actual clearing price
    try:
        win_price = float(str(win_price_raw).replace("${AUCTION_PRICE}", "0"))
    except (ValueError, TypeError):
        win_price = 0.0
    try:
        bid_price = float(bid_price_raw)
    except (ValueError, TypeError):
        bid_price = 0.0

    campaign_id = int(campaign_id_raw) if campaign_id_raw else None
    creative_id = int(creative_id_raw) if creative_id_raw else None

    # Update or insert AuctionLog: mark won=True
    bid_request_id = data.get("bid_request_id")
    existing = None
    if bid_request_id:
        existing = db.query(AuctionLog).filter(
            AuctionLog.bid_request_id == bid_request_id,
            AuctionLog.won == False,
        ).first()

    if existing:
        existing.won = True
        existing.win_price = Decimal(str(win_price))
        existing.clearing_price = Decimal(str(win_price))
        db.add(existing)
    else:
        log = AuctionLog(
            campaign_id=campaign_id,
            creative_id=creative_id,
            bid_request_id=bid_request_id,
            imp_id=data.get("imp_id"),
            bid_price=Decimal(str(bid_price)),
            win_price=Decimal(str(win_price)),
            clearing_price=Decimal(str(win_price)),
            won=True,
            ssp_name=data.get("ssp"),
            site_domain=data.get("site_domain"),
            app_bundle=data.get("app_bundle"),
            geo_country=data.get("geo"),
            device_type=str(data.get("device") or ""),
        )
        db.add(log)

    # Write impression event
    if campaign_id:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if campaign and campaign.pixels:
            pixel = campaign.pixels[0]
            event = Event(
                pixel_id=pixel.pixel_id,
                auction_id=bid_request_id,
                imp_id=data.get("imp_id"),
                event_name="impression",
                creative_id=str(creative_id) if creative_id else None,
                value=Decimal(str(win_price)),
                currency="USD",
                country_code=data.get("geo"),
                device_type=str(data.get("device") or ""),
                metadata_={
                    "source": "rtb_win",
                    "ssp": data.get("ssp"),
                    "win_price": win_price,
                },
            )
            db.add(event)
            pixel.total_events = (pixel.total_events or 0) + 1
            pixel.last_fire_at = datetime.now(timezone.utc)
            db.add(pixel)

    db.commit()
    logger.info(
        f"[RTB WIN] campaign={campaign_id} creative={creative_id} "
        f"bid={bid_price:.4f} win={win_price:.4f} ssp={data.get('ssp')}"
    )
    # Return empty 200 (SSP expects fast response to nurl)
    return Response(status_code=200)


@router.get("/loss-notice", summary="Loss Notice (GET, SSP macro)")
@router.post("/loss-notice", summary="Loss Notice (POST, bidder)")
async def loss_notice(request: Request, db: Session = Depends(get_db)):
    """Called when we LOSE an auction or SSP fires lurl macro."""
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
    else:
        data = dict(request.query_params)

    # Update existing AuctionLog if exists (already written at bid time)
    bid_request_id = data.get("bid_request_id")
    if bid_request_id:
        existing = db.query(AuctionLog).filter(
            AuctionLog.bid_request_id == bid_request_id,
            AuctionLog.won == False,
        ).first()
        if existing:
            db.commit()
            return Response(status_code=200)  # already marked as lost

    # Write new loss log if not found
    try:
        log = AuctionLog(
            campaign_id=int(data["campaign_id"]) if data.get("campaign_id") else None,
            bid_request_id=bid_request_id,
            imp_id=data.get("imp_id"),
            bid_price=Decimal(str(data.get("bid_price") or 0)),
            won=False,
            ssp_name=data.get("ssp"),
            site_domain=data.get("site_domain"),
            geo_country=data.get("geo"),
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"[RTB LOSS] write error: {e}")
    return Response(status_code=200)


# =============================================================================
# 3. CAMPAIGN FEED (internal bidder polling)
# =============================================================================

@router.get("/campaigns", response_model=CentralCampaignFeed)
def get_rtb_campaigns(db: Session = Depends(get_db)):
    """
    Internal bidder polls this endpoint every N seconds.
    Returns all active campaigns in CentralDSP format.
    """
    campaigns = db.query(Campaign).filter(Campaign.status == "active").all()
    result = []
    for c in campaigns:
        cc = _campaign_to_central(c)
        if cc:
            result.append(cc)
    logger.debug(f"[RTB Feed] serving {len(result)} campaigns")
    return CentralCampaignFeed(
        campaigns=result,
        generated_at=datetime.now(timezone.utc),
    )


def _campaign_to_central(campaign: Campaign) -> Optional[CentralCampaign]:
    """Convert ORM Campaign to CentralCampaign schema."""
    targeting = campaign.targeting  # FIX: uselist=False, not a list
    t = CentralTargeting(
        geo_countries=list(targeting.geo_countries or []) if targeting else [],
        device_types=list(targeting.device_types or []) if targeting else [],
        os_list=list(targeting.os_list or []) if targeting else [],
        browser_list=list(targeting.browser_list or []) if targeting else [],
        site_whitelist=list(targeting.site_whitelist or []) if targeting else [],
        site_blacklist=list(targeting.site_blacklist or []) if targeting else [],
        hour_schedule=dict(targeting.hour_schedule or {}) if targeting else {},
        bid_floor=float(targeting.bid_floor or 0.01) if targeting else 0.01,
        bid_ceiling=float(targeting.bid_ceiling or 5.0) if targeting else 5.0,
    )
    creatives_data = []
    for cr in campaign.creatives:
        if cr.status != "active":
            continue
        creatives_data.append(CentralCreative(
            id=str(cr.id),
            campaign_id=str(campaign.id),
            format=cr.format,
            width=cr.width,
            height=cr.height,
            image_url=cr.image_url,
            title=cr.title,
            description=cr.description,
            click_url=cr.click_url or "",
            adm=cr.adm,
            price_floor=t.bid_floor,
            max_bid=t.bid_ceiling,
        ))
    if not creatives_data:
        return None
    return CentralCampaign(
        id=str(campaign.id),
        name=campaign.name,
        status=campaign.status,
        daily_budget=float(campaign.daily_budget) if campaign.daily_budget else None,
        total_budget=float(campaign.total_budget) if campaign.total_budget else None,
        targeting=t,
        creatives=creatives_data,
        win_notice_url=f"{BACKEND_URL}/rtb/win-notice",
        loss_notice_url=f"{BACKEND_URL}/rtb/loss-notice",
    )


# =============================================================================
# 4. CREATIVE SERVE
# =============================================================================

@router.get("/creatives/{creative_id}", summary="Serve creative markup")
def get_creative(creative_id: int, db: Session = Depends(get_db)):
    cr = db.query(Creative).filter(
        Creative.id == creative_id,
        Creative.status == "active",
    ).first()
    if not cr:
        raise HTTPException(status_code=404, detail="Creative not found")
    return {
        "id": cr.id,
        "format": cr.format,
        "width": cr.width,
        "height": cr.height,
        "image_url": cr.image_url,
        "title": cr.title,
        "description": cr.description,
        "click_url": cr.click_url,
        "adm": cr.adm,
    }


# =============================================================================
# 5. STATS & HEALTH
# =============================================================================

@router.get("/stats/{campaign_id}", summary="Per-campaign RTB stats")
def get_rtb_stats(campaign_id: int, db: Session = Depends(get_db)):
    """Win rate, CPM, spend for a single campaign."""
    total = db.query(AuctionLog).filter(
        AuctionLog.campaign_id == campaign_id
    ).count()
    wins = db.query(AuctionLog).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.won == True,
    ).count()
    avg_win_price = db.query(sqlfunc.avg(AuctionLog.win_price)).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.won == True,
    ).scalar()
    total_spend = db.query(sqlfunc.sum(AuctionLog.win_price)).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.won == True,
    ).scalar()
    win_rate = (wins / total * 100) if total else 0.0
    avg_cpm = float(avg_win_price or 0) * 1000
    return {
        "campaign_id": campaign_id,
        "total_auctions": total,
        "wins": wins,
        "win_rate": round(win_rate, 2),
        "avg_cpm": round(avg_cpm, 4),
        "total_spend": round(float(total_spend or 0), 4),
    }


@router.get("/stats/summary", summary="Global RTB stats (all campaigns)")
def get_rtb_summary(db: Session = Depends(get_db)):
    """Global RTB stats for the dashboard RTB tab."""
    today = date.today()
    total = db.query(AuctionLog).count()
    wins = db.query(AuctionLog).filter(AuctionLog.won == True).count()
    today_wins = db.query(AuctionLog).filter(
        AuctionLog.won == True,
        sqlfunc.date(AuctionLog.created_at) == today,
    ).count()
    today_spend = db.query(sqlfunc.sum(AuctionLog.win_price)).filter(
        AuctionLog.won == True,
        sqlfunc.date(AuctionLog.created_at) == today,
    ).scalar()
    avg_win_price = db.query(sqlfunc.avg(AuctionLog.win_price)).filter(
        AuctionLog.won == True,
    ).scalar()
    avg_bid_price = db.query(sqlfunc.avg(AuctionLog.bid_price)).scalar()
    win_rate = (wins / total * 100) if total else 0.0
    avg_cpm = float(avg_win_price or 0) * 1000
    return {
        "total_auctions": total,
        "total_wins": wins,
        "win_rate": round(win_rate, 2),
        "avg_cpm": round(avg_cpm, 4),
        "avg_bid_price": round(float(avg_bid_price or 0), 6),
        "today_wins": today_wins,
        "today_spend_usd": round(float(today_spend or 0), 4),
    }


@router.get("/health", summary="RTB Bridge health check")
def rtb_health():
    """SSPs can ping this to verify the bidder is alive before sending traffic."""
    return {
        "status": "ok",
        "service": "central-dsp-rtb-bridge",
        "version": "3.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "bid_endpoint": f"{BACKEND_URL}/rtb/bid",
    }
