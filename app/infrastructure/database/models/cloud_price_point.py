from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class CloudPricePointModel(Base):
    """Preço oficial de nuvem com proveniência (Precision Engine v3 §3.2)."""

    __tablename__ = "cloud_price_points"

    provider: Mapped[str] = mapped_column(String(10), primary_key=True)
    sku_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    region: Mapped[str] = mapped_column(String(60), primary_key=True)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    service_type: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    unit: Mapped[str] = mapped_column(String(30), nullable=False, default="instance_hour")
    vcpus: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    ram_gib: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    confidence: Mapped[str] = mapped_column(String(10), nullable=False, default="official")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Ciclo de vida (PLANO_CICLO_DE_VIDA_DO_PRECO): recalculado depois de cada sync
    estado: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confianca_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    confirmado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ultima_fonte: Mapped[str | None] = mapped_column(String(20), nullable=True)
    versao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1", default=1)
    # A coleta oficial deixou de trazer o SKU: fica como referência e envelhece; a
    # cotação ignora. Volta a None quando a coleta traz de novo.
    ausente_desde: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_price_points_provider_type", "provider", "service_type"),)
