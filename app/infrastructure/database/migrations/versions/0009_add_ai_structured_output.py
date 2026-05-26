"""add_ai_structured_output_to_analysis_jobs

Revision ID: 0009
Revises: 8f40871ac57e
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0009"
down_revision: str | None = "8f40871ac57e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "analysis_jobs",
        sa.Column("ai_structured_output", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_jobs", "ai_structured_output")
