from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base


class ExchangeRateModel(Base):
    """Taxa de câmbio por par e data (Precision Engine v3 §3.1).

    ``taxa`` = quantas unidades da moeda de origem valem 1 unidade da moeda
    de destino (BRL/USD = 5.70 → R$5,70 por US$1).
    """

    __tablename__ = "exchange_rates"

    par: Mapped[str] = mapped_column(String(7), primary_key=True)  # 'BRL/USD'
    data: Mapped[date] = mapped_column(Date, primary_key=True)
    taxa: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fonte: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
