"""0006_add_docs_costs.py

Add docs_costs column to cloud_services_metadata.
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: ClassVar[list[str] | None] = None
depends_on: ClassVar[list[str] | None] = None


def upgrade() -> None:
    op.add_column(
        "cloud_services_metadata",
        sa.Column("docs_costs", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_services_metadata", "docs_costs")
