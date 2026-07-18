"""Pydantic v2 schemas for Central DSP API.

Naming convention:
  <Model>Base     — shared fields (no id, no timestamps)
  <Model>Create   — request body for POST (inherits Base, adds required fields)
  <Model>Update   — request body for PATCH (all fields Optional)
  <Model>Out      — response body (inherits Base, adds id + timestamps)
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ═══════════════════════════════════════════════════════════════════════════
# SHARED PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════

class OrmBase(BaseModel):
    """Root base: enables ORM mode for all schemas."""
    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════════
# USER / AUTH
# ═══════════════════════════════════════════════════════════════════════════

class UserBase(OrmBase):
    email: EmailStr
    full_name: Optional[str] = None
    role: str = Field(default="viewer", pattern="^(admin|manager|viewer)$")
    is_active: bool = True


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserUpdate(OrmBase):
    full_name: Optional[str] = None
    role: Optional[str] = Field(default=None, pattern="^(admin|manager|viewer)$")
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8)


class UserOut(UserBase):
    id: int
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[int] = None
    role: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# ADVERTISER
# ═══════════════════════════════════════════════════════════════════════════

class AdvertiserBase(OrmBase):
    name: str = Field(max_length=255)
    domain: Optional[str] = Field(default=None, max_length=255)
    iab_categories: List[str] = Field(default_factory=list)
    status: str = Field(default="active", pattern="^(active|paused|blocked)$")
    billing_currency: str = Field(default="USD", max_length=8)
    total_budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None


class AdvertiserCreate(AdvertiserBase):
    pass


class AdvertiserUpdate(OrmBase):
    name: Optional[str] = Field(default=None, max_length=255)
    domain: Optional[str] = Field(default=None, max_length=255)
    iab_categories: Optional[List[str]] = None
    status: Optional[str] = Field(default=None, pattern="^(active|paused|blocked)$")
    billing_currency: Optional[str] = Field(default=None, max_length=8)
    total_budget_limit: Optional[Decimal] = Field(default=None, ge=0)
    notes: Optional[str] = None


class AdvertiserOut(AdvertiserBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# CAMPAIGN
# ═══════════════════════════════════════════════════════════════════════════

class CampaignBase(OrmBase):
    name: str = Field(max_length=255)
    status: str = Field(default="active",
                        pattern="^(active|stopped|paused|archived)$")
    is_ab: bool = False
    ab_limit: Optional[int] = Field(default=None, ge=1)
    offer_value: Decimal = Field(default=Decimal("0"), ge=0)
    attribution_model: str = Field(default="last_click",
                                   pattern="^(last_click|first_click|linear)$")
    daily_budget: Optional[Decimal] = Field(default=None, ge=0)
    total_budget: Optional[Decimal] = Field(default=None, ge=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    advertiser_id: Optional[int] = None

    @model_validator(mode="after")
    def end_after_start(self) -> "CampaignBase":
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValueError("end_date must be after start_date")
        return self


class CampaignCreate(CampaignBase):
    pass


class CampaignUpdate(OrmBase):
    name: Optional[str] = Field(default=None, max_length=255)
    status: Optional[str] = Field(default=None,
                                  pattern="^(active|stopped|paused|archived)$")
    daily_budget: Optional[Decimal] = Field(default=None, ge=0)
    total_budget: Optional[Decimal] = Field(default=None, ge=0)
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    advertiser_id: Optional[int] = None
    offer_value: Optional[Decimal] = Field(default=None, ge=0)
    attribution_model: Optional[str] = Field(
        default=None, pattern="^(last_click|first_click|linear)$")


class CampaignOut(CampaignBase):
    id: int
    user_id: int
    camp_code: str
    daily_spent: Decimal
    total_spent: Decimal
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# LINE ITEM
# ═══════════════════════════════════════════════════════════════════════════

class LineItemBase(OrmBase):
    name: str = Field(max_length=255)
    status: str = Field(default="active", pattern="^(active|paused|archived)$")

    # Bidding
    bid_strategy: str = Field(default="fixed",
                               pattern="^(fixed|cpm|cpc|target_cpa)$")
    bid_price: Decimal = Field(default=Decimal("1.0"), ge=0)
    max_bid: Optional[Decimal] = Field(default=None, ge=0)
    min_bid: Optional[Decimal] = Field(default=None, ge=0)

    # Budget / Pacing
    daily_budget: Optional[Decimal] = Field(default=None, ge=0)
    total_budget: Optional[Decimal] = Field(default=None, ge=0)
    pacing: str = Field(default="even", pattern="^(even|asap|frontload)$")

    # Schedule
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    hour_schedule: dict = Field(default_factory=dict)

    # Targeting
    geo_countries: List[str] = Field(default_factory=list)
    device_types: List[str] = Field(default_factory=list)
    os_list: List[str] = Field(default_factory=list)
    browser_list: List[str] = Field(default_factory=list)
    site_whitelist: List[str] = Field(default_factory=list)
    site_blacklist: List[str] = Field(default_factory=list)
    app_whitelist: List[str] = Field(default_factory=list)
    app_blacklist: List[str] = Field(default_factory=list)
    iab_categories: List[str] = Field(default_factory=list)

    # Frequency Cap
    freq_cap_impressions: Optional[int] = Field(default=None, ge=1)
    freq_cap_window_hours: int = Field(default=24, ge=1, le=168)

    # Creative rotation
    creative_rotation: str = Field(default="even",
                                   pattern="^(even|weighted|sequential)$")

    @model_validator(mode="after")
    def end_after_start(self) -> "LineItemBase":
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValueError("end_date must be after start_date")
        return self

    @field_validator("max_bid", mode="after")
    @classmethod
    def max_above_min(cls, v: Optional[Decimal], info) -> Optional[Decimal]:
        """max_bid must be >= min_bid when both are set."""
        # info.data available in pydantic v2 via ValidationInfo
        min_bid = info.data.get("min_bid")
        if v is not None and min_bid is not None and v < min_bid:
            raise ValueError("max_bid must be >= min_bid")
        return v


class LineItemCreate(LineItemBase):
    campaign_id: int


class LineItemUpdate(OrmBase):
    name: Optional[str] = Field(default=None, max_length=255)
    status: Optional[str] = Field(default=None, pattern="^(active|paused|archived)$")
    bid_strategy: Optional[str] = Field(default=None,
                                         pattern="^(fixed|cpm|cpc|target_cpa)$")
    bid_price: Optional[Decimal] = Field(default=None, ge=0)
    max_bid: Optional[Decimal] = Field(default=None, ge=0)
    min_bid: Optional[Decimal] = Field(default=None, ge=0)
    daily_budget: Optional[Decimal] = Field(default=None, ge=0)
    total_budget: Optional[Decimal] = Field(default=None, ge=0)
    pacing: Optional[str] = Field(default=None, pattern="^(even|asap|frontload)$")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    hour_schedule: Optional[dict] = None
    geo_countries: Optional[List[str]] = None
    device_types: Optional[List[str]] = None
    os_list: Optional[List[str]] = None
    browser_list: Optional[List[str]] = None
    site_whitelist: Optional[List[str]] = None
    site_blacklist: Optional[List[str]] = None
    app_whitelist: Optional[List[str]] = None
    app_blacklist: Optional[List[str]] = None
    iab_categories: Optional[List[str]] = None
    freq_cap_impressions: Optional[int] = Field(default=None, ge=1)
    freq_cap_window_hours: Optional[int] = Field(default=None, ge=1, le=168)
    creative_rotation: Optional[str] = Field(default=None,
                                              pattern="^(even|weighted|sequential)$")


class LineItemOut(LineItemBase):
    id: int
    campaign_id: int
    daily_spent: Decimal
    total_spent: Decimal
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# SUPPLY PARTNER  (SSP)
# ═══════════════════════════════════════════════════════════════════════════

class SupplyPartnerBase(OrmBase):
    name: str = Field(max_length=100)
    endpoint_url: Optional[str] = None
    protocol: str = Field(default="openrtb_2_5",
                          pattern="^(openrtb_2_5|openrtb_3_0|custom)$")
    qps_limit: int = Field(default=1000, ge=1, le=100_000)
    bid_timeout_ms: int = Field(default=150, ge=50, le=1000)
    status: str = Field(default="active", pattern="^(active|paused|testing)$")
    seat_id: Optional[str] = Field(default=None, max_length=128)
    publisher_id: Optional[str] = Field(default=None, max_length=128)
    allowed_formats: List[str] = Field(default_factory=list)
    revenue_share: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    notes: Optional[str] = None


class SupplyPartnerCreate(SupplyPartnerBase):
    pass


class SupplyPartnerUpdate(OrmBase):
    name: Optional[str] = Field(default=None, max_length=100)
    endpoint_url: Optional[str] = None
    protocol: Optional[str] = Field(default=None,
                                    pattern="^(openrtb_2_5|openrtb_3_0|custom)$")
    qps_limit: Optional[int] = Field(default=None, ge=1, le=100_000)
    bid_timeout_ms: Optional[int] = Field(default=None, ge=50, le=1000)
    status: Optional[str] = Field(default=None, pattern="^(active|paused|testing)$")
    seat_id: Optional[str] = Field(default=None, max_length=128)
    publisher_id: Optional[str] = Field(default=None, max_length=128)
    allowed_formats: Optional[List[str]] = None
    revenue_share: Optional[Decimal] = Field(default=None, ge=0, le=1)
    notes: Optional[str] = None


class SupplyPartnerOut(SupplyPartnerBase):
    id: int
    incoming_token: str   # shown to admin only — route SSP traffic to /rtb/{token}
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# CREATIVE
# ═══════════════════════════════════════════════════════════════════════════

class CreativeBase(OrmBase):
    name: Optional[str] = Field(default=None, max_length=255)
    format: str = Field(pattern="^(banner|native|video|push)$")
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)
    image_url: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    click_url: Optional[str] = None
    adm: Optional[str] = None
    impression_tracker_url: Optional[str] = None
    click_tracker_url: Optional[str] = None
    status: str = Field(default="active", pattern="^(active|paused|archived)$")
    weight: int = Field(default=1, ge=1, le=100)
    line_item_id: Optional[int] = None


class CreativeCreate(CreativeBase):
    campaign_id: int


class CreativeUpdate(OrmBase):
    name: Optional[str] = Field(default=None, max_length=255)
    image_url: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    click_url: Optional[str] = None
    adm: Optional[str] = None
    impression_tracker_url: Optional[str] = None
    click_tracker_url: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(active|paused|archived)$")
    weight: Optional[int] = Field(default=None, ge=1, le=100)
    line_item_id: Optional[int] = None


class CreativeOut(CreativeBase):
    id: int
    campaign_id: int
    audit_status: str
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# TARGETING RULE  (campaign-level defaults)
# ═══════════════════════════════════════════════════════════════════════════

class TargetingRuleBase(OrmBase):
    bid_floor: Decimal = Field(default=Decimal("0.01"), ge=0)
    bid_ceiling: Decimal = Field(default=Decimal("5.00"), ge=0)
    geo_countries: List[str] = Field(default_factory=list)
    device_types: List[str] = Field(default_factory=list)
    os_list: List[str] = Field(default_factory=list)
    browser_list: List[str] = Field(default_factory=list)
    site_whitelist: List[str] = Field(default_factory=list)
    site_blacklist: List[str] = Field(default_factory=list)
    hour_schedule: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def floor_below_ceiling(self) -> "TargetingRuleBase":
        if self.bid_floor >= self.bid_ceiling:
            raise ValueError("bid_floor must be less than bid_ceiling")
        return self


class TargetingRuleCreate(TargetingRuleBase):
    campaign_id: int


class TargetingRuleUpdate(OrmBase):
    bid_floor: Optional[Decimal] = Field(default=None, ge=0)
    bid_ceiling: Optional[Decimal] = Field(default=None, ge=0)
    geo_countries: Optional[List[str]] = None
    device_types: Optional[List[str]] = None
    os_list: Optional[List[str]] = None
    browser_list: Optional[List[str]] = None
    site_whitelist: Optional[List[str]] = None
    site_blacklist: Optional[List[str]] = None
    hour_schedule: Optional[dict] = None


class TargetingRuleOut(TargetingRuleBase):
    id: int
    campaign_id: int
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# BUDGET LOG
# ═══════════════════════════════════════════════════════════════════════════

class BudgetLogOut(OrmBase):
    """Read-only — budget logs are never created via API, only internally."""
    id: int
    campaign_id: int
    line_item_id: Optional[int]
    auction_log_id: Optional[int]
    event_type: str
    amount: Decimal
    currency: str
    recorded_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# AUCTION LOG
# ═══════════════════════════════════════════════════════════════════════════

class AuctionLogOut(OrmBase):
    id: int
    campaign_id: Optional[int]
    line_item_id: Optional[int]
    creative_id: Optional[int]
    supply_partner_id: Optional[int]
    bid_request_id: Optional[str]
    imp_id: Optional[str]
    bid_price: Optional[Decimal]
    win_price: Optional[Decimal]
    clearing_price: Optional[Decimal]
    won: bool
    ssp_name: Optional[str]
    site_domain: Optional[str]
    app_bundle: Optional[str]
    geo_country: Optional[str]
    device_type: Optional[str]
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# LINK / PIXEL / POSTBACK  (tracking layer — unchanged)
# ═══════════════════════════════════════════════════════════════════════════

class LinkBase(OrmBase):
    original_url: str
    platform: Optional[str] = Field(default=None, max_length=100)
    external_id: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = True
    cost_per_click: Decimal = Field(default=Decimal("0"), ge=0)
    geo_rules: dict = Field(default_factory=dict)
    device_rules: dict = Field(default_factory=dict)


class LinkCreate(LinkBase):
    campaign_id: int
    custom_slug: Optional[str] = Field(default=None, max_length=128)


class LinkOut(LinkBase):
    id: int
    campaign_id: int
    short_code: str
    custom_slug: Optional[str]
    created_at: datetime


class PixelBase(OrmBase):
    name: str = Field(max_length=255)
    is_active: bool = True


class PixelCreate(PixelBase):
    campaign_id: int


class PixelOut(PixelBase):
    id: int
    campaign_id: int
    pixel_id: UUID
    last_fire_at: Optional[datetime]
    total_events: int
    created_at: datetime


class PostbackOut(OrmBase):
    id: int
    click_id: Optional[UUID]
    payout: Decimal
    currency: str
    status: Optional[str]
    processed: bool
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# RULES (Autopilot)
# ═══════════════════════════════════════════════════════════════════════════

class RuleBase(OrmBase):
    level: str = Field(default="campaign", pattern="^(campaign|sub_id|bid)$")
    metric: str = Field(max_length=50)
    condition: str = Field(pattern="^(<|>|<=|>=|==)$")
    threshold: Decimal
    action: str = Field(max_length=50)
    is_active: bool = True


class RuleCreate(RuleBase):
    campaign_id: int


class RuleUpdate(OrmBase):
    threshold: Optional[Decimal] = None
    action: Optional[str] = Field(default=None, max_length=50)
    is_active: Optional[bool] = None


class RuleOut(RuleBase):
    id: int
    campaign_id: int
    triggered: bool
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════════════
# AD ACCOUNT
# ═══════════════════════════════════════════════════════════════════════════

class AdAccountBase(OrmBase):
    platform: str = Field(max_length=50)
    name: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = True
    currency: str = Field(default="USD", max_length=8)


class AdAccountCreate(AdAccountBase):
    credentials: dict = Field(default_factory=dict)


class AdAccountOut(AdAccountBase):
    id: int
    user_id: int
    balance: Optional[Decimal]
    last_sync_at: Optional[datetime]
    created_at: datetime
    # NOTE: credentials intentionally excluded from Out schema (sensitive)


# ═══════════════════════════════════════════════════════════════════════════
# PAGINATED RESPONSES
# ═══════════════════════════════════════════════════════════════════════════

from typing import TypeVar, Generic

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Generic paginated response wrapper."""
    items: List[T]
    total: int
    page: int
    page_size: int
    pages: int
