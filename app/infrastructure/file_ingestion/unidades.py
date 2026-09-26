"""Unidade canônica de quantidade (RN5 do PLANO_CUR_BIGQUERY).

Cada provedor escreve a mesma unidade de um jeito: `Hrs` (AWS), `hour` (GCP),
`1 Hour` e `10 Hours` (Azure), `OCPU Hours` (OCI). O agregador reconhece hora e
GiB-hora; se o parser entregar a grafia do provedor, a capacidade simplesmente
não acumula — sem erro, só um pool vazio e o custo indo para o balde errado.

Então o parser normaliza **aqui**, e o agregador continua sem saber de nuvem.
O multiplicador embutido na unidade (`10 Hours`) vai para a quantidade.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

HORAS = "hours"
GIB_HORA = "gibibyte hour"
GIB_MES = "gibibyte month"
GIB = "gibibyte"

_CANONICAS: dict[str, str] = {
    "h": HORAS,
    "hr": HORAS,
    "hrs": HORAS,
    "hour": HORAS,
    "hours": HORAS,
    "gb-mo": GIB_MES,
    "gb-month": GIB_MES,
    "gib-mo": GIB_MES,
    "gb/month": GIB_MES,
    "gib/month": GIB_MES,
    "gb month": GIB_MES,
    "gibibyte month": GIB_MES,
    "gibibyte-month": GIB_MES,
    "gb-hours": GIB_HORA,
    "gb hours": GIB_HORA,
    "gb-hrs": GIB_HORA,
    "gib hours": GIB_HORA,
    "gibibyte hour": GIB_HORA,
    "gb": GIB,
    "gib": GIB,
    "gibibyte": GIB,
}

# "10 Hours", "1 GB/Month", "100 Hours": número na frente da unidade.
_COM_MULTIPLICADOR = re.compile(r"^\s*([\d.,]+)\s*[kK]?\s+(.+?)\s*$")


def normalizar_unidade(bruta: str | None) -> tuple[str | None, Decimal]:
    """Unidade do provedor → (unidade canônica, multiplicador da quantidade).

    Unidade desconhecida volta como veio, com multiplicador 1: melhor preservar
    a grafia original do que inventar uma equivalência.
    """
    if bruta is None:
        return None, Decimal("1")
    texto = str(bruta).strip()
    if not texto:
        return None, Decimal("1")

    multiplicador = Decimal("1")
    m = _COM_MULTIPLICADOR.match(texto)
    if m:
        try:
            multiplicador = Decimal(m.group(1).replace(",", ""))
            texto = m.group(2)
        except InvalidOperation:
            multiplicador = Decimal("1")
        if multiplicador <= 0:
            multiplicador = Decimal("1")

    return _CANONICAS.get(texto.casefold(), texto), multiplicador
