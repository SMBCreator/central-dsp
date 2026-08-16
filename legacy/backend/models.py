"""SQLAlchemy ORM models for Central DSP."""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Float, Numeric,
    Text, DateTime, ForeignKey, JSON, ARRAY, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, INET, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from backend.database import Base


# ─────────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255))
    role = Column(String(20), nullable=False, default="viewer")  # admin|manager|viewer
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaigns = relationship("Campaign", back_populates="user")
    ad_accounts = relationship("AdAccount", back_populates="user")
    settings = relationship("Setting", back_populates="user")
    advertisers = relationship("Advertiser", back_populates="user")


# ─────────────────────────────────────────────────────────
# ADVERTISERS  (DSP tenant/client entity)
# ─────────────────────────────────────────────────────────
class Advertiser(Base):
    """
    Represents an advertiser (client) in the DSP.
    One User (manager/admin) can manage multiple Advertisers.
    Hierarchy: Advertiser → Campaign → LineItem → Creative
    """
    __tablename__ = "advertisers"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    domain = Column(String(255))                      # advertiser's primary domain
    iab_categories = Column(ARRAY(String), default=list)  # IAB content categories
    status = Column(String(20), nullable=False, default="active")  # active|paused|blocked
    billing_currency = Column(String(8), nullable=False, default="USD")
    total_budget_limit = Column(Numeric(14, 4))       # optional hard cap across all campaigns
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="advertisers")
    campaigns = relationship("Campaign", back_populates="advertiser", cascade="all, delete-orphan")


# ─────────────────────────────────────────────────────────
# CAMPAIGNS
# ─────────────────────────────────────────────────────────
class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # NEW: advertiser_id — nullable for backward compat with existing rows
    advertiser_id = Column(Integer, ForeignKey("advertisers.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active")  # active|stopped|paused|archived
    camp_code = Column(String(32), unique=True, nullable=False, index=True)
    is_ab = Column(Boolean, nullable=False, default=False)
    ab_limit = Column(Integer)
    offer_value = Column(Numeric(10, 4), default=0)
    attribution_model = Column(String(20), nullable=False, default="last_click")  # last_click|first_click|linear
    daily_budget = Column(Numeric(10, 4))
    total_budget = Column(Numeric(10, 4))
    # NEW: spent trackers (updated atomically via BudgetLog)
    daily_spent = Column(Numeric(14, 6), nullable=False, default=0)
    total_spent = Column(Numeric(14, 6), nullable=False, default=0)
    start_date = Column(DateTime(timezone=True))
    end_date = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="campaigns")
    advertiser = relationship("Advertiser", back_populates="campaigns")
    links = relationship("Link", back_populates="campaign", cascade="all, delete-orphan")
    pixels = relationship("Pixel", back_populates="campaign", cascade="all, delete-orphan")
    rules = relationship("Rule", back_populates="campaign", cascade="all, delete-orphan")
    # NOTE: creatives now belong to LineItems, but kept here for backward compat
    creatives = relationship("Creative", back_populates="campaign", cascade="all, delete-orphan")
    targeting = relationship("TargetingRule", back_populates="campaign", uselist=False, cascade="all, delete-orphan")
    line_items = relationship("LineItem", back_populates="campaign", cascade="all, delete-orphan")
    budget_logs = relationship("BudgetLog", back_populates="campaign")


# ─────────────────────────────────────────────────────────
# LINE ITEMS  (flight / ad group level)
# ─────────────────────────────────────────────────────────
class LineItem(Base):
    """
    Line Item = the flight/ad-group layer between Campaign and Creative.
    Controls bid price, pacing, targeting overrides, and creative rotation.
    In RTB: one LineItem maps to one bid candidate evaluated per auction.
    """
    __tablename__ = "line_items"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active")  # active|paused|archived

    # ── Bidding ──────────────────────────────────────────
    bid_strategy = Column(String(20), nullable=False, default="fixed")  # fixed|cpm|cpc|target_cpa
    bid_price = Column(Numeric(10, 6), nullable=False, default=1.00)    # CPM in USD
    max_bid = Column(Numeric(10, 6))                                    # ceiling for dynamic strategies
    min_bid = Column(Numeric(10, 6))                                    # floor override

    # ── Budget / Pacing ──────────────────────────────────
    daily_budget = Column(Numeric(10, 4))
    total_budget = Column(Numeric(10, 4))
    daily_spent = Column(Numeric(14, 6), nullable=False, default=0)
    total_spent = Column(Numeric(14, 6), nullable=False, default=0)
    pacing = Column(String(20), nullable=False, default="even")         # even|asap|frontload

    # ── Schedule ─────────────────────────────────────────
    start_date = Column(DateTime(timezone=True))
    end_date = Column(DateTime(timezone=True))
    hour_schedule = Column(JSONB, default=dict)   # {0: [0,1,...,23], 1: [...]} day→hours

    # ── Targeting (overrides campaign-level) ─────────────
    geo_countries = Column(ARRAY(String), default=list)
    device_types = Column(ARRAY(String), default=list)   # mobile|desktop|tablet|ctv
    os_list = Column(ARRAY(String), default=list)
    browser_list = Column(ARRAY(String), default=list)
    site_whitelist = Column(ARRAY(String), default=list)
    site_blacklist = Column(ARRAY(String), default=list)
    app_whitelist = Column(ARRAY(String), default=list)
    app_blacklist = Column(ARRAY(String), default=list)
    iab_categories = Column(ARRAY(String), default=list)  # IAB allow-list

    # ── Frequency Cap ────────────────────────────────────
    freq_cap_impressions = Column(Integer)               # max imps per user
    freq_cap_window_hours = Column(Integer, default=24)  # rolling window

    # ── Creative rotation ────────────────────────────────
    creative_rotation = Column(String(20), nullable=False, default="even")  # even|weighted|sequential

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    campaign = relationship("Campaign", back_populates="line_items")
    creatives = relationship("Creative", back_populates="line_item", cascade="all, delete-orphan")
    auction_logs = relationship("AuctionLog", back_populates="line_item")

    __table_args__ = (
        Index("idx_lineitems_campaign_status", "campaign_id", "status"),
    )


# ─────────────────────────────────────────────────────────
# SUPPLY PARTNERS  (SSP integrations)
# ─────────────────────────────────────────────────────────
class SupplyPartner(Base):
    """
    Represents an SSP (Supply-Side Platform) integration endpoint.
    The RTB bridge sends bid requests received from SSPs to our bidder.
    Each SSP has a unique token so we can route win notices correctly.
    """
    __tablename__ = "supply_partners"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)   # e.g. "Xandr", "OpenX"
    endpoint_url = Column(Text)                               # outgoing bid endpoint (for outbound DSP mode)
    incoming_token = Column(String(64), unique=True, nullable=False, index=True,
                            default=lambda: uuid.uuid4().hex)  # auth token SSP uses to POST bid requests to us
    protocol = Column(String(20), nullable=False, default="openrtb_2_5")  # openrtb_2_5|openrtb_3_0|custom
    qps_limit = Column(Integer, default=1000)                 # max bid requests/sec from this SSP
    bid_timeout_ms = Column(Integer, default=150)             # we must respond within this window
    status = Column(String(20), nullable=False, default="active")  # active|paused|testing
    # Optional seat/publisher mapping
    seat_id = Column(String(128))                             # our seat ID on this SSP
    publisher_id = Column(String(128))
    # Allowed formats from this SSP
    allowed_formats = Column(ARRAY(String), default=list)    # banner|native|video|push
    # Revenue share / margin
    revenue_share = Column(Numeric(5, 4), default=0)          # 0.15 = 15% margin
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    auction_logs = relationship("AuctionLog", back_populates="supply_partner")

    __table_args__ = (
        Index("idx_supply_partners_status", "status"),
    )


# ─────────────────────────────────────────────────────────
# BUDGET LOGS  (atomic spend tracking)
# ─────────────────────────────────────────────────────────
class BudgetLog(Base):
    """
    Immutable append-only log of every spend event.
    Used to reconstruct daily_spent / total_spent after a crash,
    and to audit budget pacing. Never update rows — only insert.
    """
    __tablename__ = "budget_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    line_item_id = Column(Integer, ForeignKey("line_items.id"), nullable=True, index=True)
    auction_log_id = Column(BigInteger, ForeignKey("auction_logs.id"), nullable=True)
    event_type = Column(String(30), nullable=False)   # win|impression|refund|adjustment
    amount = Column(Numeric(14, 6), nullable=False)   # positive = spend, negative = refund
    currency = Column(String(8), nullable=False, default="USD")
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    campaign = relationship("Campaign", back_populates="budget_logs")

    __table_args__ = (
        Index("idx_budgetlog_campaign_recorded", "campaign_id", "recorded_at"),
        Index("idx_budgetlog_lineitm_recorded", "line_item_id", "recorded_at"),
    )


# ─────────────────────────────────────────────────────────
# LINKS
# ─────────────────────────────────────────────────────────
class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(100))
    external_id = Column(String(255))
    original_url = Column(Text, nullable=False)
    short_code = Column(String(32), unique=True, nullable=False, index=True)
    custom_slug = Column(String(128), unique=True)
    is_active = Column(Boolean, nullable=False, default=True)
    cost_per_click = Column(Numeric(10, 6), default=0)
    expires_at = Column(DateTime(timezone=True))
    geo_rules = Column(JSONB, default=dict)
    device_rules = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="links")
    clicks = relationship("Click", back_populates="link")
    stats = relationship("Stat", back_populates="link")


# ─────────────────────────────────────────────────────────
# PIXELS
# ─────────────────────────────────────────────────────────
class Pixel(Base):
    __tablename__ = "pixels"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    pixel_id = Column(UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_fire_at = Column(DateTime(timezone=True))
    total_events = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="pixels")


# ─────────────────────────────────────────────────────────
# EVENTS (TimeSeries)
# ─────────────────────────────────────────────────────────
class Event(Base):
    __tablename__ = "events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pixel_id = Column(UUID(as_uuid=True), ForeignKey("pixels.pixel_id"), index=True)
    link_id = Column(Integer, ForeignKey("links.id"), index=True)
    auction_id = Column(String(128))
    imp_id = Column(String(128))
    session_id = Column(String(64))
    event_name = Column(String(64), nullable=False, index=True)
    sub_id = Column(String(255), index=True)
    creative_id = Column(String(255))
    click_id = Column(String(128), index=True)
    value = Column(Numeric(12, 6), default=0)
    currency = Column(String(8), default="USD")
    metadata_ = Column("metadata", JSONB, default=dict)
    ip = Column(INET)
    country_code = Column(String(4))
    device_type = Column(String(20))
    os = Column(String(50))
    browser = Column(String(50))
    user_agent = Column(Text)
    referer = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_events_created_at", "created_at"),
        Index("idx_events_link_created", "link_id", "created_at"),
        Index("idx_events_pixel_created", "pixel_id", "created_at"),
        Index("idx_events_name_created", "event_name", "created_at"),
        Index("idx_events_sub_created", "sub_id", "created_at"),
    )


# ─────────────────────────────────────────────────────────
# CLICKS (TimeSeries)
# ─────────────────────────────────────────────────────────
class Click(Base):
    __tablename__ = "clicks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    link_id = Column(Integer, ForeignKey("links.id"), nullable=False, index=True)
    sub_id = Column(String(255))
    creative_id = Column(String(255))
    session_id = Column(String(64))
    click_id = Column(UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4, index=True)
    ip = Column(INET)
    country_code = Column(String(4))
    device_type = Column(String(20))
    cost = Column(Numeric(10, 6), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    link = relationship("Link", back_populates="clicks")

    __table_args__ = (
        Index("idx_clicks_created_at", "created_at"),
        Index("idx_clicks_link_created", "link_id", "created_at"),
    )


# ─────────────────────────────────────────────────────────
# POSTBACKS (S2S audit)
# ─────────────────────────────────────────────────────────
class Postback(Base):
    __tablename__ = "postbacks"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    click_id = Column(UUID(as_uuid=True), index=True)
    payout = Column(Numeric(10, 6), default=0)
    currency = Column(String(8), default="USD")
    status = Column(String(50))
    raw_params = Column(JSONB, default=dict)
    processed = Column(Boolean, nullable=False, default=False)
    ip = Column(INET)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ─────────────────────────────────────────────────────────
# RTB — CREATIVES
# ─────────────────────────────────────────────────────────
class Creative(Base):
    __tablename__ = "creatives"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    # NEW: line_item_id — nullable for backward compat
    line_item_id = Column(Integer, ForeignKey("line_items.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String(255))
    format = Column(String(20), nullable=False)  # banner|native|video|push
    width = Column(Integer)
    height = Column(Integer)
    image_url = Column(Text)
    title = Column(String(255))
    description = Column(Text)
    click_url = Column(Text)
    adm = Column(Text)   # raw ad markup (VAST / NURL / HTML snippet)
    # NEW: tracking URLs for impression and click
    impression_tracker_url = Column(Text)
    click_tracker_url = Column(Text)
    status = Column(String(20), nullable=False, default="active")
    audit_status = Column(String(20), nullable=False, default="pending")  # pending|approved|rejected
    # NEW: weight for creative rotation within line item
    weight = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="creatives")
    line_item = relationship("LineItem", back_populates="creatives")


# ─────────────────────────────────────────────────────────
# RTB — TARGETING RULES  (campaign-level defaults)
# ─────────────────────────────────────────────────────────
class TargetingRule(Base):
    __tablename__ = "targeting_rules"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True)
    bid_floor = Column(Numeric(8, 6), default=0.01)
    bid_ceiling = Column(Numeric(8, 6), default=5.00)
    geo_countries = Column(ARRAY(String), default=list)
    device_types = Column(ARRAY(String), default=list)
    os_list = Column(ARRAY(String), default=list)
    browser_list = Column(ARRAY(String), default=list)
    site_whitelist = Column(ARRAY(String), default=list)
    site_blacklist = Column(ARRAY(String), default=list)
    hour_schedule = Column(JSONB, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    campaign = relationship("Campaign", back_populates="targeting")


# ─────────────────────────────────────────────────────────
# RTB — AUCTION LOGS (TimeSeries)
# ─────────────────────────────────────────────────────────
class AuctionLog(Base):
    __tablename__ = "auction_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), index=True)
    line_item_id = Column(Integer, ForeignKey("line_items.id"), nullable=True, index=True)
    creative_id = Column(Integer, ForeignKey("creatives.id"))
    supply_partner_id = Column(Integer, ForeignKey("supply_partners.id"), nullable=True, index=True)
    bid_request_id = Column(String(128))
    imp_id = Column(String(128))
    bid_price = Column(Numeric(10, 6))
    win_price = Column(Numeric(10, 6))
    clearing_price = Column(Numeric(10, 6))
    won = Column(Boolean, nullable=False, default=False, index=True)
    ssp_name = Column(String(100))
    site_domain = Column(String(255))
    app_bundle = Column(String(255))
    geo_country = Column(String(4))
    device_type = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    line_item = relationship("LineItem", back_populates="auction_logs")
    supply_partner = relationship("SupplyPartner", back_populates="auction_logs")

    __table_args__ = (
        Index("idx_auction_campaign_created", "campaign_id", "created_at"),
        Index("idx_auction_won_created", "won", "created_at"),
        Index("idx_auction_lineitem_created", "line_item_id", "created_at"),
    )


# ─────────────────────────────────────────────────────────
# AUTOPILOT RULES
# ─────────────────────────────────────────────────────────
class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    level = Column(String(20), nullable=False, default="campaign")  # campaign|sub_id|bid
    metric = Column(String(50), nullable=False)
    condition = Column(String(10), nullable=False)  # <|>|<=|>=|==
    threshold = Column(Numeric(12, 6), nullable=False)
    action = Column(String(50), nullable=False)
    triggered = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="rules")
    logs = relationship("RuleLog", back_populates="rule")


# ─────────────────────────────────────────────────────────
# AUTOPILOT RULE LOGS
# ─────────────────────────────────────────────────────────
class RuleLog(Base):
    __tablename__ = "rule_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    rule_id = Column(Integer, ForeignKey("rules.id"), nullable=False)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    triggered_at = Column(DateTime(timezone=True), server_default=func.now())
    metric_value = Column(Numeric(12, 6))
    action_taken = Column(String(100))
    notification_sent = Column(Boolean, nullable=False, default=False)

    rule = relationship("Rule", back_populates="logs")


# ─────────────────────────────────────────────────────────
# AD ACCOUNTS
# ─────────────────────────────────────────────────────────
class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)  # push_house|yandex|kadam|propellerads|google
    name = Column(String(255))
    credentials = Column(JSONB, default=dict)  # encrypted at app layer
    is_active = Column(Boolean, nullable=False, default=True)
    balance = Column(Numeric(12, 4))
    currency = Column(String(8), default="USD")
    last_sync_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="ad_accounts")


# ─────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    key = Column(String(100), nullable=False)
    value = Column(Text)

    user = relationship("User", back_populates="settings")

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_settings_user_key"),)


# ─────────────────────────────────────────────────────────
# LEGACY STATS (backward compat with v2)
# ─────────────────────────────────────────────────────────
class Stat(Base):
    __tablename__ = "stats"

    id = Column(Integer, primary_key=True, index=True)
    link_id = Column(Integer, ForeignKey("links.id"), nullable=False)
    sub_id = Column(String(255))
    creative_id = Column(String(255))
    clicks = Column(BigInteger, nullable=False, default=0)
    conversions = Column(BigInteger, nullable=False, default=0)
    bounces = Column(BigInteger, nullable=False, default=0)
    total_time = Column(BigInteger, nullable=False, default=0)
    cost = Column(Numeric(12, 6), default=0)
    revenue = Column(Numeric(12, 6), default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    link = relationship("Link", back_populates="stats")

    __table_args__ = (UniqueConstraint("link_id", "sub_id", "creative_id", name="uq_stats_link_sub_creative"),)
