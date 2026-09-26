from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base

NUMERIC_18_6 = Numeric(18, 6)


class BillingEnrichedModel(Base):
    __tablename__ = "billing_enriched"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("billing_reports.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    billing_period: Mapped[str] = mapped_column(String(7), nullable=False)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    usage_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)

    project_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    usage_amount: Mapped[Decimal | None] = mapped_column(NUMERIC_18_6, nullable=True)
    usage_unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cost: Mapped[Decimal] = mapped_column(NUMERIC_18_6, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")

    sku_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sku_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    component_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="unclassified"
    )
    mapping_confidence: Mapped[str | None] = mapped_column(String(10), nullable=True)
    match_layer: Mapped[str | None] = mapped_column(String(2), nullable=True)
    pricing_source: Mapped[str] = mapped_column(String(20), nullable=False)
    pricing_confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="line_item")

    source_line_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Exports detalhados (migração f6a7b8c9d0e1). Nulos no caminho legado.
    resource_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # `none_as_null`: sem ele o SQLAlchemy grava None como o JSON `null`, e a
    # linha legada ficaria indistinguível de "atributos vazios" numa query.
    attributes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_billing_enriched_user_period", "user_id", "billing_period"),
        Index("ix_billing_enriched_report_id", "report_id"),
    )
