"""Conversão monetária determinística (Precision Engine v3 — Pilar A1).

Regra inegociável: nunca comparar custos em moedas diferentes. Todo
``cost_delta`` é calculado depois de normalizar origem e destino para a
**moeda de análise**. A taxa usada e sua origem viajam no ledger ``premissas``.

Ver ``docs/precision-engine.md`` §3.1 e §4.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

# Moeda em que o catálogo de preços está expresso e em que as comparações
# são feitas. Mudar isto exige reconverter o catálogo inteiro.
ANALYSIS_CURRENCY = "USD"

# `fonte` quando não há taxa alguma para o par. A taxa vira 1 — não porque as
# moedas valham o mesmo, mas porque não há número honesto a aplicar. Este rótulo
# é o que impede a identidade de passar como conversão: quem lê o ledger vê que
# a comparação está entre moedas diferentes.
SEM_TAXA = "SEM_TAXA_CAMBIO"

# `fonte` do fallback de ambiente: um número no env, sem data e sem quem o
# atualize.
FONTE_CONFIG = "config"


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

    @property
    def sem_taxa(self) -> bool:
        """Não existe taxa para o par — o valor 1 é ausência, não conversão."""
        return self.fonte == SEM_TAXA

    def idade_em_dias(self, hoje: date) -> int | None:
        """Quantos dias desde a cotação. ``None`` quando a taxa não tem data.

        Sem data não há como distinguir uma cotação de ontem de uma de 2023, e
        `data: null` é exatamente o estado do fallback de ambiente hoje.
        """
        if self.data is None:
            return None
        return (hoje - self.data).days


def identity_rate(currency: str) -> FxRate:
    """Taxa neutra — usada quando origem e análise já são a mesma moeda."""
    return FxRate(par=f"{currency}/{currency}", taxa=Decimal("1"), fonte="identity")


def taxa_desconhecida(par: str) -> FxRate:
    """Sem taxa conhecida para o par.

    Devolve identidade **rotulada**. Converter por 1 compara real com dólar como
    se fossem a mesma coisa; o rótulo é o que permite a camada de cima rebaixar
    a confiança e avisar na tela em vez de deixar o número sair plausível.
    """
    return FxRate(par=par, taxa=Decimal("1"), fonte=SEM_TAXA)


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
