from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Numeric, Text,
    DateTime, ForeignKey, ARRAY, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, INET
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()


# ─────────────────────────────────────────
class UserDB(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name     = Column(String(255))
    role          = Column(String(20), nullable=False, default="viewer")  # admin|manager|viewer
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    campaigns  = relationship("CampaignDB", back_populates="user")
    ad_accounts = relationship("AdAccountDB", back_populates="user")


# ─────────────────────────────────────────
class CampaignDB(Base):
    __tablename__ = "campaigns"
    id                = Column(Integer, primary_key=True)
    user_id           = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name              = Column(String(255), nullable=False)
    status            = Column(String(20), nullable=False, default="active")  # active|stopped|paused|archived
    camp_code         = Column(String(32), unique=True, nullable=False)
    is_ab             = Column(Boolean, nullable=False, default=False)
    ab_limit          = Column(Integer)
    offer_value       = Column(Numeric(10, 4), default=0)
    attribution_model = Column(String(20), nullable=False, default="last_click")
    daily_budget      = Column(Numeric(10, 4))
    total_budget      = Column(Numeric(10, 4))
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user       = relationship("UserDB", back_populates="campaigns")
    links      = relationship("LinkDB", back_populates="campaign", cascade="all, delete-orphan")
    pixels     = relationship("PixelDB", back_populates="campaign", cascade="all, delete-orphan")
    rules      = relationship("RuleDB", back_populates="campaign", cascade="all, delete-orphan")
    creatives  = relationship("CreativeDB", back_populates="campaign", cascade="all, delete-orphan")
    targeting  = relationship("TargetingRuleDB", back_populates="campaign", uselist=False)


# ─────────────────────────────────────────
class LinkDB(Base):
    __tablename__ = "links"
    id             = Column(Integer, primary_key=True)
    campaign_id    = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    platform       = Column(String(100))
    external_id    = Column(String(255))
    original_url   = Column(Text, nullable=False)
    short_code     = Column(String(32), unique=True, nullable=False)
    custom_slug    = Column(String(128), unique=True)
    is_active      = Column(Boolean, nullable=False, default=True)
    cost_per_click = Column(Numeric(10, 6), default=0)
    expires_at     = Column(DateTime(timezone=True))
    geo_rules      = Column(JSONB, default={})
    device_rules   = Column(JSONB, default={})
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("CampaignDB", back_populates="links")
    clicks   = relationship("ClickDB", back_populates="link")


# ─────────────────────────────────────────
class PixelDB(Base):
    __tablename__ = "pixels"
    id           = Column(Integer, primary_key=True)
    campaign_id  = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    name         = Column(String(255), nullable=False)
    pixel_id     = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4, unique=True)
    is_active    = Column(Boolean, nullable=False, default=True)
    last_fire_at = Column(DateTime(timezone=True))
    total_events = Column(BigInteger, nullable=False, default=0)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("CampaignDB", back_populates="pixels")
    events   = relationship("EventDB", back_populates="pixel")


# ─────────────────────────────────────────
class EventDB(Base):
    """TimeSeries — all tracking events (pageview, lead, impression, win, etc)"""
    __tablename__ = "events"
    id           = Column(BigInteger, primary_key=True)
    pixel_id     = Column(UUID(as_uuid=True), ForeignKey("pixels.pixel_id"))
    link_id      = Column(Integer, ForeignKey("links.id"))
    auction_id   = Column(String(128))   # RTB auction ID from SSP
    imp_id       = Column(String(128))   # RTB impression ID
    session_id   = Column(String(64))
    event_name   = Column(String(64), nullable=False)
    sub_id       = Column(String(255))
    creative_id  = Column(String(255))
    click_id     = Column(String(128))
    value        = Column(Numeric(12, 6), default=0)
    currency     = Column(String(8), default="USD")
    metadata_    = Column("metadata", JSONB, default={})
    ip           = Column(INET)
    country_code = Column(String(4))
    device_type  = Column(String(20))
    os           = Column(String(50))
    browser      = Column(String(50))
    user_agent   = Column(Text)
    referer      = Column(Text)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    pixel = relationship("PixelDB", back_populates="events")


# ─────────────────────────────────────────
class ClickDB(Base):
    """TimeSeries — individual clicks for date-range analytics"""
    __tablename__ = "clicks"
    id           = Column(BigInteger, primary_key=True)
    link_id      = Column(Integer, ForeignKey("links.id"), nullable=False)
    sub_id       = Column(String(255))
    creative_id  = Column(String(255))
    session_id   = Column(String(64))
    click_id     = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4, unique=True)
    ip           = Column(INET)
    country_code = Column(String(4))
    device_type  = Column(String(20))
    cost         = Column(Numeric(10, 6), default=0)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    link = relationship("LinkDB", back_populates="clicks")


# ─────────────────────────────────────────
class PostbackDB(Base):
    """S2S postback audit log"""
    __tablename__ = "postbacks"
    id         = Column(BigInteger, primary_key=True)
    click_id   = Column(UUID(as_uuid=True))
    payout     = Column(Numeric(10, 6), default=0)
    currency   = Column(String(8), default="USD")
    status     = Column(String(50))
    raw_params = Column(JSONB, default={})
    processed  = Column(Boolean, nullable=False, default=False)
    ip         = Column(INET)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ─────────────────────────────────────────
class CreativeDB(Base):
    __tablename__ = "creatives"
    id           = Column(Integer, primary_key=True)
    campaign_id  = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    name         = Column(String(255))
    format       = Column(String(20), nullable=False)  # banner|native|video|push
    width        = Column(Integer)
    height       = Column(Integer)
    image_url    = Column(Text)
    title        = Column(String(255))
    description  = Column(Text)
    click_url    = Column(Text)
    adm          = Column(Text)   # raw ad markup
    status       = Column(String(20), nullable=False, default="active")
    audit_status = Column(String(20), nullable=False, default="pending")
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("CampaignDB", back_populates="creatives")


# ─────────────────────────────────────────
class TargetingRuleDB(Base):
    __tablename__ = "targeting_rules"
    id             = Column(Integer, primary_key=True)
    campaign_id    = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True)
    bid_floor      = Column(Numeric(8, 6), default=0.01)
    bid_ceiling    = Column(Numeric(8, 6), default=5.00)
    geo_countries  = Column(ARRAY(Text), default=[])
    device_types   = Column(ARRAY(Text), default=[])
    os_list        = Column(ARRAY(Text), default=[])
    browser_list   = Column(ARRAY(Text), default=[])
    site_whitelist = Column(ARRAY(Text), default=[])
    site_blacklist = Column(ARRAY(Text), default=[])
    hour_schedule  = Column(JSONB, default={})  # {"1": [9,10,11,...18], ...} weekday->hours
    updated_at     = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    campaign = relationship("CampaignDB", back_populates="targeting")


# ─────────────────────────────────────────
class AuctionLogDB(Base):
    """TimeSeries — every bid request (won + lost) for win rate stats"""
    __tablename__ = "auction_logs"
    id              = Column(BigInteger, primary_key=True)
    campaign_id     = Column(Integer, ForeignKey("campaigns.id"))
    creative_id     = Column(Integer, ForeignKey("creatives.id"))
    bid_request_id  = Column(String(128))
    imp_id          = Column(String(128))
    bid_price       = Column(Numeric(10, 6))
    win_price       = Column(Numeric(10, 6))
    clearing_price  = Column(Numeric(10, 6))
    won             = Column(Boolean, nullable=False, default=False)
    ssp_name        = Column(String(100))
    site_domain     = Column(String(255))
    app_bundle      = Column(String(255))
    geo_country     = Column(String(4))
    device_type     = Column(String(20))
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), index=True)


# ─────────────────────────────────────────
class RuleDB(Base):
    __tablename__ = "rules"
    id          = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False)
    level       = Column(String(20), nullable=False, default="campaign")  # campaign|sub_id|bid
    metric      = Column(String(50), nullable=False)
    condition   = Column(String(10), nullable=False)  # <|>|<=|>=|==
    threshold   = Column(Numeric(12, 6), nullable=False)
    action      = Column(String(50), nullable=False)
    triggered   = Column(Boolean, nullable=False, default=False)
    is_active   = Column(Boolean, nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    campaign = relationship("CampaignDB", back_populates="rules")
    logs     = relationship("RuleLogDB", back_populates="rule")


# ─────────────────────────────────────────
class RuleLogDB(Base):
    __tablename__ = "rule_logs"
    id                = Column(BigInteger, primary_key=True)
    rule_id           = Column(Integer, ForeignKey("rules.id"), nullable=False)
    campaign_id       = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    triggered_at      = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    metric_value      = Column(Numeric(12, 6))
    action_taken      = Column(String(100))
    notification_sent = Column(Boolean, nullable=False, default=False)

    rule = relationship("RuleDB", back_populates="logs")


# ─────────────────────────────────────────
class AdAccountDB(Base):
    __tablename__ = "ad_accounts"
    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform     = Column(String(50), nullable=False)  # push_house|yandex|kadam|propellerads|google
    name         = Column(String(255))
    credentials  = Column(JSONB, default={})  # encrypted at app layer
    is_active    = Column(Boolean, nullable=False, default=True)
    balance      = Column(Numeric(12, 4))
    currency     = Column(String(8), default="USD")
    last_sync_at = Column(DateTime(timezone=True))
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("UserDB", back_populates="ad_accounts")


# ─────────────────────────────────────────
class SettingDB(Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("user_id", "key"),)
    id      = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    key     = Column(String(100), nullable=False)
    value   = Column(Text)


# ─────────────────────────────────────────
class StatsDB(Base):
    """Legacy aggregated stats — kept for v2 backward compat"""
    __tablename__ = "stats"
    __table_args__ = (UniqueConstraint("link_id", "sub_id", "creative_id"),)
    id          = Column(Integer, primary_key=True)
    link_id     = Column(Integer, ForeignKey("links.id"), nullable=False)
    sub_id      = Column(String(255))
    creative_id = Column(String(255))
    clicks      = Column(BigInteger, nullable=False, default=0)
    conversions = Column(BigInteger, nullable=False, default=0)
    bounces     = Column(BigInteger, nullable=False, default=0)
    total_time  = Column(BigInteger, nullable=False, default=0)
    cost        = Column(Numeric(12, 6), default=0)
    revenue     = Column(Numeric(12, 6), default=0)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
