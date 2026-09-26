"""Tabelas do inventário (I5 do PLANO_INVENTARIO_API).

``inventories`` descreve o envio; ``inventory_resources`` e
``inventory_relations`` guardam o conteúdo normalizado. As regras de rede ficam
na linha do grupo de segurança a que pertencem (``rules``): são lidas sempre
junto com ele, e uma tabela a mais só serviria para juntar de volta.

Apagar o inventário apaga o conteúdo (``ON DELETE CASCADE``): é o `DELETE` da
RI17, e não deixa linha órfã com IP e nome de máquina do cliente.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class InventoryModel(Base):
    __tablename__ = "inventories"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    provider: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source_files: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    detected_formats: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    captured_at_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    relation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rule_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    counts: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    declarations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    declarations_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_inventories_user_id_created_at", "user_id", "created_at"),)


class InventoryResourceModel(Base):
    __tablename__ = "inventory_resources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inventory_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventories.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    resource_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    native_type: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    account: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    tags: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rules: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("inventory_id", "resource_key", name="uq_inventory_resources_key"),
        Index("ix_inventory_resources_inventory_kind", "inventory_id", "kind"),
    )


class InventoryRelationModel(Base):
    __tablename__ = "inventory_relations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    inventory_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inventories.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    target_key: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "inventory_id", "source_key", "target_key", "kind", name="uq_inventory_relations"
        ),
        Index("ix_inventory_relations_inventory", "inventory_id"),
    )
