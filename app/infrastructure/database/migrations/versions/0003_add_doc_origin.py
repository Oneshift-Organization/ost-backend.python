"""0003_add_doc_origin.py

Add doc_origin column to cloud_services_comparation.
"""

from __future__ import annotations

from typing import ClassVar

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: ClassVar[list[str] | None] = None
depends_on: ClassVar[list[str] | None] = None


def upgrade() -> None:
    op.add_column(
        "cloud_services_comparation",
        sa.Column("doc_origin", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cloud_services_comparation", "doc_origin")
