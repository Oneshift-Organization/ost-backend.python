"""Expansão de instance-hours → vCPU/RAM pelo esquema de nomenclatura (Wave 2).

Alguns provedores (AWS Cost Explorer) faturam compute por *instância-hora*:
uma linha ``BoxUsage:m7g.2xlarge`` com ``usage_amount`` em horas de instância.
Tratar isso como vCPU-hora subdimensiona o pool pelo número de vCPUs da máquina
(m7g.2xlarge = 8 vCPU → erro de 8x). Outros (GCP Cost Table) já faturam por
vCPU-hora e RAM-hora em linhas separadas — esses não têm shape e não são tocados.

A modelagem é **estrutural**, não uma tabela por instância: o *tamanho*
(``2xlarge``) codifica o vCPU e a *letra de classe* (``c``/``m``/``r``…) codifica
a razão GiB/vCPU. É o esquema de nomenclatura da AWS, estável por gerações — vale
para ``m7g``, ``c8g`` ou qualquer família futura sem enumerá-las. O resultado é
uma **estimativa** (``confidence="estimado"``); a camada de reconciliação depois
revalida o preço/vCPU e mantém a suspeita se a estimativa não fechar.

Função pura, sem I/O. Ver ``docs/refactor/precision-engine-v3-spec.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

# vCPU por tamanho. Sub-'large' é achatado em 2 (baseline burstable + famílias
# m/c/r que começam em 'large'); 'xlarge'=4 e daí 'Nxlarge' = N*4 — a regra que
# cobre o grosso do gasto. Erros ficam nos tamanhos minúsculos (custo desprezível).
_SIZE_VCPU = {
    "nano": Decimal("2"),
    "micro": Decimal("2"),
    "small": Decimal("2"),
    "medium": Decimal("2"),
    "large": Decimal("2"),
    "xlarge": Decimal("4"),
}
_XLARGE_STEP = Decimal("4")

# GiB de RAM por vCPU, pela LETRA de classe da família:
#   c=compute(2)  m/a/t/h/n=geral(4)  r/z/g/p/f/v=memória/acel(8)  x/u=alta-mem
#   i/d=storage(6). Cobre o encoding; famílias exóticas caem no default.
_CLASS_RAM_PER_VCPU = {
    "t": Decimal("4"),
    "m": Decimal("4"),
    "a": Decimal("4"),
    "h": Decimal("4"),
    "n": Decimal("4"),
    "c": Decimal("2"),
    "r": Decimal("8"),
    "z": Decimal("8"),
    "g": Decimal("8"),
    "p": Decimal("8"),
    "f": Decimal("8"),
    "v": Decimal("8"),
    "x": Decimal("16"),
    "u": Decimal("24"),
    "i": Decimal("6"),
    "d": Decimal("6"),
}
_DEFAULT_RAM_PER_VCPU = Decimal("4")

# <familia>.<tamanho> — família = <letras><dígito><sufixo>, tamanho = [N]sizeword.
# O separador '.' é o que distingue o padrão AWS do 'e2-standard-4' do GCP (que
# não casa e portanto não é expandido).
_SHAPE = re.compile(
    r"\b([a-z]{1,4})\d[a-z]{0,3}\.(\d*)(nano|micro|small|medium|large|xlarge)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class InstanceShape:
    vcpus: Decimal
    ram_gib: Decimal
    confidence: str = "estimado"


def parse_instance_shape(text: str | None) -> InstanceShape | None:
    """Deriva (vCPU, RAM) de um identificador de instância, ou None.

    None quando não há shape ``<familia>.<tamanho>`` no texto — sinal de que a
    linha já é por-unidade (ex.: 'N2 Instance Core' do GCP) e não deve ser
    expandida."""
    if not text:
        return None
    m = _SHAPE.search(text)
    if not m:
        return None

    class_letter = m.group(1)[0].lower()
    mult, size = m.group(2), m.group(3).lower()

    if size == "xlarge":
        n = Decimal(mult) if mult else Decimal("1")
        vcpus = n * _XLARGE_STEP
    else:
        vcpus = _SIZE_VCPU.get(size, Decimal("2"))

    ram = vcpus * _CLASS_RAM_PER_VCPU.get(class_letter, _DEFAULT_RAM_PER_VCPU)
    return InstanceShape(vcpus=vcpus, ram_gib=ram)
