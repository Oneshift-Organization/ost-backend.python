"""Tamanho de VM Azure → (vCPU, RAM) pelo esquema de nomenclatura (Wave 3).

A Retail Prices API devolve preço + ``armSkuName`` (ex.: ``Standard_D4s_v5``) mas
não os specs. Como no ``instance_shape`` da AWS, a modelagem é estrutural: o
número após a letra de família é o vCPU, e a letra de família codifica a razão
GiB/vCPU (D=4, E=8 memória, F=2 compute…). Vale para gerações futuras sem
enumerar SKUs.

Função pura, sem I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

# GiB de RAM por vCPU pela LETRA de família Azure.
#   B/A/D/H=geral(4)  F=compute(2)  E/M=memória(8/28)  L=storage(8)  N=GPU(8)
_FAMILY_RAM_PER_VCPU = {
    "a": Decimal("2"),
    "b": Decimal("4"),
    "d": Decimal("4"),
    "h": Decimal("4"),
    "f": Decimal("2"),
    "e": Decimal("8"),
    "m": Decimal("28"),
    "l": Decimal("8"),
    "n": Decimal("8"),
    "g": Decimal("8"),
}
_DEFAULT_RAM_PER_VCPU = Decimal("4")

# Standard_<familia><vcpu>[-<constrained>]<sufixo>[_v<gen>]
#   D4s_v5      -> familia D, vcpu 4
#   E8-4as_v5   -> familia E, vcpu 8, mas -4 = vCPUs realmente utilizáveis
#   F2s_v2      -> familia F, vcpu 2
#   B2ms        -> familia B, vcpu 2
_AZURE_VM = re.compile(
    r"\bStandard_([A-Za-z]+)(\d+)(?:-(\d+))?[a-z]*(?:_v(\d+))?\b",
    re.IGNORECASE,
)
# Nas famílias D/E, o número só é a contagem de vCPU a partir da geração v3.
# Nas v1/v2 (D11_v2 = 2 vCPU, D4_v2 = 8) o número é um índice, não a contagem —
# confiar nele produziria specs-fantasma. B e F sempre foram lineares.
_INDEX_FAMILIES = frozenset({"d", "e", "g"})
_MIN_LINEAR_GENERATION = 3


@dataclass(frozen=True, slots=True)
class AzureShape:
    vcpus: Decimal
    ram_gib: Decimal
    confidence: str = "estimado"


def parse_azure_shape(arm_sku_name: str | None) -> AzureShape | None:
    """Deriva (vCPU, RAM) de um ``armSkuName``, ou None se não for uma VM."""
    if not arm_sku_name:
        return None
    m = _AZURE_VM.search(arm_sku_name)
    if not m:
        return None
    family = m.group(1)[0].lower()
    generation = int(m.group(4)) if m.group(4) else 0
    # D/E/G v1-v2: número é índice, não contagem de vCPU — rejeita para não gerar
    # specs-fantasma (D11_v2 "11" na verdade são 2 vCPU).
    if family in _INDEX_FAMILIES and generation < _MIN_LINEAR_GENERATION:
        return None
    # Tamanho "constrained" (ex.: E8-4as): o vCPU efetivo é o número após o traço.
    vcpus = Decimal(m.group(3) or m.group(2))
    if vcpus <= 0:
        return None
    ram = vcpus * _FAMILY_RAM_PER_VCPU.get(family, _DEFAULT_RAM_PER_VCPU)
    return AzureShape(vcpus=vcpus, ram_gib=ram)


# `Standard_D4s_v5` → `DSV5`: letra de família, recursos e versão, sem o tamanho
_FAMILIA_VM = re.compile(r"^Standard_([A-Za-z]+)\d+(?:-\d+)?([a-z]*)(?:_v(\d+))?", re.IGNORECASE)


def familia_vm(tamanho: str | None) -> str | None:
    """Família de um tamanho de VM da Azure, como os pools da fatura a nomeiam."""
    if not tamanho:
        return None
    m = _FAMILIA_VM.match(tamanho)
    if not m:
        return None
    familia = (m.group(1) + (m.group(2) or "")).upper()
    return f"{familia}V{m.group(3)}" if m.group(3) else familia
