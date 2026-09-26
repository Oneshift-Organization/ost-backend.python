"""tabelas do inventário: envio, recursos e relações

O inventário (AWS Config, Asset Inventory, Resource Graph, canônico) vira três
tabelas: o envio (`inventories`), os recursos normalizados, com as regras de rede
na linha do grupo de segurança, e as relações. Só tabelas novas: nada do que
existe muda. Ver PLANO_INVENTARIO_API.md, etapa I5.

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "a1b2c3d4e5f7"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(10), nullable=True),
        sa.Column("source_files", JSONB(), nullable=False, server_default="[]"),
        sa.Column("detected_formats", JSONB(), nullable=False, server_default="{}"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("captured_at_estimated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resource_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rule_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("counts", JSONB(), nullable=False, server_default="{}"),
        sa.Column("fingerprint", sa.String(64), nullable=True),
        sa.Column("declarations", JSONB(), nullable=False, server_default="{}"),
        sa.Column("declarations_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.String(2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_inventories_user_id_created_at", "inventories", ["user_id", "created_at"]
    )

    op.create_table(
        "inventory_resources",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "inventory_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("resource_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("native_type", sa.String(200), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("account", sa.Text(), nullable=True),
        sa.Column("region", sa.String(100), nullable=True),
        sa.Column("zone", sa.String(100), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attributes", JSONB(), nullable=True),
        sa.Column("tags", JSONB(), nullable=True),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rules", JSONB(), nullable=True),
        sa.UniqueConstraint("inventory_id", "resource_key", name="uq_inventory_resources_key"),
    )
    op.create_index(
        "ix_inventory_resources_inventory_kind", "inventory_resources", ["inventory_id", "kind"]
    )

    op.create_table(
        "inventory_relations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "inventory_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("target_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "inventory_id", "source_key", "target_key", "kind", name="uq_inventory_relations"
        ),
    )
    op.create_index("ix_inventory_relations_inventory", "inventory_relations", ["inventory_id"])


def downgrade() -> None:
    op.drop_index("ix_inventory_relations_inventory", table_name="inventory_relations")
    op.drop_table("inventory_relations")
    op.drop_index("ix_inventory_resources_inventory_kind", table_name="inventory_resources")
    op.drop_table("inventory_resources")
    op.drop_index("ix_inventories_user_id_created_at", table_name="inventories")
    op.drop_table("inventories")
