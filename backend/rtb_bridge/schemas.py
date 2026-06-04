"""
RTB Bridge Pydantic schemas.

Используются:
- router.py  — FastAPI request/response validation
- service.py — типизация внутренней логики
"""
from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────
# Targeting
# ──────────────────────────────────────────────────────────────────

class TargetingOut(BaseModel):
    bid_floor: float = 0.0
    bid_ceiling: float = 10.0
    geo_countries: List[str] = []
    device_types: List[str] = []
    os_list: List[str] = []
    browser_list: List[str] = []
    site_whitelist: List[str] = []
    site_blacklist: List[str] = []
    hour_schedule: Dict[str, Any] = {}

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────────────────────────
# Creatives
# ──────────────────────────────────────────────────────────────────

class CreativeOut(BaseModel):
    id: int
    format: str
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    click_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    sponsored_by: Optional[str] = None
    vast_url: Optional[str] = None
    video_url: Optional[str] = None
    duration_sec: Optional[int] = None
    adm: Optional[str] = None
    adomain: Optional[str] = None
    status: Optional[str] = None
    audit_status: Optional[str] = None

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────────────────────────
# Campaigns (для биддера)
# ──────────────────────────────────────────────────────────────────

class RTBCampaignOut(BaseModel):
    id: int
    name: str
    status: str
    bid_price: Optional[float] = None
    bid_floor: Optional[float] = None
    bid_ceiling: Optional[float] = None
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    budget_remaining: Optional[float] = None
    click_url: Optional[str] = None
    tracking_pixel: Optional[str] = None
    freq_cap: Optional[int] = None
    targeting: Optional[TargetingOut] = None
    creatives: List[CreativeOut] = []

    class Config:
        from_attributes = True


# ──────────────────────────────────────────────────────────────────
# Win / Loss Notices
# ──────────────────────────────────────────────────────────────────

class WinNotice(BaseModel):
    auction_id: str
    imp_id: str = ""               # optional — не все SSP передают imp_id в NURL
    campaign_id: int
    creative_id: int
    win_price: float                # clearing price in CPM USD
    bid_price: Optional[float] = None   # наша ставка (если известна)
    clearing_price: Optional[float] = None  # = win_price для first-price auction
    site_domain: Optional[str] = None
    app_bundle: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


class LossNotice(BaseModel):
    auction_id: str
    imp_id: str = ""
    campaign_id: int
    creative_id: Optional[int] = None
    bid_price: Optional[float] = None   # наша ставка — сохраняем для статистики
    loss_reason: Optional[int] = None   # OpenRTB Loss Reason Code


# ──────────────────────────────────────────────────────────────────
# Bidder Status
# ──────────────────────────────────────────────────────────────────

class BidderStatusOut(BaseModel):
    bidder_alive: bool
    endpoint: str
    win_rate_pct: float
    auctions_today: int
    wins_today: int
    spend_today: float


# ──────────────────────────────────────────────────────────────────
# RTB Stats
# ──────────────────────────────────────────────────────────────────

class RTBStatsOut(BaseModel):
    campaign_id: int
    auctions: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_bid_price: float    # среднее значение нашей ставки
    avg_win_price: float    # среднее clearing price
    total_spend: float
    avg_cpm: float
