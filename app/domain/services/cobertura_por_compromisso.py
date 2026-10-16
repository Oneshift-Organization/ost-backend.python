"""Pools com capacidade real e custo zero — coberto por compromisso.

## O que acontece

Um bill da AWS com Savings Plan traz as duas coisas separadas::

    BoxUsage:c5a.large        5.701,6 horas    $0,00
    ComputeSP:3yrNoUpfront                  $8.184,00

As instâncias rodaram — a capacidade é real. O custo também é real, só que
faturado na linha do compromisso, não na da instância.

O agregador segrega o compromisso de propósito, porque somá-lo ao pool
explodiria o preço por vCPU (custo sem a capacidade correspondente). Só que o
efeito colateral é o inverso: o pool fica com **capacidade e custo zero**.

## Por que isso importa

`cost_delta = custo_estimado_no_destino - custo_na_origem`. Com a origem em
zero, o delta vira o custo inteiro do destino, e a análise afirma que migrar
aquele pool **adiciona** gasto — quando na verdade o cliente já paga por ele,
via compromisso.

No bill AWS versionado isso atinge 3 dos 20 pools. Não é caso de borda: quem tem
Savings Plan é justamente quem tem escala para pensar em migrar.

## O que este módulo NÃO faz

Não rateia o compromisso entre os pools. `ComputeSP` é fungível entre famílias e
`EC2SP:r7g` é específico de uma; decidir quanto de cada um cobre qual pool é
modelo, não fato. Inventar esse número aqui seria trocar um erro visível por um
plausível.

O que ele faz é **declarar**: estes pools têm capacidade sem custo próprio,
existe este tanto de compromisso no bill, e a comparação de economia para eles
não é confiável.

Módulo puro — entra dataclass, sai dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.services.azure_shape import familia_vm
from app.domain.services.sku_matcher import extract_family

# Pool que paga menos de 60% do próprio preço sob demanda: o compromisso cobre parte
# das horas dele (V4.6). Abaixo disso, a diferença pode ser só região ou desconto.
LIMIAR_DE_COBERTURA = Decimal("0.4")


def familia_do_ponto(provedor: str, sku: str) -> str | None:
    """Família de um ponto de preço como os pools da fatura a nomeiam (C7G, E2, E4, DSV5)."""
    if provedor.lower() == "azure":
        return familia_vm(sku)
    return extract_family(sku)


def cobertura_estimada(custo_vcpu_hora: Decimal, sob_demanda_vcpu_hora: Decimal) -> Decimal | None:
    """Quanto do preço sob demanda o pool não pagou na própria linha (0 a 1).

    É o caso do C7G da AWS real: 0,004 a 0,018 por vCPU-hora contra 0,036 a 0,054 sob
    demanda, porque o Savings Plan paga o resto na linha dele. None sem preço de
    referência."""
    if sob_demanda_vcpu_hora <= 0 or custo_vcpu_hora < 0:
        return None
    return max(Decimal(0), Decimal(1) - custo_vcpu_hora / sob_demanda_vcpu_hora)


class _PoolLike(Protocol):
    """O mínimo que precisamos de um pool, para não acoplar ao tipo concreto."""

    @property
    def monthly_cost(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class CoberturaPorCompromisso:
    """Diagnóstico da fatia de capacidade sem custo próprio."""

    pools: tuple[str, ...]
    vcpus_sem_custo: Decimal
    vcpus_total: Decimal
    compromisso: Decimal

    @property
    def existe(self) -> bool:
        return bool(self.pools)

    @property
    def fracao_da_capacidade(self) -> Decimal:
        if self.vcpus_total <= 0:
            return Decimal("0")
        return self.vcpus_sem_custo / self.vcpus_total

    def as_ledger(self) -> dict[str, object]:
        """Vai para `premissas`. Aditivo — nenhum consumidor atual quebra.

        A `explicacao` viaja junto porque o número sozinho não diz o que fazer:
        quem lê precisa saber que a economia daqueles pools está superestimada,
        e por quê.
        """
        return {
            "pools": list(self.pools),
            "vcpus_sem_custo": round(float(self.vcpus_sem_custo), 2),
            "fracao_da_capacidade": round(float(self.fracao_da_capacidade), 4),
            "compromisso_no_bill": round(float(self.compromisso), 2),
            "explicacao": (
                "Estes pools têm capacidade medida e custo zero na origem, o que "
                "acontece quando as instâncias são cobertas por compromisso "
                "(Savings Plan, Reserved Instance, CUD). O custo existe, mas é "
                "faturado na linha do compromisso. A economia calculada para "
                "eles está SUPERESTIMADA: o comparativo assume origem de custo "
                "zero. Não rateamos o compromisso entre pools porque a "
                "atribuição depende do tipo de plano e seria estimativa "
                "apresentada como fato."
            ),
        }


def avaliar_cobertura(
    pools_por_chave: dict[str, tuple[Decimal, Decimal]],
    compromisso: Decimal,
) -> CoberturaPorCompromisso:
    """``{chave do pool: (vcpus, custo mensal)}`` → diagnóstico.

    Só conta como "sem custo" o pool que tem capacidade **e** custo zerado.
    Capacidade zero com custo zero é pool vazio, que a agregação já descarta, e
    incluí-lo aqui encheria o alerta de ruído.
    """
    sem_custo = tuple(
        sorted(
            chave for chave, (vcpus, custo) in pools_por_chave.items() if vcpus > 0 and custo <= 0
        )
    )
    return CoberturaPorCompromisso(
        pools=sem_custo,
        vcpus_sem_custo=sum((pools_por_chave[c][0] for c in sem_custo), Decimal("0")),
        vcpus_total=sum((v for v, _ in pools_por_chave.values()), Decimal("0")),
        compromisso=compromisso,
    )
