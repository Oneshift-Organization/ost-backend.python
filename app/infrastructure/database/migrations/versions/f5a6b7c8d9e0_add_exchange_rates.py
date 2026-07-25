"""add_exchange_rates

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-06-01 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'exchange_rates',
        sa.Column('par', sa.String(length=7), nullable=False),
        sa.Column('data', sa.Date(), nullable=False),
        sa.Column('taxa', sa.Numeric(18, 8), nullable=False),
        sa.Column('fonte', sa.String(length=40), nullable=False, server_default='manual'),
        sa.PrimaryKeyConstraint('par', 'data'),
    )


def downgrade() -> None:
    op.drop_table('exchange_rates')
