"""Ciclo de vida do preço: observações e regras em tabela (PLANO_CICLO_DE_VIDA_DO_PRECO)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class PriceObservationModel(Base):
    """Observação de um preço que não é a confirmação diária do sync: troca de valor,
    billing de cliente (P3.3) ou resposta do agente."""

    __tablename__ = "price_observations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(10), nullable=False)
    sku_key: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[str] = mapped_column(String(60), nullable=False)
    fonte: Mapped[str] = mapped_column(String(20), nullable=False)
    preco: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    moeda: Mapped[str] = mapped_column(String(3), nullable=False)
    unidade: Mapped[str] = mapped_column(String(30), nullable=False)
    sob_demanda: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    usuario_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detalhe: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_price_observations_ponto", "provider", "sku_key", "region"),)


class PriceLifecycleRuleModel(Base):
    """Meia-vida e limiares por tipo de preço; a regra mais específica vence."""

    __tablename__ = "price_lifecycle_rules"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    padrao_tipo: Mapped[str] = mapped_column(String(100), nullable=False)
    padrao_nome: Mapped[str | None] = mapped_column(String(200), nullable=True)
    meia_vida_dias: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    limiar_vivo: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    limiar_maduro: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    limiar_decomposicao: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    ativo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PriceSourceWeightModel(Base):
    """Peso de cada fonte no score e se ela confirma (o agente não confirma)."""

    __tablename__ = "price_source_weights"

    fonte: Mapped[str] = mapped_column(String(20), primary_key=True)
    peso: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confirma: Mapped[bool] = mapped_column(Boolean, nullable=False)
    descricao: Mapped[str | None] = mapped_column(String(300), nullable=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
