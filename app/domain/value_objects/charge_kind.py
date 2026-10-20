"""Natureza de uma linha de billing.

Cada parser de export detalhado (CUR, BigQuery, Azure, OCI) traduz o tipo de
linha do provedor para uma destas naturezas. O que fazer com cada uma é decidido
no enriquecimento e no agregador — **pela natureza, nunca pelo nome da nuvem**.
Tabela de mapeamento por provedor em ``PLANO_CUR_BIGQUERY.md`` (RN2).

Parser legado não preenche natureza (``None``); para ele valem os filtros de
hoje, que olham o ``raw_payload``.
"""

from __future__ import annotations

from enum import StrEnum


class ChargeKind(StrEnum):
    USAGE = "usage"
    # Instrumento financeiro: taxa de Savings Plan/RI/CUD, compra de reserva,
    # compromisso ocioso. Tem custo e NÃO tem capacidade — se entrasse num pool
    # como `core`, as horas do plano virariam vCPU fantasma.
    COMMITMENT = "commitment"
    TAX = "tax"
    CREDIT = "credit"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"
    OTHER = "other"


# Fora da base de análise (RN3). Imposto não muda entre nuvens; crédito
# promocional acaba; reembolso e ajuste não são custo recorrente. Deixá-los na
# base faria um cliente com crédito parecer gastar zero — e toda migração
# pareceria mais cara.
FORA_DA_BASE: frozenset[ChargeKind] = frozenset(
    {ChargeKind.TAX, ChargeKind.CREDIT, ChargeKind.REFUND, ChargeKind.ADJUSTMENT}
)

VALORES: frozenset[str] = frozenset(k.value for k in ChargeKind)


def fora_da_base(natureza: str | None) -> bool:
    """True se a linha não entra no custo analisado.

    ``None`` (parser legado) nunca é excluído aqui: o legado tem os próprios
    filtros, e decidir por ele seria mudar o comportamento travado pela
    caracterização.
    """
    return natureza is not None and natureza in FORA_DA_BASE
