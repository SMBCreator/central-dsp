from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ─────────────────────────────────────────
# Schemas for CentralBidder ↔ CentralDSP communication
# ─────────────────────────────────────────

class CentralCreative(BaseModel):
    """Creative format expected by CentralBidder"""
    id: str
    campaign_id: str
    format: str                        # banner | native | video | push
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: str
    adm: Optional[str] = None         # raw ad markup
    price_floor: float = 0.01
    max_bid: float = 5.00


class CentralTargeting(BaseModel):
    """Targeting rules for CentralBidder campaign"""
    geo_countries: List[str] = []
    device_types: List[str] = []      # mobile | desktop | tablet | ctv
    os_list: List[str] = []
    browser_list: List[str] = []
    site_whitelist: List[str] = []
    site_blacklist: List[str] = []
    hour_schedule: Dict[str, List[int]] = {}  # {"1": [9,10,11,...18], ...}
    bid_floor: float = 0.01
    bid_ceiling: float = 5.00


class CentralCampaign(BaseModel):
    """Campaign format served to CentralBidder"""
    id: str
    name: str
    status: str                        # active | stopped | paused
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None
    targeting: CentralTargeting
    creatives: List[CentralCreative]
    win_notice_url: str
    loss_notice_url: str


class CentralCampaignFeed(BaseModel):
    """Response to CentralBidder campaign polling"""
    campaigns: List[CentralCampaign]
    generated_at: datetime
    version: str = "1.0"


# ─────────────────────────────────────────
# Win / Loss Notice payloads from CentralBidder
# ─────────────────────────────────────────

class WinNotice(BaseModel):
    bid_request_id: str
    imp_id: str
    campaign_id: str
    creative_id: str
    bid_price: float
    win_price: float
    clearing_price: float
    ssp_name: Optional[str] = None
    site_domain: Optional[str] = None
    app_bundle: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


class LossNotice(BaseModel):
    bid_request_id: str
    imp_id: str
    campaign_id: str
    creative_id: str
    bid_price: float
    loss_reason: Optional[int] = None   # OpenRTB loss reason code
    ssp_name: Optional[str] = None
    site_domain: Optional[str] = None
    geo_country: Optional[str] = None
    device_type: Optional[str] = None


# ─────────────────────────────────────────
# RTB Stats response (for UI dashboard)
# ─────────────────────────────────────────

class RTBStats(BaseModel):
    auctions_total: int = 0
    auctions_won: int = 0
    win_rate: float = 0.0              # %
    impressions: int = 0
    avg_win_price: float = 0.0
    avg_bid_price: float = 0.0
    spend_today: float = 0.0
    cpm: float = 0.0
