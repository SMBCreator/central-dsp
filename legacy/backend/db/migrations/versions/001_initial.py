"""
Initial migration — CentralDSP full schema.
Creates all tables: users, campaigns, pixels, events, creatives,
targeting_rules, auction_logs, bid_adjustments, advertisers,
ad_accounts, smart_links, autopilot_rules.

Revision ID: 001_initial
Revises: —
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, UUID
import uuid

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── EXTENSIONS ───────────────────────────────────────────────────────────
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')

    # ─── USERS ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",         sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("email",      sa.String(320), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(128), nullable=False),
        sa.Column("full_name",  sa.String(200)),
        sa.Column("role",       sa.String(32), nullable=False, server_default="user"),
        sa.Column("is_active",  sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), onupdate=sa.text("NOW()")),
    )
    op.create_index("ix_users_email", "users", ["email"])

    # ─── ADVERTISERS ──────────────────────────────────────────────────────────
    op.create_table(
        "advertisers",
        sa.Column("id",           sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id",      sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",         sa.String(200), nullable=False),
        sa.Column("daily_budget", sa.Numeric(14, 4)),
        sa.Column("total_budget", sa.Numeric(14, 4)),
        sa.Column("currency",     sa.String(3), server_default="USD"),
        sa.Column("status",       sa.String(32), server_default="active"),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_advertisers_user_id", "advertisers", ["user_id"])

    # ─── AD ACCOUNTS (внешние сети + внутренний RTB) ──────────────────────────
    op.create_table(
        "ad_accounts",
        sa.Column("id",           sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id",      sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("network",      sa.String(64), nullable=False),   # push_house, propellerads, mgid, evadav, rtb
        sa.Column("label",        sa.String(200)),
        sa.Column("api_token",    sa.Text()),
        sa.Column("api_key",      sa.Text()),
        sa.Column("extra",        JSONB, server_default="{}"),      # доп. параметры конкретной сети
        sa.Column("is_active",    sa.Boolean(), server_default="true"),
        sa.Column("balance",      sa.Numeric(14, 4), server_default="0"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_ad_accounts_user_id", "ad_accounts", ["user_id"])
    op.create_index("ix_ad_accounts_network", "ad_accounts", ["network"])

    # ─── CAMPAIGNS ────────────────────────────────────────────────────────────
    op.create_table(
        "campaigns",
        sa.Column("id",             sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id",        sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("advertiser_id",  sa.Integer(), sa.ForeignKey("advertisers.id", ondelete="SET NULL")),
        sa.Column("ad_account_id",  sa.Integer(), sa.ForeignKey("ad_accounts.id", ondelete="SET NULL")),
        sa.Column("name",           sa.String(300), nullable=False),
        sa.Column("status",         sa.String(32), server_default="draft"),  # draft|active|paused|archived
        sa.Column("campaign_type",  sa.String(32), server_default="rtb"),    # rtb|push|pop|native
        sa.Column("external_id",    sa.String(200)),                          # ID в сторонней сети
        sa.Column("daily_budget",   sa.Numeric(14, 4)),
        sa.Column("total_budget",   sa.Numeric(14, 4)),
        sa.Column("bid_strategy",   sa.String(32), server_default="cpm"),    # cpm|cpc|cpa|auto
        sa.Column("target_cpa",     sa.Numeric(14, 4)),
        sa.Column("target_cpm",     sa.Numeric(14, 4)),
        sa.Column("start_date",     sa.Date()),
        sa.Column("end_date",       sa.Date()),
        sa.Column("created_at",     sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at",     sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_campaigns_user_id",   "campaigns", ["user_id"])
    op.create_index("ix_campaigns_status",    "campaigns", ["status"])
    op.create_index("ix_campaigns_ad_account","campaigns", ["ad_account_id"])

    # ─── CREATIVES ────────────────────────────────────────────────────────────
    op.create_table(
        "creatives",
        sa.Column("id",           sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id",  sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("format",       sa.String(32), nullable=False),   # banner|native|video|push
        sa.Column("width",        sa.Integer()),
        sa.Column("height",       sa.Integer()),
        sa.Column("image_url",    sa.Text()),
        sa.Column("icon_url",     sa.Text()),
        sa.Column("title",        sa.String(256)),
        sa.Column("description",  sa.Text()),
        sa.Column("click_url",    sa.Text(), nullable=False),
        sa.Column("adm",          sa.Text()),                        # raw ad markup (VAST/HTML)
        sa.Column("status",       sa.String(32), server_default="active"),
        sa.Column("audit_status", sa.String(32), server_default="pending"),  # pending|approved|rejected
        sa.Column("audit_feedback", sa.Text()),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_creatives_campaign_id",   "creatives", ["campaign_id"])
    op.create_index("ix_creatives_status_audit",  "creatives", ["status", "audit_status"])

    # ─── TARGETING RULES ──────────────────────────────────────────────────────
    op.create_table(
        "targeting_rules",
        sa.Column("id",             sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id",    sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("bid_floor",      sa.Numeric(10, 4), server_default="0.01"),
        sa.Column("bid_ceiling",    sa.Numeric(10, 4), server_default="10.00"),
        sa.Column("geo_countries",  ARRAY(sa.Text()), server_default="{}"),
        sa.Column("device_types",   ARRAY(sa.Text()), server_default="{}"),   # mobile|desktop|tablet|ctv
        sa.Column("os_list",        ARRAY(sa.Text()), server_default="{}"),   # iOS|Android|Windows|...
        sa.Column("browser_list",   ARRAY(sa.Text()), server_default="{}"),
        sa.Column("site_whitelist", ARRAY(sa.Text()), server_default="{}"),
        sa.Column("site_blacklist", ARRAY(sa.Text()), server_default="{}"),
        sa.Column("hour_schedule",  JSONB, server_default="{}"),             # {"0":[0,1,2,...23], "1":[...]} по дням недели
        sa.Column("age_groups",     ARRAY(sa.Text()), server_default="{}"),
        sa.Column("interests",      ARRAY(sa.Text()), server_default="{}"),
        sa.Column("updated_at",     sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )

    # ─── BID ADJUSTMENTS ──────────────────────────────────────────────────────
    op.create_table(
        "bid_adjustments",
        sa.Column("id",          sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension",   sa.String(32), nullable=False),   # country|device|os|hour|site
        sa.Column("value",       sa.String(200), nullable=False),  # 'US', 'mobile', '14', 'cnn.com'
        sa.Column("multiplier",  sa.Numeric(6, 3), nullable=False, server_default="1.0"),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
        sa.UniqueConstraint("campaign_id", "dimension", "value", name="uq_bid_adj"),
    )
    op.create_index("ix_bid_adj_campaign", "bid_adjustments", ["campaign_id"])

    # ─── AUCTION LOGS ─────────────────────────────────────────────────────────
    op.create_table(
        "auction_logs",
        sa.Column("id",            sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id",   sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("creative_id",   sa.Integer(), sa.ForeignKey("creatives.id", ondelete="SET NULL")),
        sa.Column("auction_id",    sa.String(128)),    # OpenRTB BidRequest.id
        sa.Column("imp_id",        sa.String(128)),    # OpenRTB Impression.id
        sa.Column("bid_price",     sa.Numeric(10, 4)), # наша ставка
        sa.Column("win_price",     sa.Numeric(10, 4)), # цена победы (clearing price)
        sa.Column("clearing_price",sa.Numeric(10, 4)),
        sa.Column("won",           sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("loss_reason",   sa.Integer()),      # OpenRTB loss reason code
        sa.Column("site_domain",   sa.String(256)),
        sa.Column("app_bundle",    sa.String(256)),
        sa.Column("geo_country",   sa.String(3)),
        sa.Column("device_type",   sa.String(32)),
        sa.Column("created_at",    sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_auction_logs_campaign_id",  "auction_logs", ["campaign_id"])
    op.create_index("ix_auction_logs_created_at",   "auction_logs", ["created_at"])
    op.create_index("ix_auction_logs_won",           "auction_logs", ["won"])
    op.create_index("ix_auction_logs_geo",           "auction_logs", ["geo_country"])

    # ─── PIXELS ───────────────────────────────────────────────────────────────
    op.create_table(
        "pixels",
        sa.Column("id",          sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id",     sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="SET NULL")),
        sa.Column("name",        sa.String(200), nullable=False),
        sa.Column("pixel_token", sa.String(64), nullable=False, unique=True),
        sa.Column("pixel_type",  sa.String(32), server_default="js"),  # js|postback|img
        sa.Column("is_active",   sa.Boolean(), server_default="true"),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_pixels_token",   "pixels", ["pixel_token"])
    op.create_index("ix_pixels_user_id", "pixels", ["user_id"])

    # ─── EVENTS ───────────────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id",           sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pixel_id",     sa.Integer(), sa.ForeignKey("pixels.id", ondelete="SET NULL")),
        sa.Column("event_name",   sa.String(64), nullable=False),  # impression|click|conversion|...
        sa.Column("sub_id",       sa.String(256)),
        sa.Column("click_id",     sa.String(256)),
        sa.Column("creative_id",  sa.String(64)),
        sa.Column("value",        sa.Numeric(12, 4)),
        sa.Column("currency",     sa.String(3), server_default="USD"),
        sa.Column("ip_address",   sa.String(45)),
        sa.Column("user_agent",   sa.Text()),
        sa.Column("country_code", sa.String(3)),
        sa.Column("device_type",  sa.String(32)),
        sa.Column("metadata_",    JSONB, server_default="{}"),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_events_pixel_id",    "events", ["pixel_id"])
    op.create_index("ix_events_event_name",  "events", ["event_name"])
    op.create_index("ix_events_created_at",  "events", ["created_at"])
    op.create_index("ix_events_sub_id",      "events", ["sub_id"])
    op.create_index("ix_events_click_id",    "events", ["click_id"])

    # ─── SMART LINKS ──────────────────────────────────────────────────────────
    op.create_table(
        "smart_links",
        sa.Column("id",           sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id",      sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name",         sa.String(200), nullable=False),
        sa.Column("slug",         sa.String(128), nullable=False, unique=True),
        sa.Column("destinations", JSONB, nullable=False, server_default="[]"),
        # [{"url": "...", "weight": 50, "geo": ["US"], "device": "mobile"}, ...]
        sa.Column("is_active",    sa.Boolean(), server_default="true"),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_smart_links_slug",    "smart_links", ["slug"])
    op.create_index("ix_smart_links_user_id", "smart_links", ["user_id"])

    # ─── AUTOPILOT RULES ──────────────────────────────────────────────────────
    op.create_table(
        "autopilot_rules",
        sa.Column("id",          sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id",     sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE")),
        sa.Column("name",        sa.String(200), nullable=False),
        sa.Column("trigger",     JSONB, nullable=False),
        # {"metric": "win_rate_pct", "operator": "<", "value": 1.0, "window_minutes": 60}
        sa.Column("action",      JSONB, nullable=False),
        # {"type": "adjust_bid", "delta_pct": 20} | {"type": "pause_campaign"} | {"type": "add_blacklist", "dimension": "site"}
        sa.Column("is_active",   sa.Boolean(), server_default="true"),
        sa.Column("last_fired_at", sa.DateTime(timezone=True)),
        sa.Column("fire_count",  sa.Integer(), server_default="0"),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_autopilot_rules_campaign", "autopilot_rules", ["campaign_id"])
    op.create_index("ix_autopilot_rules_active",   "autopilot_rules", ["is_active"])


def downgrade() -> None:
    for tbl in [
        "autopilot_rules", "smart_links", "events", "pixels",
        "auction_logs", "bid_adjustments", "targeting_rules",
        "creatives", "campaigns", "ad_accounts", "advertisers", "users",
    ]:
        op.drop_table(tbl)
