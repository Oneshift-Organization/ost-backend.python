from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base

NUMERIC_18_6 = Numeric(18, 6)


class CloudSkuCatalogModel(Base):
    __tablename__ = "cloud_sku_catalog"

    sku_id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    usage_unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    price_per_unit: Mapped[Decimal | None] = mapped_column(NUMERIC_18_6, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    vcpus: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    ram_gb: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    effective_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_sku_catalog_provider_type_hw", "provider", "service_type", "vcpus", "ram_gb"),
    )
