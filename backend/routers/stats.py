"""Analytics & Stats router — /api/analytics/*

Endpoints:
  GET /api/analytics/summary           — overall dashboard stats (clicks, conversions, ROI)
  GET /api/analytics/campaigns/{id}    — per-campaign breakdown
  GET /api/analytics/timeseries        — hourly/daily time series for charts
  GET /api/analytics/top-sources       — top sub_ids / sites by conversions
  GET /api/analytics/rtb/summary       — RTB global stats (win rate, CPM, spend)
  GET /api/analytics/rtb/hourly        — hourly win rate + CPM for chart
  GET /api/analytics/rtb/top-sites     — top sites by spend + win rate
  GET /api/analytics/rtb/{campaign_id} — per-campaign RTB breakdown
"""
from datetime import datetime, timezone, timedelta, date
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc, cast, Date

from database import get_db
from auth import get_current_user
from models import (
    User, Campaign, Event, Click, Postback,
    AuctionLog, Stat,
)

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


# ─────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────
def _date_filter(model, field_name: str, days: int):
    """Return SQLAlchemy filter for last N days."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return getattr(model, field_name) >= since


# ─────────────────────────────────────────────────────
# 1. OVERALL SUMMARY
# ─────────────────────────────────────────────────────
@router.get("/summary")
def get_summary(
    days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dashboard summary card: clicks, events, conversions, revenue, ROI."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Clicks
    total_clicks = db.query(sqlfunc.count(Click.id)).filter(
        Click.created_at >= since
    ).scalar() or 0

    # Events by name
    events_q = db.query(
        Event.event_name,
        sqlfunc.count(Event.id).label("count"),
        sqlfunc.coalesce(sqlfunc.sum(Event.value), 0).label("revenue")
    ).filter(Event.created_at >= since).group_by(Event.event_name).all()

    total_revenue = sum(float(r.revenue) for r in events_q)
    total_conversions = sum(r.count for r in events_q if r.event_name not in ("impression", "click", "pageview"))
    total_impressions = sum(r.count for r in events_q if r.event_name == "impression")

    # RTB spend
    rtb_spend = db.query(
        sqlfunc.coalesce(sqlfunc.sum(AuctionLog.win_price), 0)
    ).filter(
        AuctionLog.won == True,
        AuctionLog.created_at >= since,
    ).scalar() or Decimal("0")

    roi = 0.0
    if float(rtb_spend) > 0:
        roi = ((total_revenue - float(rtb_spend)) / float(rtb_spend)) * 100

    return {
        "days": days,
        "total_clicks": total_clicks,
        "total_impressions": total_impressions,
        "total_conversions": total_conversions,
        "total_revenue": round(total_revenue, 4),
        "rtb_spend_usd": round(float(rtb_spend), 4),
        "roi_pct": round(roi, 2),
        "events": [
            {"name": r.event_name, "count": r.count, "revenue": round(float(r.revenue), 4)}
            for r in events_q
        ],
    }


# ─────────────────────────────────────────────────────
# 2. PER-CAMPAIGN STATS
# ─────────────────────────────────────────────────────
@router.get("/campaigns/{campaign_id}")
def get_campaign_stats(
    campaign_id: int,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)

    clicks = db.query(sqlfunc.count(Click.id)).join(
        Click.link
    ).filter(
        Click.created_at >= since,
        Click.link.has(campaign_id=campaign_id),
    ).scalar() or 0

    events = db.query(
        Event.event_name,
        sqlfunc.count(Event.id).label("count"),
        sqlfunc.coalesce(sqlfunc.sum(Event.value), 0).label("revenue"),
    ).join(Event.pixel
    ).filter(
        Event.created_at >= since,
        Event.pixel.has(campaign_id=campaign_id),
    ).group_by(Event.event_name).all()

    rtb = db.query(
        sqlfunc.count(AuctionLog.id).label("auctions"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, 1), else_=0)
        ), 0).label("wins"),
        sqlfunc.coalesce(sqlfunc.sum(AuctionLog.win_price), 0).label("spend"),
        sqlfunc.coalesce(sqlfunc.avg(AuctionLog.win_price), 0).label("avg_cpm_raw"),
    ).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.created_at >= since,
    ).first()

    win_rate = (float(rtb.wins) / float(rtb.auctions) * 100) if rtb.auctions else 0
    avg_cpm = float(rtb.avg_cpm_raw or 0) * 1000

    return {
        "campaign_id": campaign_id,
        "days": days,
        "clicks": clicks,
        "events": [{"name": e.event_name, "count": e.count, "revenue": round(float(e.revenue), 4)} for e in events],
        "rtb": {
            "auctions": int(rtb.auctions or 0),
            "wins": int(rtb.wins or 0),
            "win_rate_pct": round(win_rate, 2),
            "spend_usd": round(float(rtb.spend or 0), 4),
            "avg_cpm_usd": round(avg_cpm, 4),
        },
    }


# ─────────────────────────────────────────────────────
# 3. TIME SERIES (for charts)
# ─────────────────────────────────────────────────────
@router.get("/timeseries")
def get_timeseries(
    days: int = Query(7, ge=1, le=90),
    granularity: str = Query("day", regex="^(hour|day)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hourly or daily time series for dashboard charts."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    if granularity == "hour":
        trunc = sqlfunc.date_trunc("hour", AuctionLog.created_at)
    else:
        trunc = sqlfunc.date_trunc("day", AuctionLog.created_at)

    rows = db.query(
        trunc.label("ts"),
        sqlfunc.count(AuctionLog.id).label("auctions"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, 1), else_=0)
        ), 0).label("wins"),
        sqlfunc.coalesce(sqlfunc.sum(AuctionLog.win_price), 0).label("spend"),
        sqlfunc.coalesce(sqlfunc.avg(AuctionLog.win_price), 0).label("avg_win_price"),
    ).filter(
        AuctionLog.created_at >= since
    ).group_by(trunc).order_by(trunc).all()

    return {
        "granularity": granularity,
        "days": days,
        "series": [
            {
                "ts": r.ts.isoformat() if r.ts else None,
                "auctions": int(r.auctions or 0),
                "wins": int(r.wins or 0),
                "win_rate_pct": round(
                    float(r.wins) / float(r.auctions) * 100 if r.auctions else 0, 2
                ),
                "spend_usd": round(float(r.spend or 0), 6),
                "cpm": round(float(r.avg_win_price or 0) * 1000, 4),
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────
# 4. TOP SOURCES
# ─────────────────────────────────────────────────────
@router.get("/top-sources")
def get_top_sources(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(
        Event.sub_id,
        sqlfunc.count(Event.id).label("events"),
        sqlfunc.coalesce(sqlfunc.sum(Event.value), 0).label("revenue"),
    ).filter(
        Event.created_at >= since,
        Event.event_name.notin_(["impression", "click", "pageview"]),
    ).group_by(Event.sub_id).order_by(
        sqlfunc.sum(Event.value).desc()
    ).limit(limit).all()

    return {
        "days": days,
        "sources": [
            {"sub_id": r.sub_id, "events": r.events, "revenue": round(float(r.revenue), 4)}
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────
# 5. RTB GLOBAL SUMMARY
# ─────────────────────────────────────────────────────
@router.get("/rtb/summary")
def get_rtb_summary(
    days: int = Query(1, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """RTB global stats for the dashboard RTB tab."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    total = db.query(sqlfunc.count(AuctionLog.id)).filter(
        AuctionLog.created_at >= since
    ).scalar() or 0
    wins = db.query(sqlfunc.count(AuctionLog.id)).filter(
        AuctionLog.won == True,
        AuctionLog.created_at >= since,
    ).scalar() or 0
    today_wins = db.query(sqlfunc.count(AuctionLog.id)).filter(
        AuctionLog.won == True,
        AuctionLog.created_at >= today,
    ).scalar() or 0
    today_spend = db.query(
        sqlfunc.coalesce(sqlfunc.sum(AuctionLog.win_price), 0)
    ).filter(
        AuctionLog.won == True,
        AuctionLog.created_at >= today,
    ).scalar() or Decimal("0")
    avg_win = db.query(
        sqlfunc.coalesce(sqlfunc.avg(AuctionLog.win_price), 0)
    ).filter(AuctionLog.won == True, AuctionLog.created_at >= since).scalar() or Decimal("0")
    avg_bid = db.query(
        sqlfunc.coalesce(sqlfunc.avg(AuctionLog.bid_price), 0)
    ).filter(AuctionLog.created_at >= since).scalar() or Decimal("0")

    win_rate = (wins / total * 100) if total else 0.0
    return {
        "days": days,
        "total_auctions": total,
        "total_wins": wins,
        "win_rate_pct": round(win_rate, 2),
        "avg_cpm_usd": round(float(avg_win) * 1000, 4),
        "avg_bid_usd": round(float(avg_bid), 6),
        "today_wins": today_wins,
        "today_spend_usd": round(float(today_spend), 4),
    }


# ─────────────────────────────────────────────────────
# 6. RTB HOURLY (chart)
# ─────────────────────────────────────────────────────
@router.get("/rtb/hourly")
def get_rtb_hourly(
    hours: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Hourly win rate + CPM for the RTB performance chart."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    trunc = sqlfunc.date_trunc("hour", AuctionLog.created_at)

    rows = db.query(
        trunc.label("hour"),
        sqlfunc.count(AuctionLog.id).label("auctions"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, 1), else_=0)
        ), 0).label("wins"),
        sqlfunc.coalesce(sqlfunc.avg(
            sqlfunc.case((AuctionLog.won == True, AuctionLog.win_price), else_=None)
        ), 0).label("avg_cpm_raw"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, AuctionLog.win_price), else_=0)
        ), 0).label("spend"),
    ).filter(
        AuctionLog.created_at >= since
    ).group_by(trunc).order_by(trunc).all()

    return {
        "hours": hours,
        "series": [
            {
                "hour": r.hour.isoformat() if r.hour else None,
                "auctions": int(r.auctions or 0),
                "wins": int(r.wins or 0),
                "win_rate_pct": round(
                    float(r.wins) / float(r.auctions) * 100 if r.auctions else 0, 2
                ),
                "cpm": round(float(r.avg_cpm_raw or 0) * 1000, 4),
                "spend_usd": round(float(r.spend or 0), 6),
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────
# 7. RTB TOP SITES
# ─────────────────────────────────────────────────────
@router.get("/rtb/top-sites")
def get_rtb_top_sites(
    days: int = Query(1, ge=1, le=30),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Top sites by spend + win rate. Used for site blacklist decisions."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    rows = db.query(
        AuctionLog.site_domain,
        sqlfunc.count(AuctionLog.id).label("auctions"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, 1), else_=0)
        ), 0).label("wins"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, AuctionLog.win_price), else_=0)
        ), 0).label("spend"),
    ).filter(
        AuctionLog.created_at >= since,
        AuctionLog.site_domain != None,  # noqa
    ).group_by(AuctionLog.site_domain).order_by(
        sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, AuctionLog.win_price), else_=0)
        ).desc()
    ).limit(limit).all()

    return {
        "days": days,
        "sites": [
            {
                "domain": r.site_domain,
                "auctions": int(r.auctions or 0),
                "wins": int(r.wins or 0),
                "win_rate_pct": round(
                    float(r.wins) / float(r.auctions) * 100 if r.auctions else 0, 2
                ),
                "spend_usd": round(float(r.spend or 0), 4),
            }
            for r in rows
        ],
    }


# ─────────────────────────────────────────────────────
# 8. RTB PER-CAMPAIGN
# ─────────────────────────────────────────────────────
@router.get("/rtb/{campaign_id}")
def get_rtb_campaign(
    campaign_id: int,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.query(
        sqlfunc.count(AuctionLog.id).label("total"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, 1), else_=0)
        ), 0).label("wins"),
        sqlfunc.coalesce(sqlfunc.sum(
            sqlfunc.case((AuctionLog.won == True, AuctionLog.win_price), else_=0)
        ), 0).label("spend"),
        sqlfunc.coalesce(sqlfunc.avg(
            sqlfunc.case((AuctionLog.won == True, AuctionLog.win_price), else_=None)
        ), 0).label("avg_cpm_raw"),
        sqlfunc.coalesce(sqlfunc.avg(AuctionLog.bid_price), 0).label("avg_bid"),
    ).filter(
        AuctionLog.campaign_id == campaign_id,
        AuctionLog.created_at >= since,
    ).first()

    win_rate = (float(rows.wins) / float(rows.total) * 100) if rows.total else 0
    return {
        "campaign_id": campaign_id,
        "days": days,
        "total_auctions": int(rows.total or 0),
        "wins": int(rows.wins or 0),
        "win_rate_pct": round(win_rate, 2),
        "spend_usd": round(float(rows.spend or 0), 4),
        "avg_cpm_usd": round(float(rows.avg_cpm_raw or 0) * 1000, 4),
        "avg_bid_usd": round(float(rows.avg_bid or 0), 6),
    }
