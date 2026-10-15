"""Custo de categoria (disco, objeto, egress, banco) separado por zona.

P1 do PLANO_AUTORIDADE_V3_E_PRECISAO §3.

Antes, cada categoria era cotada inteira na **zona dominante** do bill: o disco de
uma conta com 3,5% do compute em São Paulo saía todo cotado no meio-oeste. Aqui a
categoria vira partes, uma por zona da região de cada linha; quem cota aplica a taxa
da zona de cada parte e soma.

Linha sem região (ou de região fora da tabela de zonas) vai para a zona dominante, e
a parte diz que foi assim: a fração declarada, não escondida.

Módulo puro, sem I/O.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.services.regioes import normalizar_regiao, zona_da_regiao

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class ParteDaZona:
    zona: str
    # A região da origem nessa zona (a de mais custo), para a taxa da própria origem
    regiao_origem: str | None
    custo: Decimal
    fracao: Decimal
    # Custo que caiu aqui por não ter região na linha
    sem_regiao: Decimal = _ZERO


def dividir_por_zona(
    provedor: str,
    por_regiao: Mapping[str, Decimal],
    *,
    zona_dominante: str,
    regiao_dominante: str | None,
) -> list[ParteDaZona]:
    """Partes por zona, a de mais custo primeiro. ``por_regiao``: região → custo; a
    chave ``""`` é o custo sem região."""
    total = sum((c for c in por_regiao.values() if c > 0), _ZERO)
    if total <= 0:
        return []
    custo: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    sem_regiao: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    regioes: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
    for regiao, valor in por_regiao.items():
        if valor <= 0:
            continue
        zona = zona_da_regiao(provedor, regiao) if regiao else None
        if zona is None:
            zona = zona_dominante
            sem_regiao[zona] += valor
        else:
            regioes[zona][normalizar_regiao(provedor, regiao) or regiao] += valor
        custo[zona] += valor
    partes = []
    for zona, valor in custo.items():
        da_zona = regioes.get(zona)
        regiao = (
            max(da_zona.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if da_zona
            else (regiao_dominante if zona == zona_dominante else None)
        )
        partes.append(
            ParteDaZona(
                zona=zona,
                regiao_origem=regiao,
                custo=valor,
                fracao=valor / total,
                sem_regiao=sem_regiao.get(zona, _ZERO),
            )
        )
    return sorted(partes, key=lambda p: (-p.custo, p.zona))
