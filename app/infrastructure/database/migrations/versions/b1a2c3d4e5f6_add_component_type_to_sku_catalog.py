"""add_component_type_to_sku_catalog

Revision ID: b1a2c3d4e5f6
Revises: a1f6761f2452
Create Date: 2026-05-30 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1a2c3d4e5f6'
down_revision: Union[str, None] = 'a1f6761f2452'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'cloud_sku_catalog',
        sa.Column(
            'component_type',
            sa.String(length=30),
            nullable=False,
            server_default='unclassified',
        ),
    )


def downgrade() -> None:
    op.drop_column('cloud_sku_catalog', 'component_type')
