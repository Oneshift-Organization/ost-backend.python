"""Calculadoras por categoria — sizing cross-cloud de custos não-compute (Wave 4).

Storage, egress e afins não têm hardware para dimensionar; têm um custo por
unidade (GB-mês, GB). O `usage_amount`/`usage_unit` dos parsers é inconsistente
(vazio na AWS, escalas variadas), mas o **custo** é confiável (conservação
validada ao centavo). Então reprecificamos por RAZÃO DE TAXA:

    custo_alvo = custo_origem x (taxa_alvo / taxa_origem)

O GB implícito se cancela — só precisamos de uma pequena tabela de taxas por
provedor (referência/oficial, com confiança). Provider-agnóstico: nenhuma regra
por nuvem, só aritmética de taxas.

Função pura, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CategoryRate:
    """Taxa unitária de uma categoria num provedor (ex.: $/GB-mês de SSD)."""

    provider: str
    rate: Decimal
    unit: str            # 'gb_month' | 'gb'
    confidence: str = "reference"


@dataclass(frozen=True, slots=True)
class CategoryEquivalent:
    provider: str
    estimated_monthly_cost: Decimal
    cost_delta: Decimal
    pricing_confidence: str


def estimate_by_cost_ratio(
    *,
    source_provider: str,
    source_cost: Decimal,
    rates: dict[str, CategoryRate],
) -> list[CategoryEquivalent]:
    """Reprecifica ``source_cost`` em cada provedor-alvo pela razão de taxas.

    Retorna vazio se não há taxa para o provedor de ORIGEM (sem ela não dá para
    inferir a quantidade). Alvos sem taxa são simplesmente omitidos.
    """
    src = rates.get(source_provider.lower())
    if src is None or src.rate <= 0:
        return []

    out: list[CategoryEquivalent] = []
    for provider, rate in rates.items():
        if provider == source_provider.lower():
            continue
        if rate.rate <= 0 or rate.unit != src.unit:
            continue
        target_cost = source_cost * (rate.rate / src.rate)
        out.append(
            CategoryEquivalent(
                provider=provider,
                estimated_monthly_cost=target_cost,
                cost_delta=target_cost - source_cost,
                # A confiança é a do elo mais fraco: origem e alvo.
                pricing_confidence=_weakest(src.confidence, rate.confidence),
            )
        )
    return out


_CONFIDENCE_ORDER = {"official": 3, "reference": 2, "low": 1}


def _weakest(a: str, b: str) -> str:
    return a if _CONFIDENCE_ORDER.get(a, 0) <= _CONFIDENCE_ORDER.get(b, 0) else b
