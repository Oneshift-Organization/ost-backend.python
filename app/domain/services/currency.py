"""Conversão monetária determinística (Precision Engine v3 — Pilar A1).

Regra inegociável: nunca comparar custos em moedas diferentes. Todo
``cost_delta`` é calculado depois de normalizar origem e destino para a
**moeda de análise**. A taxa usada e sua origem viajam no ledger ``premissas``.

Ver ``docs/refactor/precision-engine-v3-spec.md`` §3.1 e §4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Moeda em que o catálogo de preços está expresso e em que as comparações
# são feitas. Mudar isto exige reconverter o catálogo inteiro.
ANALYSIS_CURRENCY = "USD"


@dataclass(frozen=True, slots=True)
class FxRate:
    """Taxa de câmbio aplicada, com procedência para auditoria."""

    par: str  # 'BRL/USD'
    taxa: Decimal  # quantas unidades da origem valem 1 unidade do destino
    data: date | None = None
    fonte: str = "config"

    @property
    def is_identity(self) -> bool:
        return self.taxa == Decimal("1")


def identity_rate(currency: str) -> FxRate:
    """Taxa neutra — usada quando origem e análise já são a mesma moeda."""
    return FxRate(par=f"{currency}/{currency}", taxa=Decimal("1"), fonte="identity")


def convert(amount: Decimal | None, rate: FxRate) -> Decimal | None:
    """Converte ``amount`` da moeda de origem para a moeda de análise.

    ``rate.taxa`` é expressa como "unidades da origem por 1 unidade de destino"
    (ex.: BRL/USD = 5.70 → R$5,70 valem US$1). Portanto divide-se.
    Retorna ``None`` se não houver valor — nunca devolve número errado.
    """
    if amount is None:
        return None
    if rate.taxa <= 0:
        raise ValueError(f"taxa de câmbio inválida: {rate.taxa}")
    if rate.is_identity:
        return amount
    return amount / rate.taxa


def as_ledger(rate: FxRate) -> dict[str, object]:
    """Serializa a taxa para o bloco ``premissas`` do payload."""
    return {
        "par": rate.par,
        "taxa": str(rate.taxa),
        "data": rate.data.isoformat() if rate.data else None,
        "fonte": rate.fonte,
    }
