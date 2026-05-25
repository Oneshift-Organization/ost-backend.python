"""add cloud_services_metadata and cloud_services_comparation

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cloud_services_metadata",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_cloud_services_metadata_provider_category",
        "cloud_services_metadata",
        ["provider", "category"],
    )
    op.create_unique_constraint(
        "uq_cloud_services_metadata_provider_service",
        "cloud_services_metadata",
        ["provider", "service_name"],
    )

    op.create_table(
        "cloud_services_comparation",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(10), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_cloud_services_comparation_category_type",
        "cloud_services_comparation",
        ["category", "service_type"],
    )
    op.create_unique_constraint(
        "uq_cloud_services_comparation_provider_type",
        "cloud_services_comparation",
        ["provider", "category", "service_type"],
    )


def downgrade() -> None:
    op.drop_table("cloud_services_comparation")
    op.drop_table("cloud_services_metadata")
