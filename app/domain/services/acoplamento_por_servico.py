"""Acoplamento por serviço da fatura (PLANO_LOCKIN_INVENTARIO, bloco A).

A tela de acoplamento lia `cross_cloud_equivalents`, que é a tabela de preço
por destino: um serviço aparecia uma vez por nuvem de destino, a cotação de
categoria virava linha própria ("Bancos de dados (agregado)", com o RDS dentro)
e o compute aparecia por família com acoplamento fixo em baixo, enquanto o score
tratava o mesmo compute pela nota do catálogo. Somadas, as linhas passavam de
100% do gasto e os contadores contavam linhas, não serviços.

Esta visão parte do mesmo conjunto que o score técnico lê
(`decision_workloads`): uma linha por serviço da fatura. A soma fecha com o
gasto por construção, e o nível é o que o score usa:

- ``nivel``: o lado da origem, como `technical_evidence` o enxerga;
- ``por_destino``: o que o eixo técnico usa em cada cenário (pior entre origem
  e destino; desconhecido quando o mapeamento é ambíguo ou falta).

O que o score não usa — a nota herdada do tipo de serviço, um pool sem a mesma
arquitetura no destino — vai em ``sugestoes``, nunca em ``nivel``. Os pools de
compute aparecem dentro da linha de máquinas virtuais, que é onde está o custo.

Função pura, sem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from app.domain.services.decision_support import (
    decision_workloads,
    number,
    unique_targets,
)
from app.domain.services.migration_strategy import normalized, technical_evidence
from app.domain.services.perfil_de_familia import perfil
from app.domain.services.sku_normalization import normalize_provider

VERSAO = "acoplamento-v1"

NIVEIS = ("low", "medium", "high")
_ORDEM = {nivel: posicao for posicao, nivel in enumerate(NIVEIS, start=1)}
_PROVEDORES = frozenset({"aws", "azure", "gcp", "oci"})
_ZERO = Decimal(0)
_CENTAVO = Decimal("0.01")
_SEM_REGIAO = frozenset({"", "global"})


class PayloadSemServicosError(ValueError):
    """O payload não traz a lista de serviços (análise anterior à decisão
    determinística). Sem ela não há o que listar sem inventar."""


@dataclass(frozen=True, slots=True)
class Sugestao:
    nivel: str
    fonte: str  # classe | arquitetura | inventario | dependencias
    motivo: str


@dataclass(frozen=True, slots=True)
class PoolDoServico:
    familia: str
    regiao: str | None
    custo: Decimal
    arquitetura: str | None  # x86 | arm; None quando o nome não diz
    burstable: bool | None
    coberto_por_compromisso: bool
    destinos_sem_mesma_arquitetura: tuple[str, ...]
    sugestoes: tuple[Sugestao, ...]


@dataclass(frozen=True, slots=True)
class AcoplamentoDeServico:
    id: str
    nome: str
    categoria: str
    service_type: str | None
    tipo_inferido: bool
    custo: Decimal
    fracao: Decimal | None
    nivel: str | None
    fonte: str | None  # catalogo | informado
    nivel_sugerido: str | None
    sugestoes: tuple[Sugestao, ...]
    por_destino: Mapping[str, str | None]
    pools: tuple[PoolDoServico, ...]
    alertas: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Resumo:
    servicos: int
    low: int
    medium: int
    high: int
    a_confirmar: int
    a_confirmar_com_sugestao: int


@dataclass(frozen=True, slots=True)
class Reconciliacao:
    soma_linhas: Decimal
    fora_do_escopo: Decimal
    custo_atual: Decimal | None
    diferenca: Decimal | None
    status: str  # fecha | diferenca_de_escopo | sem_custo_atual


@dataclass(frozen=True, slots=True)
class AcoplamentoPorServico:
    versao: str
    moeda: str
    provedor_origem: str | None
    custo_atual: Decimal | None
    resumo: Resumo
    servicos: tuple[AcoplamentoDeServico, ...]
    pools_sem_servico: tuple[PoolDoServico, ...]
    reconciliacao: Reconciliacao


def montar_acoplamento_por_servico(payload: Mapping[str, Any]) -> AcoplamentoPorServico:
    if "decision_workloads" not in payload and "workloads" not in payload:
        raise PayloadSemServicosError(
            "a análise não tem a lista de serviços (decision_workloads); gere a análise de novo"
        )
    dados = dict(payload)
    origem = _provedor(dados.get("provedor_origem") or dados.get("source_provider")) or None
    custo_atual = _custo_atual(dados)
    cenarios = _cenarios(dados, origem)
    no_escopo = decision_workloads(dados)

    linhas = [_linha(w, cenarios, custo_atual) for w in no_escopo]
    pools = tuple(_pool(p, origem) for p in dados.get("pools_computacao") or [])
    linhas, sem_servico = _anexar_pools(linhas, pools)
    linhas.sort(key=lambda s: (-s.custo, s.nome.casefold(), s.id))

    return AcoplamentoPorServico(
        versao=VERSAO,
        moeda=str(dados.get("moeda") or dados.get("currency") or "USD"),
        provedor_origem=origem,
        custo_atual=custo_atual,
        resumo=_resumo(linhas),
        servicos=tuple(linhas),
        pools_sem_servico=sem_servico,
        reconciliacao=_reconciliacao(dados, linhas, custo_atual),
    )


# ── linha de serviço ─────────────────────────────────────────────────────────


def _linha(
    workload: dict[str, Any], cenarios: Sequence[str], custo_atual: Decimal | None
) -> AcoplamentoDeServico:
    nome = str(workload.get("name") or "Serviço não identificado")
    categoria = normalized(workload.get("category"))
    custo = number(workload.get("cost")) or _ZERO
    inferido = workload.get("type_source") == "inferido"
    # Sem destino, `technical_evidence` devolve exatamente o lado da origem: é
    # o ponto de partida do score, e o inferido sai desconhecido como lá.
    _, nivel = technical_evidence(workload, None)
    nivel = nivel if nivel in _ORDEM else None
    alvos = unique_targets(workload)
    por_destino: dict[str, str | None] = {}
    for provedor in cenarios:
        alvo = alvos.get(provedor)
        lock = technical_evidence(workload, alvo)[1] if alvo else None
        por_destino[provedor] = lock if lock in _ORDEM else None

    sugestoes = _sugestoes_de_classe(workload)
    alertas = (
        ("Tipo de serviço inferido pelo classificador; a nota só vale depois de confirmado.",)
        if inferido
        else ()
    )
    return AcoplamentoDeServico(
        id=f"service/{categoria}/{nome}",
        nome=nome,
        categoria=categoria,
        service_type=workload.get("service_type"),
        tipo_inferido=inferido,
        custo=custo,
        fracao=_fracao(custo, custo_atual),
        nivel=nivel,
        fonte=_fonte(workload) if nivel else None,
        nivel_sugerido=_nivel_sugerido(nivel, sugestoes),
        sugestoes=sugestoes,
        por_destino=por_destino,
        pools=(),
        alertas=alertas,
    )


def _sugestoes_de_classe(workload: dict[str, Any]) -> tuple[Sugestao, ...]:
    migracao = workload.get("migration") or {}
    lock = normalized(migracao.get("lock_in"))
    if migracao.get("fonte") != "classe" or lock not in _ORDEM:
        return ()
    tipo = workload.get("service_type") or "mesmo tipo"
    return (
        Sugestao(
            nivel=lock,
            fonte="classe",
            motivo=(
                f"Herdado dos serviços curados do mesmo tipo ({tipo}); "
                "fora do score até a curadoria confirmar."
            ),
        ),
    )


def _fonte(workload: dict[str, Any]) -> str:
    origem = workload.get("evidence_source", "catalog_snapshot")
    return "catalogo" if origem == "catalog_snapshot" else "informado"


def _nivel_sugerido(nivel: str | None, sugestoes: Sequence[Sugestao]) -> str | None:
    """A sugestão só conta quando mudaria o nível: sobe ou preenche o vazio."""
    if not sugestoes:
        return None
    maior = max((s.nivel for s in sugestoes), key=_ORDEM.__getitem__)
    if nivel is None or _ORDEM[maior] > _ORDEM[nivel]:
        return maior
    return None


# ── pools de compute ─────────────────────────────────────────────────────────


def _pool(pool: Mapping[str, Any], origem: str | None) -> PoolDoServico:
    familia = str(pool.get("familia") or "Família não identificada")
    regiao = str(pool.get("regiao") or "")
    custos = pool.get("custos") or {}
    custo = sum(
        (
            number(pool.get(k) or custos.get(k)) or _ZERO
            for k in ("custo_computacao", "custo_licenca_so")
        ),
        _ZERO,
    )
    p = perfil(origem or "", familia)
    arquitetura = p.arquitetura.value if p else None
    sem_par = tuple(
        sorted(
            {
                _provedor(e.get("provedor") or e.get("provider"))
                for e in pool.get("equivalentes") or []
                if e.get("arquitetura_diferente")
            }
            - {""}
        )
    )
    sugestoes: tuple[Sugestao, ...] = ()
    if sem_par and arquitetura:
        sugestoes = (
            Sugestao(
                nivel="medium",
                fonte="arquitetura",
                motivo=(
                    f"Sem família {arquitetura.upper()} equivalente em "
                    f"{', '.join(d.upper() for d in sem_par)}: a cotação usou a outra "
                    "arquitetura, o que exige recompilar ou validar a aplicação."
                ),
            ),
        )
    return PoolDoServico(
        familia=familia,
        regiao=None if regiao.casefold() in _SEM_REGIAO else regiao,
        custo=custo,
        arquitetura=arquitetura,
        burstable=p.burstable if p else None,
        coberto_por_compromisso=pool.get("custo_coberto_por_compromisso") is True,
        destinos_sem_mesma_arquitetura=sem_par,
        sugestoes=sugestoes,
    )


def _anexar_pools(
    linhas: list[AcoplamentoDeServico], pools: tuple[PoolDoServico, ...]
) -> tuple[list[AcoplamentoDeServico], tuple[PoolDoServico, ...]]:
    """Os pools detalham o serviço de máquinas virtuais, onde está o custo deles.

    Vão para o serviço curado de `virtual_server` de maior custo. Sem ele, ficam
    à parte em ``pools_sem_servico``: anexar a um palpite seria inventar dono.
    """
    if not pools:
        return linhas, ()
    candidatos = [
        i
        for i, s in enumerate(linhas)
        if s.service_type == "virtual_server" and not s.tipo_inferido
    ]
    if not candidatos:
        return linhas, pools
    alvo = max(candidatos, key=lambda i: (linhas[i].custo, linhas[i].nome))
    pools = tuple(sorted(pools, key=lambda p: (-p.custo, p.familia, p.regiao or "")))
    servico = linhas[alvo]
    com_sugestao = [p for p in pools if p.sugestoes]
    sugestoes = servico.sugestoes
    if com_sugestao:
        familias = ", ".join(sorted({p.familia for p in com_sugestao}))
        sugestoes = (
            *sugestoes,
            Sugestao(
                nivel="medium",
                fonte="arquitetura",
                motivo=f"Pools sem a mesma arquitetura em algum destino: {familias}.",
            ),
        )
    linhas[alvo] = replace(
        servico,
        pools=pools,
        sugestoes=sugestoes,
        nivel_sugerido=_nivel_sugerido(servico.nivel, sugestoes),
    )
    return linhas, ()


# ── totais ───────────────────────────────────────────────────────────────────


def _resumo(linhas: Sequence[AcoplamentoDeServico]) -> Resumo:
    def conta(nivel: str) -> int:
        return sum(1 for s in linhas if s.nivel == nivel)

    return Resumo(
        servicos=len(linhas),
        low=conta("low"),
        medium=conta("medium"),
        high=conta("high"),
        a_confirmar=sum(1 for s in linhas if s.nivel is None),
        a_confirmar_com_sugestao=sum(
            1 for s in linhas if s.nivel is None and s.nivel_sugerido is not None
        ),
    )


def _reconciliacao(
    dados: Mapping[str, Any],
    linhas: Sequence[AcoplamentoDeServico],
    custo_atual: Decimal | None,
) -> Reconciliacao:
    """O que ficou de fora (imposto, crédito, compromisso, custo zero) é a
    diferença entre todos os serviços do payload e os listados."""
    brutos = _custos_brutos(dados)
    soma_bruta = sum(brutos, _ZERO)
    soma_linhas = sum((s.custo for s in linhas), _ZERO)
    if custo_atual is None:
        return Reconciliacao(soma_linhas, soma_bruta - soma_linhas, None, None, "sem_custo_atual")
    diferenca = custo_atual - soma_bruta
    # Cada serviço é arredondado na serialização do payload.
    tolerancia = _CENTAVO * max(1, len(brutos))
    return Reconciliacao(
        soma_linhas=soma_linhas,
        fora_do_escopo=soma_bruta - soma_linhas,
        custo_atual=custo_atual,
        diferenca=diferenca,
        status="fecha" if abs(diferenca) <= tolerancia else "diferenca_de_escopo",
    )


def _custos_brutos(dados: Mapping[str, Any]) -> list[Decimal]:
    if "decision_workloads" in dados:
        return [number(w.get("cost")) or _ZERO for w in dados.get("decision_workloads") or []]
    return [
        number((w.get("source_usage") or {}).get("billed_cost")) or _ZERO
        for w in dados.get("workloads") or []
    ]


def _custo_atual(dados: Mapping[str, Any]) -> Decimal | None:
    for valor in (
        dados.get("gasto_total"),
        dados.get("total_spend"),
        (dados.get("resumo_executivo") or {}).get("custo_mensal_atual"),
    ):
        custo = number(valor)
        if custo is not None:
            return custo
    return None


def _cenarios(dados: Mapping[str, Any], origem: str | None) -> list[str]:
    """Os cenários do score: os provedores do `resumo_executivo`. Sem resumo
    (contrato direto), os destinos que aparecem nos mapeamentos."""
    provedores = {
        _provedor(p.get("provedor") or p.get("provider"))
        for p in (dados.get("resumo_executivo") or {}).get("por_provedor") or []
    }
    if not provedores:
        provedores = {
            _provedor(t.get("provider") or t.get("provedor"))
            for w in decision_workloads(dict(dados))
            for t in w.get("targets") or []
        }
    return sorted((provedores & _PROVEDORES) - {origem or ""})


def _fracao(custo: Decimal, custo_atual: Decimal | None) -> Decimal | None:
    if custo_atual is None or custo_atual <= 0:
        return None
    return (custo / custo_atual).quantize(Decimal("0.0001"))


def _provedor(valor: object) -> str:
    return normalize_provider(str(valor or "")).lower()
