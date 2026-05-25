"""initial billing schema

Revision ID: 0001
Revises:
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_reports",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(10), nullable=True),
        sa.Column("schema_version", sa.String(50), nullable=True),
        sa.Column("source_filename", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_billing_reports_user_id_created_at",
        "billing_reports",
        ["user_id", "created_at"],
    )

    op.create_table(
        "billing_items",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("billing_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("account_id", sa.String(200), nullable=True),
        sa.Column("service", sa.String(200), nullable=False),
        sa.Column("resource_id", sa.String(500), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("cost", sa.Numeric(18, 6), nullable=False),
        sa.Column("cost_discounted", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("usage_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("usage_unit", sa.String(50), nullable=True),
        sa.Column("usage_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("usage_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period", sa.String(7), nullable=False),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_correction", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_estimate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("line_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_billing_items_user_period_service",
        "billing_items",
        ["user_id", "billing_period", "service"],
    )
    op.create_index("ix_billing_items_report_id", "billing_items", ["report_id"])


def downgrade() -> None:
    op.drop_index("ix_billing_items_report_id", table_name="billing_items")
    op.drop_index("ix_billing_items_user_period_service", table_name="billing_items")
    op.drop_table("billing_items")
    op.drop_index("ix_billing_reports_user_id_created_at", table_name="billing_reports")
    op.drop_table("billing_reports")
