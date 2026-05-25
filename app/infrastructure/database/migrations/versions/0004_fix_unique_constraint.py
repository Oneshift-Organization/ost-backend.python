"""0004_fix_unique_constraint.py

Drop old UNIQUE (provider, category, service_type).
Add UNIQUE (provider, service_name) — each service name is unique per provider.
"""

from __future__ import annotations

from typing import ClassVar

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: ClassVar[list[str] | None] = None
depends_on: ClassVar[list[str] | None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_cloud_services_comparation_provider_type",
        "cloud_services_comparation",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_cloud_services_comparation_provider_service",
        "cloud_services_comparation",
        ["provider", "service_name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_cloud_services_comparation_provider_service",
        "cloud_services_comparation",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_cloud_services_comparation_provider_type",
        "cloud_services_comparation",
        ["provider", "category", "service_type"],
    )
