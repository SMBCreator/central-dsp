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


# ─────────────────────────────────────────────────────
# USERS
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# CAMPAIGNS
# ─────────────────────────────────────────────────────
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
    attribution_model = Column(String(20), nullable=False, default="last_click")
    daily_budget = Column(Numeric(10, 4))
    total_budget = Column(Numeric(10, 4))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ─── RTB поля ───
    # Базовая ставка CPM. BidEngineService берёт это значение
    # как основу и применяет bid_adjustments.
    bid_price = Column(Numeric(10, 6), nullable=True, default=1.0)

    # bid_floor / bid_ceiling на уровне кампании (глобальные).
    # Если заданы — переопределяют targeting_rule.bid_floor/ceiling.
    bid_floor = Column(Numeric(8, 6), nullable=True)
    bid_ceiling = Column(Numeric(8, 6), nullable=True)

    # Fallback click URL для AdMarkupService (если у креатива нет своего click_url).
    click_url = Column(Text, nullable=True)

    # 1×1 impression pixel URL — FrequencyCapService + attribution.
    tracking_pixel = Column(Text, nullable=True)

    # Макс показов на одного пользователя (FrequencyCapService).
    freq_cap = Column(Integer, nullable=True)

    user = relationship("User", back_populates="campaigns")
    links = relationship("Link", back_populates="campaign", cascade="all, delete-orphan")
    pixels = relationship("Pixel", back_populates="campaign", cascade="all, delete-orphan")
    rules = relationship("Rule", back_populates="campaign", cascade="all, delete-orphan")
    creatives = relationship("Creative", back_populates="campaign", cascade="all, delete-orphan")
    targeting = relationship("TargetingRule", back_populates="campaign", uselist=False, cascade="all, delete-orphan")


# ─────────────────────────────────────────────────────
# LINKS
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# PIXELS
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# EVENTS (TimeSeries)
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# CLICKS (TimeSeries)
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# POSTBACKS (S2S audit)
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# RTB — CREATIVES
# ─────────────────────────────────────────────────────
class Creative(Base):
    __tablename__ = "creatives"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255))
    format = Column(String(20), nullable=False)  # banner|native|video|push
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    status = Column(String(20), nullable=False, default="active")
    audit_status = Column(String(20), nullable=False, default="pending")  # pending|approved|rejected
    audit_feedback = Column(Text, nullable=True)

    # ─── Banner ───
    image_url = Column(Text)
    click_url = Column(Text)

    # ─── Native ───
    title = Column(String(255))
    description = Column(Text)
    icon_url = Column(Text, nullable=True)          # иконка для Native
    sponsored_by = Column(String(255), nullable=True)  # "Sponsored by ..."

    # ─── Video ───
    vast_url = Column(Text, nullable=True)          # VAST wrapper URL
    video_url = Column(Text, nullable=True)         # прямая ссылка на MP4
    duration_sec = Column(Integer, nullable=True)   # длительность видео

    # ─── Raw markup ───
    adm = Column(Text)                              # HTML/VAST/native JSON

    # ─── OpenRTB adomain ───
    adomain = Column(String(255), nullable=True)    # example.com — домен рекламодателя

    campaign = relationship("Campaign", back_populates="creatives")


# ─────────────────────────────────────────────────────
# RTB — TARGETING RULES
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# RTB — AUCTION LOGS (TimeSeries)
# ─────────────────────────────────────────────────────
class AuctionLog(Base):
    __tablename__ = "auction_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), index=True)
    creative_id = Column(Integer, ForeignKey("creatives.id"))
    # Исправлено: было bid_request_id, но service.py пишет auction_id=...
    # Объединяем в одно поле auction_id (и добавляем alias bid_request_id для совместимости)
    auction_id = Column(String(128), index=True)
    imp_id = Column(String(128))
    bid_price = Column(Numeric(10, 6))
    win_price = Column(Numeric(10, 6))
    clearing_price = Column(Numeric(10, 6))
    won = Column(Boolean, nullable=False, default=False, index=True)
    loss_reason = Column(Integer, nullable=True)  # OpenRTB Loss Reason codes
    ssp_name = Column(String(100))
    site_domain = Column(String(255))
    app_bundle = Column(String(255))
    geo_country = Column(String(4))
    device_type = Column(String(20))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (
        Index("idx_auction_campaign_created", "campaign_id", "created_at"),
        Index("idx_auction_won_created", "won", "created_at"),
    )


# ─────────────────────────────────────────────────────
# RTB — BID ADJUSTMENTS
# ─────────────────────────────────────────────────────
class BidAdjustment(Base):
    """Per-dimension bid multipliers (ктрана, девайс, OS, час, сайт)."""
    __tablename__ = "bid_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    dimension = Column(String(20), nullable=False)  # country|device|os|hour|site
    value = Column(String(128), nullable=False)     # US|mobile|iOS|14|cnn.com
    multiplier = Column(Numeric(6, 4), nullable=False, default=1.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("campaign_id", "dimension", "value", name="uq_bid_adj_camp_dim_val"),
    )


# ─────────────────────────────────────────────────────
# AUTOPILOT RULES
# ─────────────────────────────────────────────────────
class Rule(Base):
    __tablename__ = "rules"

    id = Column(Integer, primary_key=True, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    level = Column(String(20), nullable=False, default="campaign")
    metric = Column(String(50), nullable=False)
    condition = Column(String(10), nullable=False)
    threshold = Column(Numeric(12, 6), nullable=False)
    action = Column(String(50), nullable=False)
    triggered = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("Campaign", back_populates="rules")
    logs = relationship("RuleLog", back_populates="rule")


# ─────────────────────────────────────────────────────
# AUTOPILOT RULE LOGS
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# AD ACCOUNTS
# ─────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    key = Column(String(100), nullable=False)
    value = Column(Text)

    user = relationship("User", back_populates="settings")

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_settings_user_key"),)


# ─────────────────────────────────────────────────────
# LEGACY STATS (backward compat with v2)
# ─────────────────────────────────────────────────────
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
