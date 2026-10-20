"""Perfil de uma família de máquina: arquitetura e se é burstable.

O que motivou o módulo (medido em 12/10/2026, bill AWS real):

- o pool `M7G` (Graviton, ARM) era cotado em OCI `E3` (x86), e o `E2` do GCP
  (x86) em AWS `t4g` (ARM). Trocar de arquitetura é replataforma: recompilar
  imagens, trocar dependências nativas. Não é a mesma carga em outro lugar;
- `IS4GEN`, `C4` e `R8G` eram cotados em Azure `Standard_B2pts_v2`, burstable
  de 2 vCPU e 1 GiB, a US$ 6/mês por instância. Burstable tem CPU de linha de
  base com crédito; para carga sustentada, o preço é atraente e a capacidade,
  não.

O nome diz os dois, e cada provedor escreve de um jeito. A tabela abaixo é a
única parte que conhece provedor; o packer só pergunta se dois perfis são
compatíveis.

Função pura, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class Arquitetura(StrEnum):
    X86 = "x86"
    ARM = "arm"


@dataclass(frozen=True, slots=True)
class Perfil:
    arquitetura: Arquitetura
    burstable: bool = False
    # Fração da vCPU nominal que o burstable entrega sem crédito (linha de base).
    # Em destino sem burstable, a carga é dimensionada por ela, não pela nominal
    # (P1b, PLANO_AUTORIDADE_V3_E_PRECISAO §3). None: não sabemos, não reduz.
    base_cpu: Decimal | None = None


# Linha de base por vCPU das famílias T da AWS (t3, t3a, t4g; documentação de
# instâncias burstable). Sem o tamanho (pool da família), a maior das usuais:
# reduzir menos do que devia é o erro seguro.
_BASE_AWS_T = {
    "nano": Decimal("0.05"),
    "micro": Decimal("0.10"),
    "small": Decimal("0.20"),
    "medium": Decimal("0.20"),
    "large": Decimal("0.30"),
    "xlarge": Decimal("0.40"),
    "2xlarge": Decimal("0.40"),
}
_BASE_AWS_PADRAO = Decimal("0.40")
# Série B da Azure (documentação de tamanhos Bv1 e Bsv2, escala 0-100% desde 2024:
# a base da VM nessa escala é a fração de cada vCPU). Bv1 por tamanho; sem o tamanho
# (pool da família), a maior base dela.
_BASE_AZURE_V1 = {
    "b1ls": Decimal("0.05"),
    "b1s": Decimal("0.10"),
    "b1ms": Decimal("0.20"),
    "b2s": Decimal("0.20"),
    "b2ms": Decimal("0.30"),
    "b4ms": Decimal("0.225"),
    "b8ms": Decimal("0.17"),
    "b12ms": Decimal("0.17"),
    "b16ms": Decimal("0.17"),
    "b20ms": Decimal("0.17"),
    "bls": Decimal("0.05"),
    "bs": Decimal("0.20"),
    "bms": Decimal("0.30"),
}
# Bsv2, Basv2 e Bpsv2 pela letra do tamanho: `ts` 20%, `ls` 30%, `s` 40%. A primeira
# letra que aparece vale.
_BASE_AZURE_V2 = (("t", Decimal("0.20")), ("l", Decimal("0.30")), ("", Decimal("0.40")))
# Núcleo compartilhado do GCP: a fração da vCPU que a máquina tem garantida
_BASE_GCP = {
    "e2-micro": Decimal("0.125"),
    "e2-small": Decimal("0.25"),
    "e2-medium": Decimal("0.50"),
    "f1-micro": Decimal("0.20"),
    "g1-small": Decimal("0.50"),
}


# `m7g.2xlarge`, `M7G`, `t3a.micro`, `is4gen.xlarge`, `m7i-flex.large`
_AWS = re.compile(r"^([a-z]+)(\d+)([a-z-]*)")
# `Standard_D4ps_v5`, `Standard_B2pts_v2`, `DPSV5`, `BMS`
_AZURE_SKU = re.compile(r"^([a-z]+)(\d+)(?:-\d+)?([a-z]*)$")
# `t2a`, `c4a`: a família ARM do GCP termina em `a` depois da geração.
_GCP_ARM = re.compile(r"^[a-z]\d+a$")
_GCP_SHARED_CORE = frozenset({"e2-micro", "e2-small", "e2-medium", "f1-micro", "g1-small"})
# `VM.Standard.A1.Flex.4c16g` ou só a família `A1`
_OCI = re.compile(r"(?:^|\.)([a-z])(\d+)(?:\.|$)")


def _aws(nome: str) -> Perfil | None:
    m = _AWS.match(nome.split(".", 1)[0])
    if m is None:
        return None
    serie, _, sufixo = m.groups()
    # A letra de série define a classe; `g` no sufixo é Graviton (m7g, c7gn,
    # r7gd, is4gen, im4gn). `t` é burstable (t2, t3, t3a, t4g).
    arm = "g" in sufixo
    burstable = serie == "t"
    tamanho = nome.split(".", 1)[1] if "." in nome else None
    base = (_BASE_AWS_T.get(tamanho or "", _BASE_AWS_PADRAO)) if burstable else None
    return Perfil(Arquitetura.ARM if arm else Arquitetura.X86, burstable=burstable, base_cpu=base)


def _gcp(nome: str) -> Perfil | None:
    familia = nome.split("-", 1)[0]
    if not familia:
        return None
    arm = bool(_GCP_ARM.match(familia))
    return Perfil(
        Arquitetura.ARM if arm else Arquitetura.X86,
        burstable=nome in _GCP_SHARED_CORE,
        base_cpu=_BASE_GCP.get(nome),
    )


def _azure(nome: str) -> Perfil | None:
    base = re.sub(r"_v\d+$|v\d+$", "", nome.removeprefix("standard_"))
    m = _AZURE_SKU.match(base)
    if m is not None:
        serie, _, recursos = m.groups()
    elif base.isalpha():
        # Família sem tamanho (`DPSV5` → `dps`): a série é a primeira letra.
        serie, recursos = base[:1], base[1:]
    else:
        return None
    # `p` nos recursos é Ampere (Dpsv5, Epsv5, Bpsv2). A série B é burstable.
    arm = "p" in recursos
    burstable = serie.startswith("b")
    return Perfil(
        Arquitetura.ARM if arm else Arquitetura.X86,
        burstable=burstable,
        base_cpu=_base_azure(nome, base, recursos) if burstable else None,
    )


def _base_azure(nome: str, base: str, recursos: str) -> Decimal | None:
    """`nome` como veio (`standard_b2s_v2`, `bsv2`); `base` sem prefixo nem versão.
    Versão sem tabela (uma série B nova): None, não reduz."""
    versao = re.search(r"v(\d+)$", nome)
    if versao is None:
        return _BASE_AZURE_V1.get(base)
    if versao.group(1) == "2":
        return next(b for letra, b in _BASE_AZURE_V2 if letra in recursos)
    return None


def _oci(nome: str) -> Perfil | None:
    m = _OCI.search(nome.replace("vm.standard.", ""))
    if m is None:
        return None
    return Perfil(Arquitetura.ARM if m.group(1) == "a" else Arquitetura.X86)


_POR_PROVEDOR: dict[str, Callable[[str], Perfil | None]] = {
    "aws": _aws,
    "gcp": _gcp,
    "azure": _azure,
    "oci": _oci,
}


def perfil(provedor: str, nome: str | None) -> Perfil | None:
    """Perfil de uma família ou SKU, ou None quando o nome não diz.

    None significa "não sei", e quem chama não filtra: inventar um perfil para
    um nome desconhecido descartaria candidatos bons por palpite.
    """
    if not nome:
        return None
    leitor = _POR_PROVEDOR.get(provedor.lower())
    return leitor(nome.strip().lower()) if leitor else None


def aceita(origem: Perfil | None, candidato: Perfil | None) -> bool:
    """Burstable só substitui burstable. Sem perfil de um dos lados, aceita."""
    if origem is None or candidato is None:
        return True
    return origem.burstable or not candidato.burstable


def mesma_arquitetura(origem: Perfil | None, candidato: Perfil | None) -> bool:
    if origem is None or candidato is None:
        return True
    return origem.arquitetura is candidato.arquitetura
