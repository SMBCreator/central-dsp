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
from database import Base


# ────────────────────────────────────────────────────────
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
    ssp_connections = relationship("SSPConnection", back_populates="user", cascade="all, delete-orphan")


# ────────────────────────────────────────────────────────
class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active")  # active|stopped|paused|archived
    camp_code = Column(String(32), unique=True, nullable=False, index=True)
    is_ab = Column(Boolean, nullable=False, default=False)
    ab_limit = Column(Integer)
    offer_value = Column(Numeric(10, 4), default=0)
    attribution_model = Column(String(20), nullable=False, default="last_click")  # last_click|first_click|linear
    daily_budget = Column(Numeric(10, 4))
    total_budget = Column(Numeric(10, 4))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="campaigns")
    links = relationship("Link", back_populates="campaign", cascade="all, delete-orphan")
    pixels = relationship("Pixel", back_populates="campaign", cascade="all, delete-orphan")
    rules = relationship("Rule", back_populates="campaign", cascade="all, delete-orphan")
    creatives = relationship("Creative", back_populates="campaign", cascade="all, delete-orphan")
    targeting = relationship("TargetingRule", back_populates="campaign", uselist=False, cascade="all, delete-orphan")


# ────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────
class Creative(Base):
    __tablename__ = "creatives"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255))
    format = Column(String(20), nullable=False)  # banner|native|video|push
    width = Column(Integer)
    height = Column(Integer)
    image_url = Column(Text)
    title = Column(String(255))
    description = Column(Text)
    click_url = Column(Text)
    adm = Column(Text)  # raw ad markup (VAST/HTML/native JSON)
    status = Column(String(20), nullable=False, default="active")
    audit_status = Column(String(20), nullable=False, default="pending")  # pending|approved|rejected
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="creatives")
    auction_logs = relationship("AuctionLog", back_populates="creative")


# ────────────────────────────────────────────────────────
class TargetingRule(Base):
    __tablename__ = "targeting_rules"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True)
    bid_floor = Column(Numeric(8, 6), default=0.01)
    bid_ceiling = Column(Numeric(8, 6), default=5.00)
    geo_countries = Column(ARRAY(String), default=list)    # ISO 3166-1 alpha-3: RUS, UKR, BLR
    device_types = Column(ARRAY(String), default=list)    # desktop|mobile|tablet|ctv
    os_list = Column(ARRAY(String), default=list)          # iOS|Android|Windows|macOS
    browser_list = Column(ARRAY(String), default=list)    # Chrome|Firefox|Safari
    site_whitelist = Column(ARRAY(String), default=list)
    site_blacklist = Column(ARRAY(String), default=list)
    hour_schedule = Column(JSONB, default=dict)            # {"0":[0,1..23], "*":[9..22]}
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    campaign = relationship("Campaign", back_populates="targeting")


# ────────────────────────────────────────────────────────
class AuctionLog(Base):
    __tablename__ = "auction_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), index=True)
    creative_id = Column(Integer, ForeignKey("creatives.id"), index=True)
    bid_request_id = Column(String(128), index=True)
    imp_id = Column(String(128))
    bid_price = Column(Numeric(10, 6))
    win_price = Column(Numeric(10, 6))
    clearing_price = Column(Numeric(10, 6))
    won = Column(Boolean, nullable=False, default=False, index=True)
    ssp_name = Column(String(100), index=True)
    site_domain = Column(String(255))
    app_bundle = Column(String(255))
    geo_country = Column(String(4))
    device_type = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    campaign = relationship("Campaign")
    creative = relationship("Creative", back_populates="auction_logs")

    __table_args__ = (
        Index("idx_auction_campaign_created", "campaign_id", "created_at"),
        Index("idx_auction_won_created", "won", "created_at"),
        Index("idx_auction_ssp_created", "ssp_name", "created_at"),
    )


# ────────────────────────────────────────────────────────
# SSP CONNECTIONS
# ────────────────────────────────────────────────────────
class SSPConnection(Base):
    """
    Stores connection credentials and metadata for each connected SSP.
    One record per SSP integration per user.
    """
    __tablename__ = "ssp_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)   # kadam|sape|getintent|buzzoola|hybrid|yandex
    name = Column(String(255), nullable=False)       # human label
    secret = Column(String(512))                     # shared secret (stored encrypted at app level)
    ip_whitelist = Column(ARRAY(String), default=list)  # allowed SSP IPs
    extra_config = Column(JSONB, default=dict)       # platform-specific settings
    is_active = Column(Boolean, nullable=False, default=True)

    # Stats (updated by win/loss notice handlers)
    total_requests = Column(BigInteger, default=0)
    total_wins = Column(BigInteger, default=0)
    last_bid_at = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="ssp_connections")

    __table_args__ = (
        UniqueConstraint("user_id", "platform", "name", name="uq_ssp_conn_user_platform_name"),
        Index("idx_ssp_conn_user_active", "user_id", "is_active"),
    )


# ────────────────────────────────────────────────────────
class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    level = Column(String(20), nullable=False, default="campaign")  # campaign|sub_id|bid
    metric = Column(String(50), nullable=False)   # roi|cpa|cr|win_rate|spend_pct|cpm
    condition = Column(String(10), nullable=False) # <|>|<=|>=|==
    threshold = Column(Numeric(12, 6), nullable=False)
    action = Column(String(50), nullable=False)    # pause|stop|bid_up_20pct|bid_down_15pct|blacklist_site
    triggered = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="rules")
    logs = relationship("RuleLog", back_populates="rule")


# ────────────────────────────────────────────────────────
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


# ────────────────────────────────────────────────────────
class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(50), nullable=False)
    name = Column(String(255))
    credentials = Column(JSONB, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    balance = Column(Numeric(12, 4))
    currency = Column(String(8), default="USD")
    last_sync_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="ad_accounts")


# ────────────────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    key = Column(String(100), nullable=False)
    value = Column(Text)

    user = relationship("User", back_populates="settings")

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_settings_user_key"),)


# ────────────────────────────────────────────────────────
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
