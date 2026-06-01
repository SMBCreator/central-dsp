from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from database import SessionLocal
from auth import get_current_user
from models import CampaignStat, AuctionLog, Campaign, User, Click, Event

router = APIRouter(prefix="/api/stats", tags=["Stats"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Schemas ───────────────────────────────────────────────────────────────────

class HourlyStatOut(BaseModel):
    hour_bucket: datetime
    impressions: int
    clicks: int
    conversions: int
    spend: float
    revenue: float
    ctr: float
    cpa: float
    roi: float

    class Config:
        from_attributes = True


class SummaryOut(BaseModel):
    impressions: int
    clicks: int
    conversions: int
    spend: float
    revenue: float
    ctr: float
    cpa: float
    roi: float
    win_rate: float


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_div(a, b, default=0.0):
    return float(a) / float(b) if b else default


def _owned_campaign_ids(db: Session, user: User) -> List[int]:
    rows = db.execute(
        select(Campaign.id).where(Campaign.user_id == user.id)
    ).scalars().all()
    return list(rows)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/campaign/{campaign_id}/hourly", response_model=List[HourlyStatOut])
def get_hourly_stats(
    campaign_id: int,
    hours: int = Query(default=24, ge=1, le=720),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ids = _owned_campaign_ids(db, current_user)
    if campaign_id not in ids:
        return []
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(CampaignStat)
        .where(CampaignStat.campaign_id == campaign_id, CampaignStat.hour_bucket >= since)
        .order_by(CampaignStat.hour_bucket)
    ).scalars().all()
    result = []
    for r in rows:
        spend = r.spend or 0
        revenue = r.revenue or 0
        convs = r.conversions or 0
        imps = r.impressions or 0
        clicks = r.clicks or 0
        result.append(HourlyStatOut(
            hour_bucket=r.hour_bucket,
            impressions=imps, clicks=clicks, conversions=convs,
            spend=float(spend), revenue=float(revenue),
            ctr=_safe_div(clicks, imps) * 100,
            cpa=_safe_div(spend, convs),
            roi=_safe_div(revenue - spend, spend) * 100 if spend else 0.0,
        ))
    return result


@router.get("/campaign/{campaign_id}/summary", response_model=SummaryOut)
def get_campaign_summary(
    campaign_id: int,
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ids = _owned_campaign_ids(db, current_user)
    if campaign_id not in ids:
        return SummaryOut(impressions=0,clicks=0,conversions=0,spend=0,revenue=0,ctr=0,cpa=0,roi=0,win_rate=0)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    agg = db.execute(select(
        func.coalesce(func.sum(CampaignStat.impressions), 0),
        func.coalesce(func.sum(CampaignStat.clicks), 0),
        func.coalesce(func.sum(CampaignStat.conversions), 0),
        func.coalesce(func.sum(CampaignStat.spend), 0),
        func.coalesce(func.sum(CampaignStat.revenue), 0),
    ).where(CampaignStat.campaign_id == campaign_id, CampaignStat.hour_bucket >= since)).one()

    imps, clicks, convs, spend, revenue = agg
    spend = float(spend); revenue = float(revenue)

    total_auctions = db.execute(select(func.count(AuctionLog.id)).where(
        AuctionLog.campaign_id == campaign_id, AuctionLog.created_at >= since)).scalar() or 0
    won_auctions = db.execute(select(func.count(AuctionLog.id)).where(
        AuctionLog.campaign_id == campaign_id, AuctionLog.won.is_(True),
        AuctionLog.created_at >= since)).scalar() or 0

    return SummaryOut(
        impressions=imps, clicks=clicks, conversions=convs,
        spend=spend, revenue=revenue,
        ctr=_safe_div(clicks, imps) * 100,
        cpa=_safe_div(spend, convs),
        roi=_safe_div(revenue - spend, spend) * 100 if spend else 0.0,
        win_rate=_safe_div(won_auctions, total_auctions) * 100,
    )


@router.get("/overview", response_model=SummaryOut)
def get_overview(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregated stats across ALL campaigns for current user."""
    ids = _owned_campaign_ids(db, current_user)
    if not ids:
        return SummaryOut(impressions=0,clicks=0,conversions=0,spend=0,revenue=0,ctr=0,cpa=0,roi=0,win_rate=0)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    agg = db.execute(select(
        func.coalesce(func.sum(CampaignStat.impressions), 0),
        func.coalesce(func.sum(CampaignStat.clicks), 0),
        func.coalesce(func.sum(CampaignStat.conversions), 0),
        func.coalesce(func.sum(CampaignStat.spend), 0),
        func.coalesce(func.sum(CampaignStat.revenue), 0),
    ).where(CampaignStat.campaign_id.in_(ids), CampaignStat.hour_bucket >= since)).one()

    imps, clicks, convs, spend, revenue = agg
    spend = float(spend); revenue = float(revenue)

    total_auctions = db.execute(select(func.count(AuctionLog.id)).where(
        AuctionLog.campaign_id.in_(ids), AuctionLog.created_at >= since)).scalar() or 0
    won_auctions = db.execute(select(func.count(AuctionLog.id)).where(
        AuctionLog.campaign_id.in_(ids), AuctionLog.won.is_(True),
        AuctionLog.created_at >= since)).scalar() or 0

    return SummaryOut(
        impressions=imps, clicks=clicks, conversions=convs,
        spend=spend, revenue=revenue,
        ctr=_safe_div(clicks, imps) * 100,
        cpa=_safe_div(spend, convs),
        roi=_safe_div(revenue - spend, spend) * 100 if spend else 0.0,
        win_rate=_safe_div(won_auctions, total_auctions) * 100,
    )
