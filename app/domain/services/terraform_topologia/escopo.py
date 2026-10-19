"""Terraform só do escopo do plano de migração (T5, PLANO_ESCOPO_DE_MIGRACAO E3).

O pacote normal provisiona a análise inteira. Com um escopo (as aplicações que o
usuário escolheu na tela, já fechadas pelas regras da topologia), o payload é
recortado antes de derivar o plano:

- pools de compute: só os que têm máquina no escopo, com as máquinas na proporção da
  capacidade do escopo; o posicionamento fica só com as subredes do escopo. Pool sem
  posicionamento (fatura sem ID de recurso, que não liga máquina a pool): a proporção
  é a das máquinas do inventário da mesma família e região, escopo sobre conta;
- bancos: os do escopo, na topologia (onde estão) e nos grupos medidos da fatura (o
  que o pacote cria), pela classe. Sem os grupos medidos o pacote caía num banco
  genérico, fora da rede nova e com endpoint público (corrigido em 18/10);
- discos e banco gerenciado estimado: pelo custo das linhas do escopo. O
  armazenamento medido e os discos do inventário são da conta inteira e saem;
- buckets: os do inventário no escopo, um recurso por bucket (a fatura nem sempre liga
  o custo do armazenamento de objetos a cada bucket);
- o resto dos custos auxiliares (rede, segredos, serverless...) sai: o pacote não os
  provisiona, e a lista do que ficou de fora seria da conta inteira;
- o README ganha a seção do escopo: aplicações, ordem de migração e a conectividade
  entre nuvens a prever (o que vai antes e o que fica para trás).

Só o serviço (``somente_os_servicos``): o caso de uso já tirou do recorte o que não é
dos serviços escolhidos. Cada dependência que o recorte corta vai na seção como
``fronteira``, com o texto do que quebra e do que fazer; o README e os avisos do
pacote dizem o mesmo.

A fundação de rede (subredes e grupos) vem do espelho, que já cria só o que as
máquinas e os bancos do pacote usam. Módulo puro, sem I/O.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.domain.services.sku_matcher import extract_family
from app.domain.services.topologia.escopo import LigacaoNaFronteira, texto_da_fronteira

# Chave de `custos_auxiliares` que o recorte reescreve com o custo do escopo
CATEGORIAS_DO_ESCOPO = (
    "armazenamento_ssd",
    "armazenamento_hdd",
    "armazenamento_objetos",
    "banco_dados",
)
# Chave que o recorte mantém como estava: a tabela de equivalentes do banco
_MANTIDAS = ("equivalentes_banco_dados",)

_AZURE = re.compile(r"^standard_([a-z]+)\d+([a-z]*)(?:_v(\d+))?", re.IGNORECASE)


def familia_da_maquina(tipo: str | None) -> str | None:
    """Família no formato da chave do pool: `c4.4xlarge` → C4, `Standard_F16s_v2` → FSV2."""
    if not tipo:
        return None
    azure = _AZURE.match(tipo)
    if azure:
        letras, sufixo, versao = azure.groups()
        return f"{letras}{sufixo}".upper() + (f"V{versao}" if versao else "")
    return extract_family(tipo)


@dataclass(frozen=True, slots=True)
class LigacaoEntreNuvens:
    grupo: str
    ordem: int | None
    portas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BucketDoEscopo:
    """Bucket do inventário no escopo: o nome e o custo que a fatura liga a ele (zero
    quando a fatura não liga custo a bucket)."""

    nome: str
    custo: Decimal


@dataclass(frozen=True, slots=True)
class RecorteDoEscopo:
    """O que o escopo tem, já tirado do inventário pelo caso de uso."""

    aplicacoes: tuple[str, ...]
    unidades: int
    # (subrede de origem, família) → máquinas do escopo
    maquinas: Mapping[tuple[str | None, str], int]
    # Chave dos bancos do escopo, e quantos de cada classe. Só a classe: o motor vem
    # escrito de um jeito na fatura e de outro no inventário
    bancos: frozenset[str]
    bancos_por_classe: Mapping[str | None, int]
    # Chave de `custos_auxiliares` → custo do escopo, na moeda da análise
    custos: Mapping[str, Decimal]
    # (região, família) → (máquinas no escopo, máquinas na conta), pelo inventário.
    # Recorta o pool que não tem posicionamento
    por_familia: Mapping[tuple[str | None, str], tuple[int, int]] = field(default_factory=dict)
    ordens: tuple[tuple[int | None, tuple[str, ...]], ...] = ()
    antes: tuple[LigacaoEntreNuvens, ...] = ()
    para_tras: tuple[LigacaoEntreNuvens, ...] = ()
    avisos: tuple[str, ...] = field(default_factory=tuple)
    # Só o serviço: os escolhidos e o que o recorte cortou
    somente_os_servicos: bool = False
    servicos: tuple[str, ...] = ()
    fronteira: tuple[LigacaoNaFronteira, ...] = ()
    # Os buckets do inventário no escopo: o pacote cria um por bucket, mesmo quando a
    # fatura não liga o custo do armazenamento de objetos a cada um
    buckets: tuple[BucketDoEscopo, ...] = ()


def recortar_ao_escopo(payload: Mapping[str, Any], recorte: RecorteDoEscopo) -> dict[str, Any]:
    """O payload da análise só com o que o escopo provisiona. Não altera o original."""
    novo = deepcopy(dict(payload))
    topologia = novo.get("topologia") or {}
    detalhe = topologia.get("detalhe") or {}
    posicionados = {
        (pool.get("familia"), pool.get("regiao"))
        for pool in detalhe.get("posicionamento") or []
        if pool.get("posicionamentos")
    }
    fracoes = _recortar_posicionamento(detalhe, recorte)
    for pool in novo.get("pools_computacao") or []:
        chave = (pool.get("familia"), pool.get("regiao"))
        if chave not in posicionados:
            fracoes[chave] = _fracao_pelo_inventario(chave, recorte)
    fora = _recortar_pools(novo, fracoes)
    _recortar_bancos(detalhe, recorte)
    detalhe["discos"] = {}
    _recortar_bancos_medidos(novo, recorte)
    auxiliares = novo.get("custos_auxiliares") or {}
    novo["custos_auxiliares"] = {
        **{
            chave: float(custo)
            for chave in CATEGORIAS_DO_ESCOPO
            if (custo := recorte.custos.get(chave, Decimal(0))) > 0
        },
        **{chave: auxiliares[chave] for chave in _MANTIDAS if chave in auxiliares},
    }
    if topologia:
        topologia["escopo"] = _secao(recorte, fora)
    return novo


def _recortar_posicionamento(
    detalhe: dict[str, Any], recorte: RecorteDoEscopo
) -> dict[tuple[str | None, str | None], Decimal]:
    """Fatias de cada pool só nas subredes do escopo; devolve a fração da capacidade."""
    disponiveis = Counter(recorte.maquinas)
    fracoes: dict[tuple[str | None, str | None], Decimal] = {}
    pools = []
    for pool in detalhe.get("posicionamento") or []:
        familia = str(pool.get("familia") or "")
        total = sum((_peso(p) for p in pool.get("posicionamentos") or []), Decimal(0))
        fatias = []
        usado = Decimal(0)
        for fatia in pool.get("posicionamentos") or []:
            chave = (fatia.get("subrede"), familia.upper())
            no_escopo = min(int(fatia.get("instancias") or 0), disponiveis[chave])
            if no_escopo <= 0:
                continue
            disponiveis[chave] -= no_escopo
            proporcao = Decimal(no_escopo) / Decimal(int(fatia["instancias"]))
            usado += _peso(fatia) * proporcao
            nova = {**fatia, "instancias": no_escopo}
            if fatia.get("vcpus"):
                nova["vcpus"] = float(round(Decimal(str(fatia["vcpus"])) * proporcao, 2))
            fatias.append(nova)
        if fatias and total > 0:
            fracoes[(pool.get("familia"), pool.get("regiao"))] = usado / total
            pools.append({**pool, "posicionamentos": fatias, "sem_posicionamento": 0})
    detalhe["posicionamento"] = pools
    return fracoes


def _fracao_pelo_inventario(
    pool: tuple[str | None, str | None], recorte: RecorteDoEscopo
) -> Decimal:
    familia, regiao = pool
    no_escopo = na_conta = 0
    for (r, f), (e, c) in recorte.por_familia.items():
        if f == str(familia or "").upper() and _mesma_regiao(regiao, r):
            no_escopo += e
            na_conta += c
    return Decimal(min(no_escopo, na_conta)) / Decimal(na_conta) if na_conta else Decimal(0)


def _mesma_regiao(do_pool: str | None, do_recurso: str | None) -> bool:
    """Pool global casa com qualquer região; a zona (`us-central1-a`) casa com a dela."""
    if not do_pool:
        return True
    if not do_recurso:
        return False
    a, b = do_pool.lower(), do_recurso.lower()
    return a == b or b.startswith(f"{a}-")


def _peso(fatia: Mapping[str, Any]) -> Decimal:
    """vCPUs da fatia; sem a medida, o número de máquinas (como na repartição)."""
    return Decimal(str(fatia.get("vcpus") or 0)) or Decimal(int(fatia.get("instancias") or 0))


def _recortar_pools(
    payload: dict[str, Any], fracoes: Mapping[tuple[str | None, str | None], Decimal]
) -> list[str]:
    """Pool do escopo na proporção dele; o pool sem máquina no escopo sai."""
    mantidos, fora = [], []
    for pool in payload.get("pools_computacao") or []:
        fracao = fracoes.get((pool.get("familia"), pool.get("regiao")))
        if fracao is None or fracao <= 0:
            fora.append(str(pool.get("familia")))
            continue
        mantidos.append(_escalar(pool, fracao))
    payload["pools_computacao"] = mantidos
    return fora


def _escalar(pool: dict[str, Any], fracao: Decimal) -> dict[str, Any]:
    def vezes(valor: Any) -> Any:
        if isinstance(valor, int | float) and not isinstance(valor, bool):
            return float(round(Decimal(str(valor)) * fracao, 2))
        return valor

    novo = {**pool}
    novo["capacidade"] = {k: vezes(v) for k, v in (pool.get("capacidade") or {}).items()}
    novo["custo_computacao"] = vezes(pool.get("custo_computacao"))
    if pool.get("instancias"):
        novo["instancias"] = max(1, math.ceil(Decimal(str(pool["instancias"])) * fracao))
    equivalentes = []
    for e in pool.get("equivalentes") or []:
        instancias = int(e.get("instancias") or 1)
        escaladas = max(1, math.ceil(Decimal(instancias) * fracao))
        tecnica = e.get("correspondencia_tecnica") or {}
        por_maquina = {
            k: (v / instancias if isinstance(v, int | float) else v) for k, v in tecnica.items()
        }
        equivalentes.append(
            {
                **e,
                **({"instancias": escaladas} if e.get("instancias") else {}),
                "correspondencia_tecnica": {
                    k: (v * escaladas if isinstance(v, int | float) else v)
                    for k, v in por_maquina.items()
                },
            }
        )
    novo["equivalentes"] = equivalentes
    novo.pop("instancias_medidas", None)
    return novo


def _recortar_bancos(detalhe: dict[str, Any], recorte: RecorteDoEscopo) -> None:
    """Grupo medido de bancos com a quantidade do escopo, pela classe."""
    disponiveis = Counter(recorte.bancos_por_classe)
    grupos = []
    for b in detalhe.get("bancos") or []:
        chave = b.get("classe")
        no_escopo = min(int(b.get("total") or 0), disponiveis[chave])
        if no_escopo <= 0:
            continue
        disponiveis[chave] -= no_escopo
        grupos.append(
            {**b, "total": no_escopo, "encontrados": min(int(b.get("encontrados") or 0), no_escopo)}
        )
    detalhe["bancos"] = grupos
    detalhe["bancos_sem_grupo"] = [
        b for b in detalhe.get("bancos_sem_grupo") or [] if b.get("chave") in recorte.bancos
    ]


def _recortar_bancos_medidos(payload: dict[str, Any], recorte: RecorteDoEscopo) -> None:
    """Os grupos de banco medidos na fatura, na quantidade do escopo (pela classe, como a
    topologia). O armazenamento medido é da conta inteira e sai."""
    medido = payload.pop("inventario_medido", None) or {}
    disponiveis = Counter(recorte.bancos_por_classe)
    grupos = []
    for g in medido.get("bancos") or []:
        classe = g.get("classe")
        no_escopo = min(int(g.get("quantidade") or 1), disponiveis[classe])
        if no_escopo <= 0:
            continue
        disponiveis[classe] -= no_escopo
        grupos.append({**g, "quantidade": no_escopo})
    if grupos:
        payload["inventario_medido"] = {
            "bancos": grupos,
            "total_bancos": sum(g["quantidade"] for g in grupos),
        }


def _secao(recorte: RecorteDoEscopo, fora: list[str]) -> dict[str, Any]:
    def ligacao(g: LigacaoEntreNuvens) -> dict[str, Any]:
        return {"grupo": g.grupo, "ordem": g.ordem, "portas": list(g.portas)}

    return {
        "aplicacoes": list(recorte.aplicacoes),
        "unidades": recorte.unidades,
        "ordens": [{"ordem": o, "grupos": list(gs)} for o, gs in recorte.ordens],
        "antes": [ligacao(g) for g in recorte.antes],
        "para_tras": [ligacao(g) for g in recorte.para_tras],
        "pools_fora": sorted(set(fora)),
        "avisos": list(recorte.avisos),
        **(
            {
                "buckets": [
                    {"nome": b.nome, "custo": float(b.custo)}
                    for b in sorted(recorte.buckets, key=lambda b: b.nome)
                ]
            }
            if recorte.buckets
            else {}
        ),
        **(
            {
                "somente_os_servicos": True,
                "servicos": list(recorte.servicos),
                "fronteira": [
                    {
                        "sentido": f.sentido,
                        "de": f.de,
                        "para": f.para,
                        "natureza": f.natureza.value,
                        "portas": list(f.portas),
                        "forte": f.forte,
                        "texto": texto_da_fronteira(f),
                    }
                    for f in recorte.fronteira
                ],
            }
            if recorte.somente_os_servicos
            else {}
        ),
    }
