"""add_llm_input_payload_to_analysis_jobs

Revision ID: e4f5a6b7c8d9
Revises: d3c4e5f6a7b8
Create Date: 2026-06-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, None] = 'd3c4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'analysis_jobs',
        sa.Column('llm_input_payload', postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('analysis_jobs', 'llm_input_payload')
