"""Initial schema — Central DSP v2.

Revision ID: 0001
Revises: —
Create Date: 2026-06-01

Creates all tables:
  users, campaigns, links, pixels, events, clicks, postbacks,
  creatives, targeting_rules, auction_logs,
  rules, rule_logs, ad_accounts, settings, stats
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, INET, JSONB, ARRAY

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── campaigns ──────────────────────────────────────────────────────────
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("camp_code", sa.String(32), nullable=False),
        sa.Column("is_ab", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ab_limit", sa.Integer),
        sa.Column("offer_value", sa.Numeric(10, 4), server_default="0"),
        sa.Column("attribution_model", sa.String(20), nullable=False, server_default="last_click"),
        sa.Column("daily_budget", sa.Numeric(10, 4)),
        sa.Column("total_budget", sa.Numeric(10, 4)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_campaigns_id", "campaigns", ["id"])
    op.create_index("ix_campaigns_camp_code", "campaigns", ["camp_code"], unique=True)

    # ── links ──────────────────────────────────────────────────────────────
    op.create_table(
        "links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(100)),
        sa.Column("external_id", sa.String(255)),
        sa.Column("original_url", sa.Text, nullable=False),
        sa.Column("short_code", sa.String(32), nullable=False),
        sa.Column("custom_slug", sa.String(128)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("cost_per_click", sa.Numeric(10, 6), server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("geo_rules", JSONB, server_default="{}"),
        sa.Column("device_rules", JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_links_id", "links", ["id"])
    op.create_index("ix_links_short_code", "links", ["short_code"], unique=True)
    op.create_index("ix_links_custom_slug", "links", ["custom_slug"], unique=True)

    # ── pixels ─────────────────────────────────────────────────────────────
    op.create_table(
        "pixels",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("pixel_id", UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_fire_at", sa.DateTime(timezone=True)),
        sa.Column("total_events", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_pixels_id", "pixels", ["id"])
    op.create_index("ix_pixels_pixel_id", "pixels", ["pixel_id"], unique=True)

    # ── events ─────────────────────────────────────────────────────────────
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("pixel_id", UUID(as_uuid=True), sa.ForeignKey("pixels.pixel_id")),
        sa.Column("link_id", sa.Integer, sa.ForeignKey("links.id")),
        sa.Column("auction_id", sa.String(128)),
        sa.Column("imp_id", sa.String(128)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("sub_id", sa.String(255)),
        sa.Column("creative_id", sa.String(255)),
        sa.Column("click_id", sa.String(128)),
        sa.Column("value", sa.Numeric(12, 6), server_default="0"),
        sa.Column("currency", sa.String(8), server_default="USD"),
        # Python attr: metadata_  |  DB column name: metadata
        sa.Column("metadata", JSONB, server_default="{}"),
        sa.Column("ip", INET),
        sa.Column("country_code", sa.String(4)),
        sa.Column("device_type", sa.String(20)),
        sa.Column("os", sa.String(50)),
        sa.Column("browser", sa.String(50)),
        sa.Column("user_agent", sa.Text),
        sa.Column("referer", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_events_created_at", "events", ["created_at"])
    op.create_index("idx_events_link_created", "events", ["link_id", "created_at"])
    op.create_index("idx_events_pixel_created", "events", ["pixel_id", "created_at"])
    op.create_index("idx_events_name_created", "events", ["event_name", "created_at"])
    op.create_index("idx_events_sub_created", "events", ["sub_id", "created_at"])
    op.create_index("ix_events_event_name", "events", ["event_name"])
    op.create_index("ix_events_link_id", "events", ["link_id"])
    op.create_index("ix_events_pixel_id", "events", ["pixel_id"])
    op.create_index("ix_events_sub_id", "events", ["sub_id"])
    op.create_index("ix_events_click_id", "events", ["click_id"])

    # ── clicks ─────────────────────────────────────────────────────────────
    op.create_table(
        "clicks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("link_id", sa.Integer, sa.ForeignKey("links.id"), nullable=False),
        sa.Column("sub_id", sa.String(255)),
        sa.Column("creative_id", sa.String(255)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("click_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ip", INET),
        sa.Column("country_code", sa.String(4)),
        sa.Column("device_type", sa.String(20)),
        sa.Column("cost", sa.Numeric(10, 6), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_clicks_created_at", "clicks", ["created_at"])
    op.create_index("idx_clicks_link_created", "clicks", ["link_id", "created_at"])
    op.create_index("ix_clicks_link_id", "clicks", ["link_id"])
    op.create_index("ix_clicks_click_id", "clicks", ["click_id"], unique=True)

    # ── postbacks ──────────────────────────────────────────────────────────
    op.create_table(
        "postbacks",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("click_id", UUID(as_uuid=True)),
        sa.Column("payout", sa.Numeric(10, 6), server_default="0"),
        sa.Column("currency", sa.String(8), server_default="USD"),
        sa.Column("status", sa.String(50)),
        sa.Column("raw_params", JSONB, server_default="{}"),
        sa.Column("processed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ip", INET),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_postbacks_click_id", "postbacks", ["click_id"])
    op.create_index("ix_postbacks_created_at", "postbacks", ["created_at"])

    # ── creatives ──────────────────────────────────────────────────────────
    op.create_table(
        "creatives",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("image_url", sa.Text),
        sa.Column("title", sa.String(255)),
        sa.Column("description", sa.Text),
        sa.Column("click_url", sa.Text),
        sa.Column("adm", sa.Text),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("audit_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_creatives_id", "creatives", ["id"])

    # ── targeting_rules ────────────────────────────────────────────────────
    op.create_table(
        "targeting_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("bid_floor", sa.Numeric(8, 6), server_default="0.01"),
        sa.Column("bid_ceiling", sa.Numeric(8, 6), server_default="5.00"),
        sa.Column("geo_countries", ARRAY(sa.String), server_default="{}"),
        sa.Column("device_types", ARRAY(sa.String), server_default="{}"),
        sa.Column("os_list", ARRAY(sa.String), server_default="{}"),
        sa.Column("browser_list", ARRAY(sa.String), server_default="{}"),
        sa.Column("site_whitelist", ARRAY(sa.String), server_default="{}"),
        sa.Column("site_blacklist", ARRAY(sa.String), server_default="{}"),
        sa.Column("hour_schedule", JSONB, server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_targeting_rules_id", "targeting_rules", ["id"])

    # ── auction_logs ───────────────────────────────────────────────────────
    op.create_table(
        "auction_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id")),
        sa.Column("creative_id", sa.Integer, sa.ForeignKey("creatives.id")),
        sa.Column("bid_request_id", sa.String(128)),
        sa.Column("imp_id", sa.String(128)),
        sa.Column("bid_price", sa.Numeric(10, 6)),
        sa.Column("win_price", sa.Numeric(10, 6)),
        sa.Column("clearing_price", sa.Numeric(10, 6)),
        sa.Column("won", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("ssp_name", sa.String(100)),
        sa.Column("site_domain", sa.String(255)),
        sa.Column("app_bundle", sa.String(255)),
        sa.Column("geo_country", sa.String(4)),
        sa.Column("device_type", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_auction_campaign_created", "auction_logs", ["campaign_id", "created_at"])
    op.create_index("idx_auction_won_created", "auction_logs", ["won", "created_at"])
    op.create_index("ix_auction_logs_campaign_id", "auction_logs", ["campaign_id"])
    op.create_index("ix_auction_logs_won", "auction_logs", ["won"])
    op.create_index("ix_auction_logs_created_at", "auction_logs", ["created_at"])

    # ── rules ──────────────────────────────────────────────────────────────
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="campaign"),
        sa.Column("metric", sa.String(50), nullable=False),
        sa.Column("condition", sa.String(10), nullable=False),
        sa.Column("threshold", sa.Numeric(12, 6), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("triggered", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_rules_id", "rules", ["id"])

    # ── rule_logs ──────────────────────────────────────────────────────────
    op.create_table(
        "rule_logs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("rules.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer, sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("metric_value", sa.Numeric(12, 6)),
        sa.Column("action_taken", sa.String(100)),
        sa.Column("notification_sent", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_rule_logs_campaign_id", "rule_logs", ["campaign_id"])

    # ── ad_accounts ────────────────────────────────────────────────────────
    op.create_table(
        "ad_accounts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("credentials", JSONB, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("balance", sa.Numeric(12, 4)),
        sa.Column("currency", sa.String(8), server_default="USD"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ad_accounts_id", "ad_accounts", ["id"])

    # ── settings ───────────────────────────────────────────────────────────
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text),
        sa.UniqueConstraint("user_id", "key", name="uq_settings_user_key"),
    )
    op.create_index("ix_settings_id", "settings", ["id"])

    # ── stats ──────────────────────────────────────────────────────────────
    op.create_table(
        "stats",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("link_id", sa.Integer, sa.ForeignKey("links.id"), nullable=False),
        sa.Column("sub_id", sa.String(255)),
        sa.Column("creative_id", sa.String(255)),
        sa.Column("clicks", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("conversions", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("bounces", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("total_time", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(12, 6), server_default="0"),
        sa.Column("revenue", sa.Numeric(12, 6), server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("link_id", "sub_id", "creative_id", name="uq_stats_link_sub_creative"),
    )
    op.create_index("ix_stats_id", "stats", ["id"])


def downgrade() -> None:
    """Drop all tables in reverse creation order (respects FK dependencies)."""
    op.drop_table("stats")
    op.drop_table("settings")
    op.drop_table("ad_accounts")
    op.drop_table("rule_logs")
    op.drop_table("rules")
    op.drop_table("auction_logs")
    op.drop_table("targeting_rules")
    op.drop_table("creatives")
    op.drop_table("postbacks")
    op.drop_table("clicks")
    op.drop_table("events")
    op.drop_table("pixels")
    op.drop_table("links")
    op.drop_table("campaigns")
    op.drop_table("users")
