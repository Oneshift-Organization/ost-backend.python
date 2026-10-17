"""Shape flexível da OCI: quantas vCPU vale uma OCPU, e o nome do shape.

Regra oficial (docs.oracle.com, Compute Shapes, lida em 17/10/2026):

- x86 (AMD e Intel, E3/E4/E5/E6, X9): 1 OCPU = 2 vCPU;
- Ampere A1: 1 OCPU = 1 núcleo = 1 vCPU;
- Ampere A2 e A4 (AmpereOne): 1 OCPU = 2 núcleos = 2 vCPU.

A lista de preços confirma pela dupla de valores: A2 a US$ 0,007 por vCPU e US$ 0,014
por OCPU; A1 a US$ 0,01 nos dois. Antes, toda família A valia 1 vCPU por OCPU, e o ARM
da A2/A4 saía cotado pelo dobro (PLANO_PRECISAO_OCI P1.1).

Módulo puro, sem I/O.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

# Família ARM com 1 vCPU por OCPU; as demais valem 2
_UMA_VCPU_POR_OCPU = frozenset({"A1"})
_SHAPE = re.compile(r"^VM\.Standard\.([A-Z]\d+)\.Flex\.(\d+)c(\d+)g$", re.IGNORECASE)
# Família x86 que o Terraform usa quando o SKU cotado não diz qual
FAMILIA_PADRAO = "E4"


def vcpus_por_ocpu(familia: str) -> int:
    return 1 if familia.upper() in _UMA_VCPU_POR_OCPU else 2


def ler_shape(sku: str | None) -> tuple[str, int, int] | None:
    """`VM.Standard.A2.Flex.2c16g` → ("A2", 2 OCPU, 16 GB), ou None."""
    m = _SHAPE.match(sku or "")
    if m is None:
        return None
    return m.group(1).upper(), int(m.group(2)), int(m.group(3))


def ocpus_para(vcpus: Decimal | float | int, familia: str) -> int:
    """OCPU que cobrem ``vcpus`` na família: arredonda para cima, mínimo 1."""
    return max(1, math.ceil(Decimal(str(vcpus)) / vcpus_por_ocpu(familia)))
