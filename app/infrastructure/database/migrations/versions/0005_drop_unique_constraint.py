"""0005_drop_unique_constraint.py

Drop the UNIQUE constraint — CSV can have same service in multiple categories.
Keep the performance index on (category, service_type).
"""

from __future__ import annotations

from typing import ClassVar

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: ClassVar[list[str] | None] = None
depends_on: ClassVar[list[str] | None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_cloud_services_comparation_provider_service",
        "cloud_services_comparation",
        type_="unique",
    )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_cloud_services_comparation_provider_service",
        "cloud_services_comparation",
        ["provider", "service_name"],
    )
