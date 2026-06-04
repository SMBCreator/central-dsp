"""
RTB fields: add bid_price/bid_floor/bid_ceiling/click_url/tracking_pixel/freq_cap to campaigns,
add icon_url/sponsored_by/vast_url/video_url/duration_sec/adomain/audit_feedback to creatives,
rename auction_logs.bid_request_id -> auction_id, add loss_reason,
add bid_adjustments table.

Revision ID: 0002_rtb_fields
Revises: 0001
Create Date: 2026-06-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002_rtb_fields'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── campaigns: RTB поля ───
    op.add_column('campaigns', sa.Column('bid_price', sa.Numeric(10, 6), nullable=True, server_default='1.0'))
    op.add_column('campaigns', sa.Column('bid_floor', sa.Numeric(8, 6), nullable=True))
    op.add_column('campaigns', sa.Column('bid_ceiling', sa.Numeric(8, 6), nullable=True))
    op.add_column('campaigns', sa.Column('click_url', sa.Text(), nullable=True))
    op.add_column('campaigns', sa.Column('tracking_pixel', sa.Text(), nullable=True))
    op.add_column('campaigns', sa.Column('freq_cap', sa.Integer(), nullable=True))

    # ─── creatives: native/video/audit поля ───
    op.add_column('creatives', sa.Column('icon_url', sa.Text(), nullable=True))
    op.add_column('creatives', sa.Column('sponsored_by', sa.String(255), nullable=True))
    op.add_column('creatives', sa.Column('vast_url', sa.Text(), nullable=True))
    op.add_column('creatives', sa.Column('video_url', sa.Text(), nullable=True))
    op.add_column('creatives', sa.Column('duration_sec', sa.Integer(), nullable=True))
    op.add_column('creatives', sa.Column('adomain', sa.String(255), nullable=True))
    op.add_column('creatives', sa.Column('audit_feedback', sa.Text(), nullable=True))

    # ─── auction_logs: rename bid_request_id → auction_id + добавить loss_reason ───
    # Проверяем есть ли старое поле bid_request_id
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    auction_cols = [c['name'] for c in inspector.get_columns('auction_logs')]

    if 'bid_request_id' in auction_cols and 'auction_id' not in auction_cols:
        op.alter_column('auction_logs', 'bid_request_id', new_column_name='auction_id')
    elif 'auction_id' not in auction_cols:
        op.add_column('auction_logs', sa.Column('auction_id', sa.String(128), nullable=True))

    if 'loss_reason' not in auction_cols:
        op.add_column('auction_logs', sa.Column('loss_reason', sa.Integer(), nullable=True))

    # Index on auction_logs.auction_id (частый запрос по auction_id)
    op.create_index('idx_auction_auction_id', 'auction_logs', ['auction_id'])

    # ─── bid_adjustments: таблица множителей ставки ───
    op.create_table(
        'bid_adjustments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('campaign_id', sa.Integer(),
                  sa.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('dimension', sa.String(20), nullable=False),  # country|device|os|hour|site
        sa.Column('value', sa.String(128), nullable=False),
        sa.Column('multiplier', sa.Numeric(6, 4), nullable=False, server_default='1.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('campaign_id', 'dimension', 'value',
                            name='uq_bid_adj_camp_dim_val'),
    )
    op.create_index('idx_bid_adj_campaign', 'bid_adjustments', ['campaign_id'])


def downgrade() -> None:
    op.drop_table('bid_adjustments')

    # auction_logs: удаляем добавленные поля
    op.drop_index('idx_auction_auction_id', table_name='auction_logs')
    # не ренеймим обратно, чтобы не потерять данные
    op.drop_column('auction_logs', 'loss_reason')

    # creatives
    for col in ('audit_feedback', 'adomain', 'duration_sec', 'video_url', 'vast_url', 'sponsored_by', 'icon_url'):
        op.drop_column('creatives', col)

    # campaigns
    for col in ('freq_cap', 'tracking_pixel', 'click_url', 'bid_ceiling', 'bid_floor', 'bid_price'):
        op.drop_column('campaigns', col)
