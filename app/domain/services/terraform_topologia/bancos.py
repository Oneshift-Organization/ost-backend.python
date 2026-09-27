"""Banco com o que o inventário sabe e o billing não (RI15.6).

Duas coisas:

- **Versão.** O billing diz o motor, nunca a versão; o inventário diz as duas.
  A versão maior mais frequente vira o ``default`` da variável de versão, no
  formato do destino, quando o provider fixado do destino a aceita.
- **Classe do banco que o billing cobrou sem classe** (Flexible Server da
  Azure): com o inventário, o banco sai com a classe medida em vez do vCPU
  estimado por razão de custo.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.services.azure_shape import parse_azure_shape
from app.domain.services.instance_shape import parse_instance_shape

# O que os providers fixados aceitam. PostgreSQL 17 já existe nas nuvens, mas o
# azurerm 3.x recusa "17" na validação do `version`: o pacote não aplicaria.
_POSTGRES = frozenset({"12", "13", "14", "15", "16"})
_MYSQL = frozenset({"8.0"})

# Tier do Cloud SQL: `db-custom-<vCPU>-<MB>`.
_TIER_GCP = re.compile(r"^db-custom-(\d+)-\d+$")


@dataclass(frozen=True, slots=True)
class VersaoPadrao:
    default: str | None
    maior: str | None
    contagem: dict[str, int]
    motivo: str


def _maior(versao: str, familia: str | None) -> str | None:
    """`15.4` → `15`; MySQL `8.0.35` → `8.0`. Oracle `19c` e SQL Server ficam de fora."""
    numeros: list[str] = re.findall(r"\d+", versao)
    if not numeros:
        return None
    if familia == "postgresql":
        return numeros[0]
    if familia == "mysql" and len(numeros) > 1:
        return f"{numeros[0]}.{numeros[1]}"
    return None


def _chave_numerica(maior: str) -> tuple[int, ...]:
    return tuple(int(n) for n in maior.split("."))


def _no_formato(maior: str, familia: str, destino: str) -> str | None:
    if familia == "postgresql" and maior in _POSTGRES:
        return {"aws": maior, "azure": maior, "gcp": f"POSTGRES_{maior}"}.get(destino)
    if familia == "mysql" and maior in _MYSQL:
        return {"aws": maior, "gcp": "MYSQL_" + maior.replace(".", "_")}.get(destino)
    return None


def versao_padrao(versoes: Mapping[str, int], familia: str | None, destino: str) -> VersaoPadrao:
    """A versão maior mais frequente (empate: a maior), no formato do destino.

    ``default`` nulo quando nada foi medido, quando a versão não é suportada ou
    quando o destino não tem variável de versão (Autonomous Database da OCI).
    """
    por_maior: Counter[str] = Counter()
    for versao, n in versoes.items():
        maior = _maior(versao, familia)
        if maior is not None:
            por_maior[maior] += n
    if not por_maior or familia is None:
        return VersaoPadrao(None, None, {}, "nenhuma versão medida no inventário")
    maior = max(por_maior, key=lambda m: (por_maior[m], _chave_numerica(m)))
    contagem = dict(sorted(por_maior.items(), key=lambda kv: _chave_numerica(kv[0])))
    default = _no_formato(maior, familia, destino)
    if default is None:
        motivo = (
            f"versão {maior} medida no inventário, fora do que o destino aceita neste pacote "
            "(PostgreSQL 12 a 16, MySQL 8.0)"
        )
    else:
        motivo = f"versão {maior} medida no inventário (a mais frequente)"
    return VersaoPadrao(default, maior, contagem, motivo)


def vcpus_da_classe(classe: str | None) -> int | None:
    """vCPU de uma classe de banco medida: RDS, Azure ou Cloud SQL."""
    if not classe:
        return None
    forma = parse_instance_shape(classe) or parse_azure_shape(classe)
    if forma is not None:
        return int(forma.vcpus)
    m = _TIER_GCP.match(classe)
    return int(m.group(1)) if m else None


@dataclass(frozen=True, slots=True)
class BancoSemGrupo:
    """Bancos do inventário que o billing cobrou sem classe, agrupados."""

    classe: str
    motor: str | None
    versao: str | None
    alta_disponibilidade: bool
    quantidade: int
    vcpus: int
    subredes: tuple[str, ...] = ()
    grupos: tuple[str, ...] = ()


def agrupar_sem_grupo(bancos: Iterable[Mapping[str, object]]) -> tuple[list[BancoSemGrupo], int]:
    """Agrupa por (classe, motor, versão, HA). Devolve também quantos ficaram
    de fora porque a classe não diz o vCPU (Autonomous Database da OCI)."""
    grupos: Counter[tuple[str, str | None, str | None, bool]] = Counter()
    subredes: dict[tuple[str, str | None, str | None, bool], set[str]] = {}
    seguranca: dict[tuple[str, str | None, str | None, bool], set[str]] = {}
    vcpus: dict[str, int] = {}
    sem_vcpu = 0
    for b in bancos:
        classe = str(b.get("classe") or "")
        v = vcpus_da_classe(classe)
        if v is None:
            sem_vcpu += 1
            continue
        vcpus[classe] = v
        motor = str(b["motor"]) if b.get("motor") else None
        versao = str(b["versao"]) if b.get("versao") else None
        chave = (classe, motor, versao, bool(b.get("alta_disponibilidade")))
        grupos[chave] += 1
        subredes.setdefault(chave, set()).update(_textos(b.get("subredes")))
        seguranca.setdefault(chave, set()).update(_textos(b.get("grupos")))
    saida = [
        BancoSemGrupo(
            *chave,
            n,
            vcpus[chave[0]],
            tuple(sorted(subredes[chave])),
            tuple(sorted(seguranca[chave])),
        )
        for chave, n in sorted(
            grupos.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), str(kv[0][2]), kv[0][3])
        )
    ]
    return saida, sem_vcpu


def _textos(valor: object) -> list[str]:
    return [str(x) for x in valor] if isinstance(valor, list | tuple) else []
