"""002_rtb_tables — create RTB supporting tables.

Revision ID: 002
Revises: 001
Create Date: 2026-06-01

Tables created / altered:
  - ssp_connections     (SSP integration credentials)
  - creatives           (banner/native/video creatives)
  - targeting_rules     (per-campaign RTB targeting)
  - auction_logs        (every bid + win/loss)

All tables use cascade delete from campaigns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    # ── ssp_connections ────────────────────────────────────────────────
    op.create_table(
        "ssp_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("secret", sa.String(512)),
        sa.Column("ip_whitelist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("extra_config", postgresql.JSONB(), server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("total_requests", sa.BigInteger(), server_default="0"),
        sa.Column("total_wins", sa.BigInteger(), server_default="0"),
        sa.Column("last_bid_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "platform", "name", name="uq_ssp_conn_user_platform_name"),
    )
    op.create_index("idx_ssp_conn_user_active", "ssp_connections", ["user_id", "is_active"])

    # ── creatives ─────────────────────────────────────────────────────
    op.create_table(
        "creatives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("format", sa.String(20), nullable=False),   # banner|native|video|push
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("image_url", sa.Text()),
        sa.Column("title", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("click_url", sa.Text()),
        sa.Column("adm", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("audit_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_creatives_campaign", "creatives", ["campaign_id"])
    op.create_index("idx_creatives_status", "creatives", ["status", "audit_status"])

    # ── targeting_rules ───────────────────────────────────────────────
    op.create_table(
        "targeting_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(),
                  sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("bid_floor", sa.Numeric(8, 6), server_default="0.010000"),
        sa.Column("bid_ceiling", sa.Numeric(8, 6), server_default="5.000000"),
        sa.Column("geo_countries", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("device_types", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("os_list", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("browser_list", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("site_whitelist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("site_blacklist", postgresql.ARRAY(sa.String()), server_default="{}"),
        sa.Column("hour_schedule", postgresql.JSONB(), server_default="{}"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── auction_logs ──────────────────────────────────────────────────
    op.create_table(
        "auction_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), index=True),
        sa.Column("creative_id", sa.Integer(), sa.ForeignKey("creatives.id"), index=True),
        sa.Column("bid_request_id", sa.String(128), index=True),
        sa.Column("imp_id", sa.String(128)),
        sa.Column("bid_price", sa.Numeric(10, 6)),
        sa.Column("win_price", sa.Numeric(10, 6)),
        sa.Column("clearing_price", sa.Numeric(10, 6)),
        sa.Column("won", sa.Boolean(), nullable=False, server_default="false", index=True),
        sa.Column("ssp_name", sa.String(100), index=True),
        sa.Column("site_domain", sa.String(255)),
        sa.Column("app_bundle", sa.String(255)),
        sa.Column("geo_country", sa.String(4)),
        sa.Column("device_type", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    op.create_index("idx_auction_campaign_created", "auction_logs", ["campaign_id", "created_at"])
    op.create_index("idx_auction_won_created", "auction_logs", ["won", "created_at"])
    op.create_index("idx_auction_ssp_created", "auction_logs", ["ssp_name", "created_at"])


def downgrade():
    op.drop_table("auction_logs")
    op.drop_table("targeting_rules")
    op.drop_table("creatives")
    op.drop_index("idx_ssp_conn_user_active")
    op.drop_table("ssp_connections")
