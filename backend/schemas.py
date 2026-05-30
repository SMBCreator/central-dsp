"""Pydantic schemas for request/response validation."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, field_validator
import uuid


# ─────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    role: str = "viewer"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in ("admin", "manager", "viewer"):
            raise ValueError("role must be admin, manager or viewer")
        return v


class UserOut(BaseModel):
    id: int
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────
# CAMPAIGNS
# ─────────────────────────────────────────────────────────
class CampaignCreate(BaseModel):
    name: str
    is_ab: bool = False
    ab_limit: Optional[int] = None
    offer_value: float = 0.0
    attribution_model: str = "last_click"
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None


class CampaignOut(BaseModel):
    id: int
    name: str
    status: str
    camp_code: str
    is_ab: bool
    offer_value: float
    attribution_model: str
    daily_budget: Optional[float]
    total_budget: Optional[float]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    offer_value: Optional[float] = None
    daily_budget: Optional[float] = None
    total_budget: Optional[float] = None


# ─────────────────────────────────────────────────────────
# LINKS
# ─────────────────────────────────────────────────────────
class LinkCreate(BaseModel):
    original_url: str
    platform: Optional[str] = None
    external_id: Optional[str] = None
    custom_slug: Optional[str] = None
    cost_per_click: float = 0.0
    expires_in_days: Optional[int] = None
    geo_rules: Dict[str, Any] = {}
    device_rules: Dict[str, Any] = {}


class LinkOut(BaseModel):
    id: int
    campaign_id: int
    short_code: str
    custom_slug: Optional[str]
    original_url: str
    platform: Optional[str]
    is_active: bool
    cost_per_click: float
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────
# PIXELS
# ─────────────────────────────────────────────────────────
class PixelCreate(BaseModel):
    campaign_id: int
    name: str


class PixelOut(BaseModel):
    id: int
    campaign_id: int
    name: str
    pixel_id: uuid.UUID
    is_active: bool
    last_fire_at: Optional[datetime]
    total_events: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────
# EVENTS (pixel tracking)
# ─────────────────────────────────────────────────────────
class EventCreate(BaseModel):
    event_name: str = "pageview"
    sub_id: str = "unknown"
    creative_id: str = "unknown"
    click_id: Optional[str] = None
    session_id: Optional[str] = None
    value: float = 0.0
    currency: str = "USD"
    metadata: Dict[str, Any] = {}
    referer: Optional[str] = None


# ─────────────────────────────────────────────────────────
# RULES (AutoPilot)
# ─────────────────────────────────────────────────────────
class RuleCreate(BaseModel):
    level: str = "campaign"  # campaign|sub_id|bid
    metric: str
    condition: str  # <|>|<=|>=|==
    threshold: float
    action: str


class RuleOut(BaseModel):
    id: int
    campaign_id: int
    level: str
    metric: str
    condition: str
    threshold: float
    action: str
    triggered: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────
# CREATIVES
# ─────────────────────────────────────────────────────────
class CreativeCreate(BaseModel):
    name: Optional[str] = None
    format: str  # banner|native|video|push
    width: Optional[int] = None
    height: Optional[int] = None
    image_url: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    click_url: Optional[str] = None
    adm: Optional[str] = None


class CreativeOut(BaseModel):
    id: int
    campaign_id: int
    name: Optional[str]
    format: str
    width: Optional[int]
    height: Optional[int]
    image_url: Optional[str]
    title: Optional[str]
    click_url: Optional[str]
    status: str
    audit_status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ─────────────────────────────────────────────────────────
# TARGETING
# ─────────────────────────────────────────────────────────
class TargetingUpdate(BaseModel):
    bid_floor: float = 0.01
    bid_ceiling: float = 5.00
    geo_countries: List[str] = []
    device_types: List[str] = []
    os_list: List[str] = []
    browser_list: List[str] = []
    site_whitelist: List[str] = []
    site_blacklist: List[str] = []
    hour_schedule: Dict[str, List[int]] = {}


# ─────────────────────────────────────────────────────────
# ANALYTICS
# ─────────────────────────────────────────────────────────
class AnalyticsSummary(BaseModel):
    clicks: int
    conversions: int
    cr: float           # conversion rate %
    cost: float
    revenue: float
    roi: float          # %
    epc: float          # earnings per click
    cpa: float          # cost per action
    win_rate: float     # RTB win rate %
    impressions: int    # RTB impressions
    spend: float        # total spend (cost)


# ─────────────────────────────────────────────────────────
# RTB BID REQUEST / RESPONSE (OpenRTB 2.5 simplified)
# ─────────────────────────────────────────────────────────
class BidRequestImp(BaseModel):
    id: str
    bidfloor: float = 0.0
    bidfloorcur: str = "USD"
    banner: Optional[Dict] = None
    native: Optional[Dict] = None
    video: Optional[Dict] = None


class BidRequestSite(BaseModel):
    domain: Optional[str] = None
    page: Optional[str] = None
    publisher: Optional[Dict] = None


class BidRequestDevice(BaseModel):
    ua: Optional[str] = None
    ip: Optional[str] = None
    geo: Optional[Dict] = None
    os: Optional[str] = None
    devicetype: Optional[int] = None


class BidRequest(BaseModel):
    id: str
    imp: List[BidRequestImp] = []
    site: Optional[BidRequestSite] = None
    app: Optional[Dict] = None
    device: Optional[BidRequestDevice] = None
    user: Optional[Dict] = None
    tmax: int = 150
