"""campos de export detalhado em billing_items e billing_enriched

CUR, BigQuery, Azure e OCI trazem o que os dois CSVs agregados não trazem:
descritor por SKU, natureza da linha, valor faturado versus amortizado, recurso e
shape medido. Sem colunas próprias, o recurso parava em `billing_items` e o
descritor era procurado no `raw_payload` pelo nome da coluna de cada formato.

Tudo nulo e sem default: linha do caminho legado continua exatamente como era.
Ver PLANO_CUR_BIGQUERY.md, etapa E1.

Revision ID: f6a7b8c9d0e1
Revises: e2f3a4b5c6d7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "f6a7b8c9d0e1"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("billing_items", sa.Column("usage_type", sa.String(500), nullable=True))
    op.add_column("billing_items", sa.Column("resource_type", sa.String(200), nullable=True))
    op.add_column("billing_items", sa.Column("charge_kind", sa.String(20), nullable=True))
    op.add_column("billing_items", sa.Column("cost_billed", sa.Numeric(18, 6), nullable=True))
    op.add_column("billing_items", sa.Column("attributes", JSONB(), nullable=True))

    op.add_column("billing_enriched", sa.Column("resource_id", sa.String(500), nullable=True))
    op.add_column("billing_enriched", sa.Column("attributes", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("billing_enriched", "attributes")
    op.drop_column("billing_enriched", "resource_id")

    op.drop_column("billing_items", "attributes")
    op.drop_column("billing_items", "cost_billed")
    op.drop_column("billing_items", "charge_kind")
    op.drop_column("billing_items", "resource_type")
    op.drop_column("billing_items", "usage_type")
