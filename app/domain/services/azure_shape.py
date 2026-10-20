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
    r"\bStandard_([A-Za-z]+)(\d+)(?:-(\d+))?([a-z]*)(?:_v(\d+))?\b",
    re.IGNORECASE,
)
# O sufixo muda a RAM dentro da família (PLANO_PRECISAO_OCI P1.4): "l" é memória
# reduzida, 2 GiB por vCPU (Dls, Dpls, Bls); na série B v2, "t" é 0,5 GiB por vCPU
# (B2ts_v2 e B2pts_v2 têm 1 GiB). Na B v1, "m" é 4 GiB por vCPU e, sem ele, 2 (B2s).
_RAM_REDUZIDA = Decimal("2")
_RAM_DO_T = Decimal("0.5")
_FAMILIAS_COM_L = frozenset({"b", "d"})
# A série B v2 (Bsv2, Bpsv2, Basv2) trouxe o "t"; a v1 tem o "m"
_B_V2 = 2
# Nas famílias D/E, o número só é a contagem de vCPU a partir da geração v3.
# Nas v1/v2 (D11_v2 = 2 vCPU, D4_v2 = 8) o número é um índice, não a contagem —
# confiar nele produziria specs-fantasma. B e F sempre foram lineares.
_INDEX_FAMILIES = frozenset({"d", "e", "g"})
_MIN_LINEAR_GENERATION = 3
# A B v1 de 1 vCPU foge da regra (tabela oficial da série B): a RAM total pelo sufixo.
# A regra dava 2, 2 e 4 GiB, e o empacotador punha 9 B1ls num pool de 17,5 GiB (D1)
_B_V1_DE_UMA_VCPU = {"ls": Decimal("0.5"), "s": Decimal("1"), "ms": Decimal("2")}


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
    sufixo = m.group(4).lower()
    generation = int(m.group(5)) if m.group(5) else 0
    # D/E/G v1-v2: número é índice, não contagem de vCPU — rejeita para não gerar
    # specs-fantasma (D11_v2 "11" na verdade são 2 vCPU).
    if family in _INDEX_FAMILIES and generation < _MIN_LINEAR_GENERATION:
        return None
    # Tamanho "constrained" (ex.: E8-4as): o vCPU efetivo é o número após o traço.
    vcpus = Decimal(m.group(3) or m.group(2))
    if vcpus <= 0:
        return None
    if family == "b" and generation < _B_V2 and vcpus == 1 and sufixo in _B_V1_DE_UMA_VCPU:
        return AzureShape(vcpus=vcpus, ram_gib=_B_V1_DE_UMA_VCPU[sufixo])
    ram = vcpus * _ram_por_vcpu(family, sufixo, generation)
    return AzureShape(vcpus=vcpus, ram_gib=ram)


def _ram_por_vcpu(family: str, sufixo: str, generation: int) -> Decimal:
    if family == "b" and generation >= _B_V2 and "t" in sufixo:
        return _RAM_DO_T
    if family in _FAMILIAS_COM_L and "l" in sufixo:
        return _RAM_REDUZIDA
    if family == "b" and generation < _B_V2 and "m" not in sufixo:
        return _RAM_REDUZIDA
    return _FAMILY_RAM_PER_VCPU.get(family, _DEFAULT_RAM_PER_VCPU)


# Nome do medidor da Azure (`Virtual Machines | Edsv5 Series | E16ds v5`): o último
# pedaço é o tamanho, com espaço antes da geração e o modo de cobrança no fim
_MEDIDOR = re.compile(
    r"^([A-Z][A-Za-z]*\d+(?:-\d+)?[a-z]*)(?:\s+v(\d+))?(?:\s+(?:Spot|Low Priority))?$"
)


def tamanho_do_medidor(medidor: str | None) -> str | None:
    """`... | E16ds v5` → `Standard_E16ds_v5`; None se o último pedaço não for um tamanho.

    A exportação da Azure sem o SKU traz a VM só pelo medidor (item 10 do
    PROXIMAS_EXECUCOES): sem isto a hora da VM ficava sem vCPU nem família."""
    if not medidor:
        return None
    m = _MEDIDOR.match(medidor.rsplit("|", 1)[-1].strip())
    if not m:
        return None
    return f"Standard_{m.group(1)}" + (f"_v{m.group(2)}" if m.group(2) else "")


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
