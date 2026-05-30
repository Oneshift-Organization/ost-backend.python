"""add_sku_classification_rules

Revision ID: c2b3d4e5f6a7
Revises: b1a2c3d4e5f6
Create Date: 2026-05-30 18:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'c2b3d4e5f6a7'
down_revision: Union[str, None] = 'b1a2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sku_classification_rules',
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column('provider', sa.String(length=10), nullable=False),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('component_type', sa.String(length=30), nullable=False),
        sa.Column('family', sa.String(length=30), nullable=True),
        sa.Column('confidence', sa.String(length=10), server_default='medium', nullable=False),
        sa.Column('priority', sa.Integer(), server_default='100', nullable=False),
        sa.Column('active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_sku_rules_provider_priority',
        'sku_classification_rules',
        ['provider', 'priority'],
        unique=False,
        postgresql_where=sa.text('active'),
    )


def downgrade() -> None:
    op.drop_index('ix_sku_rules_provider_priority', table_name='sku_classification_rules')
    op.drop_table('sku_classification_rules')
