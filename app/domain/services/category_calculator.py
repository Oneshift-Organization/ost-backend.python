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
    unit: str  # 'gb_month' | 'gb'
    confidence: str = "reference"
    # Região da taxa e se ela é de outra região que a pedida (ver
    # `domain/services/regioes.escolher_regiao_de_preco`).
    region: str | None = None
    approximate: bool = False


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
    inferir a quantidade). Alvos sem taxa são simplesmente omitidos; alvo com taxa
    zero é serviço grátis lá (NAT e VPN da OCI) e sai com custo zero.
    """
    src = rates.get(source_provider.lower())
    if src is None or src.rate <= 0:
        return []

    out: list[CategoryEquivalent] = []
    for provider, rate in rates.items():
        if provider == source_provider.lower():
            continue
        if rate.rate < 0 or rate.unit != src.unit:
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


def estimate_by_quantity(
    *,
    source_provider: str,
    source_cost: Decimal,
    quantity: Decimal,
    measured_cost: Decimal,
    effective_price: Decimal,
    rates: dict[str, CategoryRate],
    unit: str,
) -> list[CategoryEquivalent]:
    """Destino pela quantidade medida na fatura x a taxa dele (PLANO_PRECO_NAO_COMPUTE).

    Não precisa da taxa da origem: a quantidade vem das linhas. O custo da parte sem
    quantidade (``source_cost - measured_cost``) vira quantidade pelo preço efetivo
    das linhas medidas da categoria (custo medido / quantidade). Alvo com taxa zero é
    grátis; alvo sem taxa, ou em outra unidade, fica de fora. A confiança é a da taxa
    do destino: a origem é medida.
    """
    if effective_price <= 0:
        return []
    sem_quantidade = max(source_cost - measured_cost, Decimal(0))
    total = quantity + sem_quantidade / effective_price
    out: list[CategoryEquivalent] = []
    for provider, rate in rates.items():
        if provider == source_provider.lower() or rate.rate < 0 or rate.unit != unit:
            continue
        target_cost = total * rate.rate
        out.append(
            CategoryEquivalent(
                provider=provider,
                estimated_monthly_cost=target_cost,
                cost_delta=target_cost - source_cost,
                pricing_confidence=rate.confidence,
            )
        )
    return out


_CONFIDENCE_ORDER = {"official": 3, "reference": 2, "low": 1}


def _weakest(a: str, b: str) -> str:
    return a if _CONFIDENCE_ORDER.get(a, 0) <= _CONFIDENCE_ORDER.get(b, 0) else b
