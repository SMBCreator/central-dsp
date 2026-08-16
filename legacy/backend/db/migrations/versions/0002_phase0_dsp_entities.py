"""Phase 0 — Add Advertiser, LineItem, SupplyPartner, BudgetLog.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

Apply with:  alembic upgrade head
Rollback:    alembic downgrade 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ── Alembic metadata ────────────────────────────────────────────────────────
revision = "0002"
down_revision = "0001"   # chain to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. advertisers ───────────────────────────────────────────────────────
    op.create_table(
        "advertisers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("iab_categories", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("billing_currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("total_budget_limit", sa.Numeric(14, 4), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_advertisers_user_id", "advertisers", ["user_id"])
    op.create_index("ix_advertisers_status", "advertisers", ["status"])

    # ── 2. supply_partners ───────────────────────────────────────────────────
    op.create_table(
        "supply_partners",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("endpoint_url", sa.Text(), nullable=True),
        sa.Column("incoming_token", sa.String(64), nullable=False, unique=True),
        sa.Column("protocol", sa.String(20), nullable=False, server_default="openrtb_2_5"),
        sa.Column("qps_limit", sa.Integer(), server_default="1000"),
        sa.Column("bid_timeout_ms", sa.Integer(), server_default="150"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("seat_id", sa.String(128), nullable=True),
        sa.Column("publisher_id", sa.String(128), nullable=True),
        sa.Column("allowed_formats", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("revenue_share", sa.Numeric(5, 4), server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_supply_partners_status", "supply_partners", ["status"])
    op.create_index("ix_supply_partners_incoming_token", "supply_partners", ["incoming_token"])

    # ── 3. line_items ────────────────────────────────────────────────────────
    op.create_table(
        "line_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(),
                  sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        # Bidding
        sa.Column("bid_strategy", sa.String(20), nullable=False, server_default="fixed"),
        sa.Column("bid_price", sa.Numeric(10, 6), nullable=False, server_default="1.0"),
        sa.Column("max_bid", sa.Numeric(10, 6), nullable=True),
        sa.Column("min_bid", sa.Numeric(10, 6), nullable=True),
        # Budget / Pacing
        sa.Column("daily_budget", sa.Numeric(10, 4), nullable=True),
        sa.Column("total_budget", sa.Numeric(10, 4), nullable=True),
        sa.Column("daily_spent", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("total_spent", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("pacing", sa.String(20), nullable=False, server_default="even"),
        # Schedule
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hour_schedule", postgresql.JSONB(), server_default="{}"),
        # Targeting
        sa.Column("geo_countries", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("device_types", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("os_list", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("browser_list", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("site_whitelist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("site_blacklist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("app_whitelist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("app_blacklist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("iab_categories", postgresql.ARRAY(sa.String()), server_default="{}"),
        # Frequency Cap
        sa.Column("freq_cap_impressions", sa.Integer(), nullable=True),
        sa.Column("freq_cap_window_hours", sa.Integer(), server_default="24"),
        # Creative rotation
        sa.Column("creative_rotation", sa.String(20), nullable=False, server_default="even"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("idx_lineitems_campaign_status", "line_items", ["campaign_id", "status"])

    # ── 4. budget_logs ───────────────────────────────────────────────────────
    op.create_table(
        "budget_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(),
                  sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("line_item_id", sa.Integer(),
                  sa.ForeignKey("line_items.id"), nullable=True),
        sa.Column("auction_log_id", sa.BigInteger(),
                  sa.ForeignKey("auction_logs.id"), nullable=True),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(14, 6), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("recorded_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_budgetlog_campaign_recorded", "budget_logs", ["campaign_id", "recorded_at"])
    op.create_index("idx_budgetlog_lineitm_recorded", "budget_logs", ["line_item_id", "recorded_at"])

    # ── 5. ALTER campaigns — add new columns ─────────────────────────────────
    op.add_column("campaigns",
        sa.Column("advertiser_id", sa.Integer(),
                  sa.ForeignKey("advertisers.id", ondelete="SET NULL"), nullable=True))
    op.add_column("campaigns",
        sa.Column("daily_spent", sa.Numeric(14, 6), nullable=False, server_default="0"))
    op.add_column("campaigns",
        sa.Column("total_spent", sa.Numeric(14, 6), nullable=False, server_default="0"))
    op.add_column("campaigns",
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaigns",
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_campaigns_advertiser_id", "campaigns", ["advertiser_id"])

    # ── 6. ALTER creatives — add line_item_id + tracking + weight ────────────
    op.add_column("creatives",
        sa.Column("line_item_id", sa.Integer(),
                  sa.ForeignKey("line_items.id", ondelete="SET NULL"), nullable=True))
    op.add_column("creatives",
        sa.Column("impression_tracker_url", sa.Text(), nullable=True))
    op.add_column("creatives",
        sa.Column("click_tracker_url", sa.Text(), nullable=True))
    op.add_column("creatives",
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_creatives_line_item_id", "creatives", ["line_item_id"])

    # ── 7. ALTER auction_logs — add line_item_id + supply_partner_id ─────────
    op.add_column("auction_logs",
        sa.Column("line_item_id", sa.Integer(),
                  sa.ForeignKey("line_items.id"), nullable=True))
    op.add_column("auction_logs",
        sa.Column("supply_partner_id", sa.Integer(),
                  sa.ForeignKey("supply_partners.id"), nullable=True))
    op.create_index("idx_auction_lineitem_created", "auction_logs", ["line_item_id", "created_at"])
    op.create_index("ix_auction_supply_partner", "auction_logs", ["supply_partner_id"])


def downgrade() -> None:
    # Reverse order of upgrade

    # auction_logs
    op.drop_index("ix_auction_supply_partner", table_name="auction_logs")
    op.drop_index("idx_auction_lineitem_created", table_name="auction_logs")
    op.drop_column("auction_logs", "supply_partner_id")
    op.drop_column("auction_logs", "line_item_id")

    # creatives
    op.drop_index("ix_creatives_line_item_id", table_name="creatives")
    op.drop_column("creatives", "weight")
    op.drop_column("creatives", "click_tracker_url")
    op.drop_column("creatives", "impression_tracker_url")
    op.drop_column("creatives", "line_item_id")

    # campaigns
    op.drop_index("ix_campaigns_advertiser_id", table_name="campaigns")
    op.drop_column("campaigns", "end_date")
    op.drop_column("campaigns", "start_date")
    op.drop_column("campaigns", "total_spent")
    op.drop_column("campaigns", "daily_spent")
    op.drop_column("campaigns", "advertiser_id")

    # new tables (reverse dep order)
    op.drop_table("budget_logs")
    op.drop_table("line_items")
    op.drop_table("supply_partners")
    op.drop_table("advertisers")
