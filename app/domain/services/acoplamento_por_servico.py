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
arquitetura no destino, a dependência do inventário — vai em ``sugestoes``,
nunca em ``nivel``. Serviço sem categoria identificada também é listado (é gasto
real), marcado ``entra_no_score=False``; fora da lista fica só o financeiro. Os pools de
compute aparecem dentro da linha de máquinas virtuais, que é onde está o custo.

Função pura, sem I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from app.domain.services.decision_support import (
    UNCLASSIFIED,
    all_workloads,
    decision_workloads,
    is_scored_workload,
    number,
    unique_targets,
)
from app.domain.services.migration_strategy import normalized, technical_evidence
from app.domain.services.perfil_de_familia import perfil
from app.domain.services.service_identity import is_financial_component
from app.domain.services.sku_normalization import normalize_provider
from app.domain.services.technical_assessment import assess

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
    # Do inventário (bloco B): o que migra junto, de quem depende, onda e grupo.
    # None sem inventário, ou sem recurso deste serviço encontrado nele.
    dependencias: Mapping[str, Any] | None = None
    # Serviço sem categoria identificada: é gasto real, mas o score não o lê.
    entra_no_score: bool = True
    # A outra metade da evidência técnica do score, em cada cenário: o serviço
    # equivalente e se exige refatoração (pior entre origem e destino; None
    # quando um dos lados não diz, porque "não exige" precisa dos dois).
    equivalente_por_destino: Mapping[str, str | None] = field(default_factory=dict)
    refatoracao_por_destino: Mapping[str, bool | None] = field(default_factory=dict)
    # Do catálogo: classe 7R de partida (rehost, replatform, refactor) e esforço.
    classe_migracao: str | None = None
    esforco: str | None = None


@dataclass(frozen=True, slots=True)
class Resumo:
    servicos: int
    low: int
    medium: int
    high: int
    a_confirmar: int
    a_confirmar_com_sugestao: int
    migram_em_grupo: int = 0
    fora_do_score: int = 0


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
    # A análise foi feita com inventário; e, nela, a topologia já ligou recurso a
    # serviço (análises de antes do bloco B têm inventário sem esse eixo).
    com_inventario: bool = False
    eixo_de_dependencia: bool = False


def montar_acoplamento_por_servico(payload: Mapping[str, Any]) -> AcoplamentoPorServico:
    if "decision_workloads" not in payload and "workloads" not in payload:
        raise PayloadSemServicosError(
            "a análise não tem a lista de serviços (decision_workloads); gere a análise de novo"
        )
    dados = dict(payload)
    origem = _provedor(dados.get("provedor_origem") or dados.get("source_provider")) or None
    custo_atual = _custo_atual(dados)
    cenarios = _cenarios(dados, origem)

    topologia = dados.get("topologia")
    com_inventario = isinstance(topologia, Mapping)
    por_servico = _dependencias_do_inventario(topologia)
    policy_v4 = (dados.get("decision_support") or {}).get("policy_version") == "score-7rs-v4"

    linhas = [
        _linha(w, cenarios, custo_atual, por_servico, policy_v4=policy_v4)
        for w in all_workloads(dados) if _listado(w)
    ]
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
        com_inventario=com_inventario,
        eixo_de_dependencia=por_servico is not None,
    )


def _listado(workload: Mapping[str, Any]) -> bool:
    """Tudo que o score lê, mais o gasto sem categoria. Fica de fora só o
    financeiro (imposto, crédito, desconto, compromisso, suporte) e o custo zero."""
    if is_scored_workload(dict(workload)):
        return True
    return (
        normalized(workload.get("category")) in UNCLASSIFIED
        and not is_financial_component(str(workload.get("name") or ""))
        and (number(workload.get("cost")) or _ZERO) > 0
    )


def _dependencias_do_inventario(topologia: Any) -> Mapping[str, Any] | None:
    """`topologia.detalhe.servicos`, gravado na análise com inventário (B1)."""
    if not isinstance(topologia, Mapping):
        return None
    servicos = (topologia.get("detalhe") or {}).get("servicos")
    return servicos if isinstance(servicos, Mapping) else None


# ── linha de serviço ─────────────────────────────────────────────────────────


def _linha(
    workload: dict[str, Any],
    cenarios: Sequence[str],
    custo_atual: Decimal | None,
    por_servico: Mapping[str, Any] | None,
    *,
    policy_v4: bool = False,
) -> AcoplamentoDeServico:
    nome = str(workload.get("name") or "Serviço não identificado")
    categoria = normalized(workload.get("category"))
    custo = number(workload.get("cost")) or _ZERO
    inferido = workload.get("type_source") == "inferido"
    no_score = is_scored_workload(workload)
    nivel: str | None = None
    por_destino: dict[str, str | None] = {}
    equivalentes: dict[str, str | None] = {}
    refatoracao: dict[str, bool | None] = {}
    if no_score:
        # Sem destino, `technical_evidence` devolve exatamente o lado da origem:
        # é o ponto de partida do score, e o inferido sai desconhecido como lá.
        _, lock = technical_evidence(workload, None)
        nivel = lock if lock in _ORDEM else None
        alvos = unique_targets(workload)
        for provedor in cenarios:
            alvo = alvos.get(provedor)
            refatora, lock = technical_evidence(workload, alvo) if alvo else (None, None)
            if policy_v4:
                evidence = assess(workload, provedor, {})["evidence"]
                refatora, lock = evidence["requires_refactoring"], evidence["lock_in"]
            por_destino[provedor] = lock if lock in _ORDEM else None
            refatoracao[provedor] = refatora
            equivalentes[provedor] = (
                str(alvo["service_name"]) if alvo and alvo.get("service_name") else None
            )
    migracao = workload.get("migration") or {}

    dependencias = (por_servico or {}).get(nome)
    sugestoes = (*_sugestoes_de_classe(workload), *_sugestoes_de_dependencia(dependencias))
    alertas: tuple[str, ...] = ()
    if not no_score:
        alertas = ("Categoria não identificada: fica fora do score até ser classificada.",)
    elif inferido:
        alertas = (
            "Tipo de serviço inferido pelo classificador; a nota só vale depois de confirmado.",
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
        dependencias=dependencias if isinstance(dependencias, Mapping) else None,
        entra_no_score=no_score,
        equivalente_por_destino=equivalentes,
        refatoracao_por_destino=refatoracao,
        classe_migracao=_texto(migracao.get("classe_migracao")) if no_score else None,
        esforco=_texto(migracao.get("esforco")) if no_score else None,
    )


def _texto(valor: object) -> str | None:
    return str(valor) if valor else None


def _sugestoes_de_dependencia(dependencias: Any) -> tuple[Sugestao, ...]:
    """Do inventário saem duas sugestões independentes:

    - ``inventario``: a exclusividade pelos atributos do recurso (motor do
      banco, tipo nativo). Qualquer nível conta, inclusive baixo: é evidência
      sobre o que o cliente usa, e pode preencher um nível vazio;
    - ``dependencias``: só médio e alto. Dependência baixa não diz nada sobre
      exclusividade e não deve preencher um nível vazio.
    """
    if not isinstance(dependencias, Mapping):
        return ()
    sugestoes: list[Sugestao] = []
    exclusividade = dependencias.get("exclusividade")
    if isinstance(exclusividade, Mapping) and exclusividade.get("nivel") in _ORDEM:
        sugestoes.append(
            Sugestao(
                nivel=str(exclusividade["nivel"]),
                fonte="inventario",
                motivo=str(exclusividade.get("motivo")),
            )
        )
    nivel = dependencias.get("nivel")
    if nivel in ("medium", "high"):
        sugestoes.append(
            Sugestao(nivel=nivel, fonte="dependencias", motivo=str(dependencias.get("motivo")))
        )
    return tuple(sugestoes)


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
        migram_em_grupo=sum(
            1 for s in linhas if s.dependencias and s.dependencias.get("migra_junto_com")
        ),
        fora_do_score=sum(1 for s in linhas if not s.entra_no_score),
    )


def _reconciliacao(
    dados: Mapping[str, Any],
    linhas: Sequence[AcoplamentoDeServico],
    custo_atual: Decimal | None,
) -> Reconciliacao:
    """O que ficou de fora (imposto, crédito, desconto, compromisso, suporte,
    custo zero) é a diferença entre todos os serviços do payload e os listados."""
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
    return [number(w.get("cost")) or _ZERO for w in all_workloads(dict(dados))]


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
