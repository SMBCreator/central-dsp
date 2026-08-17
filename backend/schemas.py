"""Pydantic request/response schemas for the management API."""
from pydantic import BaseModel, EmailStr, Field


# --- Auth -------------------------------------------------------------------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


# --- Advertisers ------------------------------------------------------------
class AdvertiserIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = None
    category: str | None = None
    balance: float = Field(default=0, ge=0)
    currency: str = Field(default="USD", pattern="^(USD|EUR|RUB|KZT|UZS)$")


class AdvertiserPatch(BaseModel):
    name: str | None = None
    domain: str | None = None
    category: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    balance: float | None = Field(default=None, ge=0)


# --- Campaigns ---------------------------------------------------------------
class CampaignIn(BaseModel):
    advertiser_id: str
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="awareness", pattern="^(awareness|traffic|conversions)$")
    group_id: str | None = None
    daily_budget: float = Field(default=0, ge=0)
    total_budget: float = Field(default=0, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    pacing: str = Field(default="even", pattern="^(even|asap)$")
    frequency_cap_impressions: int | None = Field(default=None, ge=1, le=100)
    frequency_cap_period_hours: int | None = Field(default=None, ge=1, le=168)


class CampaignPatch(BaseModel):
    name: str | None = None
    objective: str | None = Field(default=None, pattern="^(awareness|traffic|conversions)$")
    status: str | None = Field(default=None, pattern="^(draft|active|paused|completed|archived)$")
    daily_budget: float | None = Field(default=None, ge=0)
    total_budget: float | None = Field(default=None, ge=0)
    start_date: str | None = None
    end_date: str | None = None
    pacing: str | None = Field(default=None, pattern="^(even|asap)$")
    frequency_cap_impressions: int | None = Field(default=None, ge=1, le=100)
    frequency_cap_period_hours: int | None = Field(default=None, ge=1, le=168)
    # Optimization: pixel-driven buying
    pixel_id: str | None = None
    goal_id: str | None = None
    target_cpa: float | None = Field(default=None, ge=0)
    target_cpc: float | None = Field(default=None, ge=0)
    target_cpm: float | None = Field(default=None, ge=0)
    target_cpv: float | None = Field(default=None, ge=0)
    auto_optimize: bool | None = None
    group_id: str | None = None


# --- Line items ---------------------------------------------------------------
class LineItemIn(BaseModel):
    campaign_id: str
    name: str = Field(min_length=1, max_length=200)
    bid_type: str = Field(default="cpm", pattern="^(cpm|cpc)$")
    bid_price: float = Field(ge=0.01, le=1000)
    max_bid: float | None = Field(default=None, ge=0.01, le=1000)
    daily_budget: float = Field(default=0, ge=0)
    targeting: dict = Field(default_factory=dict)


class LineItemPatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    bid_type: str | None = Field(default=None, pattern="^(cpm|cpc)$")
    bid_price: float | None = Field(default=None, ge=0.01, le=1000)
    max_bid: float | None = Field(default=None, ge=0.01, le=1000)
    daily_budget: float | None = Field(default=None, ge=0)
    targeting: dict | None = None


# --- Creatives -----------------------------------------------------------------
class CreativeIn(BaseModel):
    campaign_id: str
    line_item_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    type: str = Field(default="banner", pattern="^(banner|native|video|push)$")
    # banner: static|gif|html5 ; video: instream|outstream ; push/native: null
    subtype: str | None = Field(default=None, pattern="^(static|gif|html5|instream|outstream)$")
    width: int | None = Field(default=None, ge=1, le=4096)
    height: int | None = Field(default=None, ge=1, le=4096)
    asset_url: str | None = None
    click_url: str | None = None
    html_snippet: str | None = None
    native_title: str | None = Field(default=None, max_length=90)
    native_description: str | None = Field(default=None, max_length=200)
    native_cta: str | None = Field(default=None, max_length=30)
    native_icon_url: str | None = None
    video_url: str | None = None
    video_duration: int | None = Field(default=None, ge=1, le=600)
    vast_xml: str | None = None
    crop: dict | None = None


class CreativePatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(pending|approved|rejected|archived)$")
    asset_url: str | None = None
    click_url: str | None = None
    html_snippet: str | None = None
    native_title: str | None = None
    native_description: str | None = None
    native_cta: str | None = None
    native_icon_url: str | None = None
    video_url: str | None = None
    video_duration: int | None = None
    vast_xml: str | None = None


# --- Supply partners --------------------------------------------------------------
class SupplyPartnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    auction_type: int = Field(default=2, ge=1, le=2)
    bid_floor: float = Field(default=0, ge=0)
    qps_limit: int = Field(default=100, ge=1, le=100000)
    allowed_ad_types: list[str] = Field(default_factory=lambda: ["banner", "native", "video", "push"])
    notes: str | None = None


class SupplyPartnerPatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    auction_type: int | None = Field(default=None, ge=1, le=2)
    bid_floor: float | None = Field(default=None, ge=0)
    qps_limit: int | None = Field(default=None, ge=1, le=100000)
    allowed_ad_types: list[str] | None = None
    notes: str | None = None


# --- Rules ---------------------------------------------------------------------
class RuleIn(BaseModel):
    campaign_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    metric: str = Field(pattern="^(ctr|cpm|cpc|spend|impressions|clicks|win_rate)$")
    operator: str = Field(pattern="^(gt|lt|gte|lte)$")
    threshold: float
    action: str = Field(pattern="^(pause_campaign|pause_line_item|decrease_bid|increase_bid|notify)$")
    action_value: float | None = None
    window_hours: int = Field(default=24, ge=1, le=168)


class RulePatch(BaseModel):
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|archived)$")
    metric: str | None = Field(default=None, pattern="^(ctr|cpm|cpc|spend|impressions|clicks|win_rate)$")
    operator: str | None = Field(default=None, pattern="^(gt|lt|gte|lte)$")
    threshold: float | None = None
    action: str | None = Field(default=None, pattern="^(pause_campaign|pause_line_item|decrease_bid|increase_bid|notify)$")
    action_value: float | None = None
    window_hours: int | None = Field(default=None, ge=1, le=168)
