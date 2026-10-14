"""Qual compromisso paga horas de máquina virtual.

O bill com Savings Plan traz o uso e o compromisso em linhas separadas::

    BoxUsage:c7g.2xlarge     40.456 h     US$ 1.275   (US$ 0,03/h; on-demand: 0,29)
    ComputeSP:3yrNoUpfront                US$ 8.184

O pool fica com a capacidade inteira e o custo líquido; o destino é cotado sem
compromisso. Comparar os dois dá 7x no C7G, e o compromisso ainda ficava no
saldo "mantido ao custo atual": o destino pagava as horas on-demand **e** a
taxa do plano.

Esta regra diz quais linhas de compromisso pagam máquina virtual, para a
consolidação tirá-las da origem junto com os pools. Não rateia entre pools: o
Compute Savings Plan é fungível entre famílias, e decidir quanto cobre cada um
seria modelo, não fato. Reserva de banco (`HeavyUsage:db.*`) e de cache paga
outro serviço e fica de fora.

A regra é dado (padrão → sim/não), a primeira que casa vence. Função pura.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase


@dataclass(frozen=True, slots=True)
class RegraDeCompromisso:
    campo: str  # usage_type | servico
    padrao: str  # glob, comparado em minúsculas
    paga_maquina_virtual: bool


_R = RegraDeCompromisso

REGRAS: tuple[RegraDeCompromisso, ...] = (
    # Reservas de outros serviços gerenciados da AWS (`HeavyUsage:db.r8g.xl`).
    _R("usage_type", "*heavyusage:db.*", False),
    _R("usage_type", "*heavyusage:cache.*", False),
    _R("usage_type", "*heavyusage:es.*", False),
    _R("usage_type", "*heavyusage:dax.*", False),
    # Savings Plans de compute e de instância EC2, e reserva de EC2.
    _R("usage_type", "*computesp:*", True),
    _R("usage_type", "*ec2sp:*", True),
    _R("usage_type", "*heavyusage:*", True),
    # Committed use discount do GCP por recurso (vCPU, RAM).
    _R("usage_type", "*commitment*cpu*", True),
    _R("usage_type", "*commitment*ram*", True),
    _R("usage_type", "*commitment*core*", True),
    # Reserva de VM da Azure (o serviço vem como "Virtual Machines").
    _R("servico", "virtual machines*", True),
)


def paga_maquina_virtual(
    usage_type: str | None,
    servico: str | None,
    regras: tuple[RegraDeCompromisso, ...] = REGRAS,
) -> bool:
    """A linha de compromisso paga horas de máquina virtual? Sem regra que case, não."""
    valores = {"usage_type": usage_type, "servico": servico}
    for regra in regras:
        valor = valores.get(regra.campo)
        if valor and fnmatchcase(valor.strip().lower(), regra.padrao):
            return regra.paga_maquina_virtual
    return False
