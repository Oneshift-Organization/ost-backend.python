"""Onde ficam as VMs aprovadas de cada pool (RI15.5).

O pool continua com o shape e a quantidade `M` que a análise aprovou; o que a
topologia decide é **onde** elas ficam. `M` é repartido entre as subredes de
origem pelo vCPU de cada uma:

- com `M` menor que o número de subredes, as `M` maiores recebem uma VM cada;
- senão, cada subrede recebe uma (preserva zona e rede de origem) e o resto vai
  pelo **maior resto** (Hamilton), com empate pelo peso e depois pela chave.

As instâncias do pool que não estão no inventário formam a fatia "sem
posicionamento", com peso = quantidade vezes o vCPU médio das posicionadas.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

SEM_POSICIONAMENTO = ""


@dataclass(frozen=True, slots=True)
class Fatia:
    """Uma subrede de origem de um pool, e quantas VMs recebe no destino."""

    subrede: str  # chave da subrede de origem; vazia = sem posicionamento
    peso: float
    instancias_origem: int
    grupos: tuple[str, ...] = ()
    conjuntos_de_grupos: int = 1
    disco_boot_gb: int | None = None
    vms: int = 0


def fatias_do_pool(posicionamento: Mapping[str, Any]) -> list[Fatia]:
    fatias = [
        Fatia(
            subrede=str(p.get("subrede") or SEM_POSICIONAMENTO),
            peso=float(p.get("vcpus") or 0) or float(p.get("instancias") or 0),
            instancias_origem=int(p.get("instancias") or 0),
            grupos=tuple(p.get("grupos") or ()),
            conjuntos_de_grupos=int(p.get("conjuntos_de_grupos") or 1),
            disco_boot_gb=p.get("disco_boot_gb"),
        )
        for p in posicionamento.get("posicionamentos") or []
        if p.get("subrede")
    ]
    sem = int(posicionamento.get("sem_posicionamento") or 0)
    posicionadas = sum(f.instancias_origem for f in fatias)
    if sem:
        medio = sum(f.peso for f in fatias) / posicionadas if posicionadas else 1.0
        fatias.append(Fatia(SEM_POSICIONAMENTO, sem * medio, sem))
    return fatias


def repartir(total: int, fatias: Sequence[Fatia]) -> list[Fatia]:
    """Devolve as mesmas fatias com ``vms`` preenchido; a soma é ``total``."""
    if total <= 0 or not fatias:
        return [replace(f, vms=0) for f in fatias]
    ordem = sorted(range(len(fatias)), key=lambda i: (-fatias[i].peso, fatias[i].subrede))
    if total < len(fatias):
        escolhidas = set(ordem[:total])
        return [replace(f, vms=1 if i in escolhidas else 0) for i, f in enumerate(fatias)]
    resto_total = total - len(fatias)
    pesos = [f.peso for f in fatias]
    if sum(pesos) <= 0:
        pesos = [1.0] * len(fatias)  # sem vCPU conhecido: divide igual
    cotas = [resto_total * w / sum(pesos) for w in pesos]
    base = [math.floor(c) for c in cotas]
    faltam = resto_total - sum(base)
    por_resto = sorted(
        range(len(fatias)),
        key=lambda i: (-(cotas[i] - base[i]), -fatias[i].peso, fatias[i].subrede),
    )
    for i in por_resto[:faltam]:
        base[i] += 1
    return [replace(f, vms=1 + base[i]) for i, f in enumerate(fatias)]
