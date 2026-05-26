"""add enrichment tables

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("billing_items", sa.Column("service_raw", sa.String(200), nullable=True))

    op.create_table(
        "service_ontology",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("usage_type", sa.String(200), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=False),
        sa.Column("confidence", sa.String(10), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_service_ontology_provider_usage_type",
        "service_ontology",
        ["provider", "usage_type"],
    )

    op.create_table(
        "cloud_sku_catalog",
        sa.Column("sku_id", sa.String(), primary_key=True),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("usage_unit", sa.String(50), nullable=True),
        sa.Column("price_per_unit", sa.Numeric(18, 6), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("attributes", JSONB, nullable=True),
        sa.Column("effective_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "billing_enriched",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("billing_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("billing_period", sa.String(7), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=True),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("usage_type", sa.String(200), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("usage_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("usage_unit", sa.String(50), nullable=True),
        sa.Column("cost", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("sku_id", sa.String(), nullable=True),
        sa.Column("sku_metadata", JSONB, nullable=True),
        sa.Column("pricing_source", sa.String(20), nullable=False),
        sa.Column("pricing_confidence", sa.String(10), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="line_item"),
        sa.Column("source_line_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_billing_enriched_user_period",
        "billing_enriched",
        ["user_id", "billing_period"],
    )
    op.create_index("ix_billing_enriched_report_id", "billing_enriched", ["report_id"])

    op.create_table(
        "unmapped_billing_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "report_id",
            UUID(as_uuid=True),
            sa.ForeignKey("billing_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("billing_period", sa.String(7), nullable=False),
        sa.Column("usage_type", sa.String(200), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("raw_payload", JSONB, nullable=False),
        sa.Column("source_line_hash", sa.String(64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_unmapped_billing_lines_user_period",
        "unmapped_billing_lines",
        ["user_id", "billing_period"],
    )


def downgrade() -> None:
    op.drop_index("ix_unmapped_billing_lines_user_period", table_name="unmapped_billing_lines")
    op.drop_table("unmapped_billing_lines")
    op.drop_index("ix_billing_enriched_report_id", table_name="billing_enriched")
    op.drop_index("ix_billing_enriched_user_period", table_name="billing_enriched")
    op.drop_table("billing_enriched")
    op.drop_table("cloud_sku_catalog")
    op.drop_index("ix_service_ontology_provider_usage_type", table_name="service_ontology")
    op.drop_table("service_ontology")
    op.drop_column("billing_items", "service_raw")
