"""add_matching_fields_to_billing_enriched

Revision ID: d3c4e5f6a7b8
Revises: c2b3d4e5f6a7
Create Date: 2026-05-30 19:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd3c4e5f6a7b8'
down_revision: Union[str, None] = 'c2b3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'billing_enriched',
        sa.Column(
            'component_type',
            sa.String(length=30),
            nullable=False,
            server_default='unclassified',
        ),
    )
    op.add_column(
        'billing_enriched',
        sa.Column('mapping_confidence', sa.String(length=10), nullable=True),
    )
    op.add_column(
        'billing_enriched',
        sa.Column('match_layer', sa.String(length=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('billing_enriched', 'match_layer')
    op.drop_column('billing_enriched', 'mapping_confidence')
    op.drop_column('billing_enriched', 'component_type')
