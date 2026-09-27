"""Discos com o inventário: boot vira disco raiz, sem anexo sai (RI15.7).

O billing mede o disco de boot como um volume qualquer (no CUR, o volume raiz é
EBS comum). Sem o inventário, o pacote criava as VMs com o disco raiz padrão da
imagem **e** um volume avulso do mesmo tamanho: o disco saía duas vezes. Com o
inventário, o boot vai para o disco raiz da VM, o disco sem anexo (ocioso) não é
gerado, e os volumes avulsos são o que sobra.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Avulsos:
    """Volumes avulsos de uma categoria, e o que deixou de ser criado."""

    gb: int
    volumes: int
    gb_boot: int
    volumes_boot: int
    gb_sem_anexo: int
    volumes_sem_anexo: int

    @property
    def por_volume(self) -> int:
        return math.ceil(self.gb / self.volumes) if self.volumes > 0 else 0


def _inteiro(valor: object) -> int:
    return int(valor) if isinstance(valor, int | float) and not isinstance(valor, bool) else 0


def avulsos(medido: Mapping[str, Any], discos: Mapping[str, Any], chave: str) -> Avulsos | None:
    """Medido no billing menos boot e sem anexo, por categoria, nunca abaixo de zero.

    ``None`` sem medição: no caminho agregado o GB é estimado pelo custo, e tirar
    dele um GB medido misturaria as duas bases.
    """
    fato = medido.get(chave) or {}
    if "gb" not in fato:
        return None
    boot = (discos.get("boot") or {}).get(chave) or {}
    solto = (discos.get("sem_anexo") or {}).get(chave) or {}
    gb_boot, vol_boot = _inteiro(boot.get("gb")), _inteiro(boot.get("volumes"))
    gb_solto, vol_solto = _inteiro(solto.get("gb")), _inteiro(solto.get("volumes"))
    gb = max(0, round(fato["gb"]) - gb_boot - gb_solto)
    volumes = max(0, _inteiro(fato.get("recursos")) - vol_boot - vol_solto)
    if gb == 0 or volumes == 0:
        gb, volumes = 0, 0
    return Avulsos(gb, volumes, gb_boot, vol_boot, gb_solto, vol_solto)


def boot_medio(posicionamentos: Iterable[Mapping[str, Any]]) -> tuple[int, int] | None:
    """(GB, instâncias com boot conhecido): média ponderada pelas instâncias,
    arredondada para cima (disco raiz menor que o de origem não cabe o sistema)."""
    soma: float = 0
    n = 0
    for p in posicionamentos:
        gb = p.get("disco_boot_gb")
        if isinstance(gb, int | float) and gb > 0:
            soma += gb * int(p.get("instancias") or 0)
            n += int(p.get("instancias") or 0)
    return (math.ceil(soma / n), n) if n else None
