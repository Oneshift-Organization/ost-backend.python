"""add_cloud_price_points

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-07-25 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6b7c8d9e0f1'
down_revision: Union[str, None] = 'f5a6b7c8d9e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'cloud_price_points',
        sa.Column('provider', sa.String(length=10), nullable=False),
        sa.Column('sku_key', sa.String(length=120), nullable=False),
        sa.Column('region', sa.String(length=60), nullable=False),
        sa.Column('service_name', sa.String(length=200), nullable=False),
        sa.Column('service_type', sa.String(length=100), nullable=False),
        sa.Column('price', sa.Numeric(18, 8), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('unit', sa.String(length=30), nullable=False, server_default='instance_hour'),
        sa.Column('vcpus', sa.Numeric(6, 2), nullable=True),
        sa.Column('ram_gib', sa.Numeric(8, 2), nullable=True),
        sa.Column('source', sa.String(length=40), nullable=False, server_default='manual'),
        sa.Column('confidence', sa.String(length=10), nullable=False, server_default='official'),
        sa.Column('captured_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('provider', 'sku_key', 'region'),
    )
    op.create_index(
        'ix_price_points_provider_type', 'cloud_price_points', ['provider', 'service_type']
    )


def downgrade() -> None:
    op.drop_index('ix_price_points_provider_type', table_name='cloud_price_points')
    op.drop_table('cloud_price_points')
