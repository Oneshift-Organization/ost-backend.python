"""Aplicações da análise e o plano de migração de um escopo (PLANO_ESCOPO_DE_MIGRACAO, E1 e E2).

Calculado na leitura, sem LLM e sem gravar: o inventário da análise é lido de
novo e a topologia recalculada (é determinística). O ``fingerprint`` tem que bater
com o que ficou congelado no payload; se o inventário mudou, a resposta seria de
outra coisa e a rota recusa.

Dinheiro na moeda da análise, com a mesma taxa de câmbio do payload.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.exceptions import ConflictError, NotFoundError
from app.domain.entities.analysis_job import AnalysisJob, AnalysisStatus
from app.domain.entities.topologia import FUNDACAO, TipoDeRecurso, TipoDeRelacao
from app.domain.repositories.i_analysis_job_repo import IAnalysisJobRepository
from app.domain.repositories.i_custo_por_recurso import ILeitorDeCustoPorRecurso
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.atribuicao_por_tipo import (
    atribuir_por_tipo,
)
from app.domain.services.atribuicao_por_tipo import (
    servicos_do_recurso as servicos_pelo_tipo,
)
from app.domain.services.correlacao_inventario import IndiceDeApelidos, correlacionar
from app.domain.services.correspondencia_inventario import (
    SEM_CORRESPONDENCIA,
    Correspondencia,
    avaliar_correspondencia,
)
from app.domain.services.cotacao_do_escopo import (
    CotacaoDoEscopo,
    LinhaDoEscopo,
    PoolCotado,
    cotar_escopo,
)
from app.domain.services.currency import FxRate, convert, identity_rate
from app.domain.services.sku_matcher import extract_family
from app.domain.services.terraform_topologia.escopo import (
    BucketDoEscopo,
    ClusterDoEscopo,
    ComponenteDoEscopo,
    GrupoDeNos,
    LigacaoEntreNuvens,
    RecorteDoEscopo,
    familia_da_maquina,
)
from app.domain.services.topologia import analisar_topologia
from app.domain.services.topologia.cargas_no_cluster import CargasDoCluster, cargas_no_cluster
from app.domain.services.topologia.dependencias_de_servico import dependencias_de_servico
from app.domain.services.topologia.escopo import (
    CatalogoDeAplicacoes,
    Escolha,
    GrupoVizinho,
    PlanoDeMigracao,
    fechar_plano,
    montar_catalogo,
)
from app.domain.services.topologia.indice import Indice
from app.use_cases.analysis.consultar_acoplamento import (
    AnaliseFalhouError,
    AnaliseNaoConcluidaError,
)
from app.use_cases.inventory.consultas import exigir_processado, obter_do_usuario
from app.use_cases.inventory.declaracoes import para_dominio

_ZERO = Decimal(0)

# custos_auxiliares do payload → categoria da cotação do escopo
_CATEGORIAS = {
    "equivalentes_banco_dados": "banco",
    "equivalentes_armazenamento_ssd": "ssd",
    "equivalentes_armazenamento_hdd": "hdd",
    "equivalentes_armazenamento_objetos": "objetos",
}

LIMITE_DAS_DEPENDENCIAS = (
    "Dependências vêm das regras de rede e do que foi declarado: o que a configuração "
    "permite, não o tráfego real."
)
EXPLICACAO_DA_ORDEM = (
    "Ordem de migração: em que etapa o grupo migra. A 1 vai primeiro; quem depende dela "
    "vem depois. Sem ordem: não depende de ninguém e pode ir a qualquer momento."
)


class InventarioAusenteError(ConflictError):
    """A análise foi feita sem inventário: não há aplicações nem dependências."""


class TopologiaDivergenteError(ConflictError):
    """O inventário mudou desde a análise: o plano seria de outra coisa."""


class InventarioNaoCorrespondeError(ConflictError):
    """O inventário da análise é de outra conta: nada da fatura está nele."""

    def __init__(self, correspondencia: Correspondencia) -> None:
        super().__init__(correspondencia.mensagem)
        self.correspondencia = correspondencia


@dataclass(frozen=True, slots=True)
class RecursoDoEscopo:
    """Um recurso do inventário, como o plano exportado o lista: o que migrar."""

    chave: str
    nome: str | None
    tipo: str
    tipo_nativo: str
    regiao: str | None
    custo: Decimal  # na moeda da análise; 0 quando a fatura não cita o recurso
    # Para o Terraform do escopo (T5): onde está e o que é
    subredes: tuple[str, ...] = ()
    familia: str | None = None
    categoria_disco: str | None = None
    classe: str | None = None
    motor: str | None = None
    # Kubernetes e serviços gerenciados no Terraform do escopo (itens 15 e 19): o
    # cluster de que o recurso é membro, o grupo de nós, a versão e o componente
    cluster: str | None = None
    grupo_de_nos: str | None = None
    versao: str | None = None
    componente: str | None = None


# Tag do grupo de nós em cada nuvem: grupo gerenciado do EKS (e do eksctl), pool do
# AKS, pool do GKE. Sem tag, os nós se agrupam pela família da máquina
_TAGS_DO_GRUPO_DE_NOS = (
    "eks:nodegroup-name",
    "alpha.eksctl.io/nodegroup-name",
    "kubernetes.azure.com/agentpool",
    "aks-managed-poolname",
    "poolname",
    "goog-k8s-node-pool-name",
)
# Zona de DNS pelo tipo nativo: o inventário não a marca como componente
_ZONAS_DNS = frozenset(
    {
        "AWS::Route53::HostedZone",
        "microsoft.network/dnszones",
        "microsoft.network/privatednszones",
        "dns.googleapis.com/ManagedZone",
        "oci_dns_zone",
    }
)
ZONA_DNS = "zona_dns"


def _grupo_de_nos(tags: Mapping[str, str]) -> str | None:
    minusculas = {k.lower(): v for k, v in tags.items()}
    return next((minusculas[k] for k in _TAGS_DO_GRUPO_DE_NOS if minusculas.get(k)), None)


def _componente(recurso: Any) -> str | None:
    if recurso.tipo_nativo in _ZONAS_DNS:
        return ZONA_DNS
    return _texto_cru(recurso.atributos.get("componente"))


def _recurso_do_escopo(chave: str, r: Any, indice: Indice, custo: Decimal) -> RecursoDoEscopo:
    cluster = next(
        (
            c
            for c in indice.para(chave, TipoDeRelacao.MEMBRO_DE)
            if indice.tipo(c) is TipoDeRecurso.CLUSTER_KUBERNETES
        ),
        None,
    )
    return RecursoDoEscopo(
        chave=chave,
        nome=r.nome,
        tipo=str(r.tipo),
        tipo_nativo=r.tipo_nativo,
        regiao=r.regiao,
        custo=custo,
        subredes=tuple(sorted(indice.subredes_de(chave))),
        familia=familia_da_maquina(_texto(r.atributos.get("tipo_maquina"))),
        categoria_disco=_texto(r.atributos.get("categoria_disco")),
        classe=_texto_cru(r.atributos.get("classe")),
        motor=_texto_cru(r.atributos.get("motor")),
        cluster=cluster,
        grupo_de_nos=_grupo_de_nos(r.tags) if cluster else None,
        versao=_texto_cru(r.atributos.get("versao")),
        componente=_componente(r),
    )


@dataclass(frozen=True, slots=True)
class ServicoNoInventario:
    """Os recursos do inventário inteiro (não só das aplicações) de um serviço da fatura."""

    nativos: tuple[tuple[str, int], ...]
    # Tem rede, subrede, grupo ou gateway: é a fundação que o espelho recria
    fundacao: bool


@dataclass(frozen=True, slots=True)
class ContextoDoEscopo:
    catalogo: CatalogoDeAplicacoes
    linhas: tuple[LinhaDoEscopo, ...]
    pools: tuple[PoolCotado, ...]
    taxas_de_categoria: Mapping[str, Mapping[str, Decimal]]
    provedores: tuple[str, ...]
    provedor_origem: str | None
    provedor_recomendado: str | None
    moeda: str
    inventario_id: str
    correspondencia: Correspondencia
    premissas: tuple[tuple[str, str | None, str], ...] = field(default_factory=tuple)
    recursos: Mapping[str, RecursoDoEscopo] = field(default_factory=dict)
    periodo: str | None = None
    gasto_total: Decimal | None = None
    # Unidade com cluster → prováveis aplicações dentro dele (indício, não unidade)
    cargas_no_cluster: Mapping[str, CargasDoCluster] = field(default_factory=dict)
    # Serviço → o que o inventário tem dele: diz o que fica fora do pacote sem mentir
    no_inventario: Mapping[str, ServicoNoInventario] = field(default_factory=dict)
    # Registro, segredo e zona de DNS fora das aplicações, com o serviço da fatura
    sem_aplicacao: Mapping[str, RecursoDoEscopo] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlanoCotado:
    plano: PlanoDeMigracao
    cotacao: CotacaoDoEscopo
    contexto: ContextoDoEscopo


class EscopoDeMigracaoUseCase:
    def __init__(
        self,
        jobs: IAnalysisJobRepository,
        inventarios: IInventoryRepository,
        custos: ILeitorDeCustoPorRecurso,
    ) -> None:
        self._jobs = jobs
        self._inventarios = inventarios
        self._custos = custos

    async def aplicacoes(self, user_id: UUID, job_id: UUID) -> ContextoDoEscopo:
        return await self._contexto(user_id, job_id)

    async def plano(self, user_id: UUID, job_id: UUID, escolha: Escolha) -> PlanoCotado:
        ctx = await self._contexto(user_id, job_id)
        # Sem ID de recurso o serviço ainda chega à unidade: pelo custo atribuído pelo
        # tipo, ou pelo tipo do recurso no inventário (servicos_presentes)
        plano = fechar_plano(ctx.catalogo, escolha)
        unidades = set(plano.unidades)
        cotacao = cotar_escopo(
            (
                linha
                for linha in ctx.linhas
                if linha.unidade in unidades
                and (not escolha.somente_os_servicos or linha.servico in escolha.servicos)
            ),
            ctx.pools,
            ctx.taxas_de_categoria,
            ctx.provedores,
        )
        return PlanoCotado(plano=plano, cotacao=cotacao, contexto=ctx)

    async def recorte(
        self, user_id: UUID, job_id: UUID, escolha: Escolha
    ) -> tuple[PlanoCotado, RecorteDoEscopo]:
        """O plano e o que o Terraform do escopo provisiona (T5)."""
        cotado = await self.plano(user_id, job_id, escolha)
        return cotado, recorte_do_plano(cotado)

    async def _contexto(self, user_id: UUID, job_id: UUID) -> ContextoDoEscopo:
        job = await self._job(user_id, job_id)
        payload: Mapping[str, Any] = job.llm_input_payload or {}
        topologia = payload.get("topologia") or {}
        inventario_id = topologia.get("inventario_id")
        if not inventario_id:
            raise InventarioAusenteError(
                f"a análise {job_id} foi feita sem inventário; envie o inventário da conta"
            )
        inventario = await obter_do_usuario(self._inventarios, UUID(inventario_id), user_id)
        exigir_processado(inventario)
        conteudo = await self._inventarios.carregar_conteudo(inventario)
        analise = analisar_topologia(conteudo, para_dominio(inventario.declaracoes))
        if topologia.get("fingerprint") and analise.fingerprint != topologia["fingerprint"]:
            raise TopologiaDivergenteError(
                "o inventário mudou desde a análise; refaça a análise com ele"
            )

        custos = atribuir_por_tipo(
            conteudo,
            await self._custos.custos_por_recurso(job.report_id, user_id),
            unidade_de=analise.unidade_de.get,
        )
        correspondencia = avaliar_correspondencia(conteudo, custos, correlacionar(conteudo, custos))
        if correspondencia.status == SEM_CORRESPONDENCIA:
            raise InventarioNaoCorrespondeError(correspondencia)
        fx = _fx(payload)
        indice = Indice(conteudo)
        apelidos = IndiceDeApelidos(conteudo)

        por_unidade: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: _ZERO))
        sem_unidade: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        servicos_do_recurso: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(lambda: _ZERO)
        )
        custo_do_recurso: dict[str, Decimal] = defaultdict(lambda: _ZERO)
        linhas = []
        for linha in custos.por_recurso:
            valor = convert(linha.custo, fx) or _ZERO
            chave = apelidos.casar(linha.resource_id)
            unidade = analise.unidade_de.get(chave) if chave else None
            if chave is None or unidade is None:
                sem_unidade[linha.servico] += valor
                continue
            por_unidade[unidade][linha.servico] += valor
            servicos_do_recurso[chave][linha.servico] += valor
            custo_do_recurso[chave] += valor
            recurso = indice.recursos[chave]
            tipo = indice.tipo(chave)
            linhas.append(
                LinhaDoEscopo(
                    servico=linha.servico,
                    custo=valor,
                    tipo=tipo,
                    familia=(
                        extract_family(str(recurso.atributos.get("tipo_maquina") or ""))
                        if tipo is TipoDeRecurso.INSTANCIA
                        else None
                    ),
                    regiao=recurso.regiao or linha.regiao,
                    tipo_disco=(
                        str(recurso.atributos.get("tipo_disco"))
                        if recurso.atributos.get("tipo_disco")
                        else None
                    ),
                    unidade=unidade,
                    recurso=chave,
                )
            )

        # O serviço de um recurso é o que mais custa nele (a VM e o disco dela
        # podem vir em linhas separadas do mesmo serviço)
        servico_de = {
            chave: max(servicos.items(), key=lambda kv: (kv[1], kv[0]))[0]
            for chave, servicos in servicos_do_recurso.items()
        }
        deps = dependencias_de_servico(indice, analise.unidade_de, servico_de)
        # Recurso sem linha na fatura fica com o serviço do tipo dele (o bucket gera S3):
        # é o que recorta o escopo de "só o serviço"
        servico_do_recurso = dict(servico_de)
        for chave, recurso in indice.recursos.items():
            if chave not in servico_do_recurso and (
                pelo_tipo := servicos_pelo_tipo(conteudo.provedor, (recurso,))
            ):
                servico_do_recurso[chave] = min(pelo_tipo)
        chave_aplicacao = next(
            (p.valor for p in analise.premissas if p.campo == "chave_aplicacao"), None
        )
        # Linha que a fatura não liga a recurso e que o tipo não resolveu: custo do
        # serviço sem aplicação, para o catálogo mostrar a fatura inteira
        for resto in custos.sem_recurso:
            sem_unidade[resto.servico] += convert(resto.custo, fx) or _ZERO
        servicos_da_fatura = set(sem_unidade) | {s for c in por_unidade.values() for s in c}
        presentes = {
            u.id: servicos & servicos_da_fatura
            for u in analise.unidades
            if (
                servicos := servicos_pelo_tipo(
                    conteudo.provedor,
                    (indice.recursos[c] for c in u.recursos if c in indice.recursos),
                )
            )
        }
        catalogo = montar_catalogo(
            analise,
            por_unidade,
            sem_unidade,
            deps,
            chave_aplicacao,
            servicos_presentes=presentes,
            servico_do_recurso=servico_do_recurso,
            deps_da_fronteira=dependencias_de_servico(
                indice, analise.unidade_de, servico_do_recurso
            ),
        )

        pools = tuple(_pools(payload))
        provedores = tuple(
            sorted({prov for p in pools for prov in p.destino} | set(_taxas(payload)))
        )
        resumo = payload.get("resumo_executivo") or {}
        return ContextoDoEscopo(
            catalogo=catalogo,
            linhas=tuple(linhas),
            pools=pools,
            taxas_de_categoria=_taxas(payload),
            provedores=provedores,
            provedor_origem=payload.get("provedor_origem"),
            provedor_recomendado=_texto(resumo.get("recomendado")),
            moeda=str(payload.get("moeda") or "USD"),
            inventario_id=str(inventario_id),
            correspondencia=correspondencia,
            premissas=tuple((p.campo, p.valor, p.base) for p in analise.premissas),
            recursos={
                chave: _recurso_do_escopo(chave, r, indice, custo_do_recurso.get(chave, _ZERO))
                for u in analise.unidades
                for chave in u.recursos
                if (r := indice.recursos.get(chave)) is not None
            },
            # Componente e zona sem aplicação: só migram pela escolha do serviço deles
            sem_aplicacao={
                chave: _recurso_do_escopo(chave, r, indice, _ZERO)
                for chave, r in indice.recursos.items()
                if chave not in analise.unidade_de
                and chave in servico_do_recurso
                and _componente(r) is not None
            },
            periodo=_texto_cru(payload.get("periodo_faturamento")),
            cargas_no_cluster=cargas_no_cluster(conteudo, analise),
            no_inventario=_no_inventario(indice, servico_do_recurso),
            gasto_total=_decimal(payload.get("gasto_total")),
        )

    async def _job(self, user_id: UUID, job_id: UUID) -> AnalysisJob:
        job = await self._jobs.get_by_id(job_id, user_id)
        if job is None:
            raise NotFoundError(f"análise {job_id} não encontrada")
        if job.status == AnalysisStatus.FAILED:
            raise AnaliseFalhouError(f"a análise {job_id} terminou com falha")
        if job.status != AnalysisStatus.COMPLETED or not job.llm_input_payload:
            raise AnaliseNaoConcluidaError(
                f"a análise {job_id} ainda não foi concluída (status {job.status})"
            )
        return job


def _texto(valor: object) -> str | None:
    return str(valor).lower() if valor else None


def _texto_cru(valor: object) -> str | None:
    return str(valor) if valor else None


def _decimal(valor: object) -> Decimal | None:
    try:
        return Decimal(str(valor)) if valor is not None else None
    except ArithmeticError:
        return None


def _fx(payload: Mapping[str, Any]) -> FxRate:
    """A taxa que o payload inteiro usou (a mesma de conversor_da_analise)."""
    fx = (payload.get("premissas") or {}).get("fx") or {}
    taxa = _decimal(fx.get("taxa"))
    if not taxa:
        return identity_rate(str(payload.get("moeda") or "USD"))
    return FxRate(par=fx.get("par", "?"), taxa=taxa, fonte=fx.get("fonte", "config"))


def _pools(payload: Mapping[str, Any]) -> list[PoolCotado]:
    """Os pools que a análise cotou, já na moeda da análise."""
    pools = []
    for p in payload.get("pools_computacao") or []:
        origem = _decimal(p.get("custo_computacao"))
        if not p.get("familia") or origem is None:
            continue
        destino = {}
        for e in p.get("equivalentes") or []:
            custo = _decimal((e.get("financeiro") or {}).get("custo_mensal_estimado"))
            if e.get("provedor") and custo is not None:
                destino[str(e["provedor"]).lower()] = custo
        pools.append(
            PoolCotado(
                familia=str(p["familia"]),
                regiao=p.get("regiao"),
                custo_origem=origem,
                destino=destino,
            )
        )
    return pools


def _taxas(payload: Mapping[str, Any]) -> dict[str, dict[str, Decimal]]:
    """Razão de preço de cada categoria no destino, por provedor: o custo estimado da
    categoria ÷ o custo dela na origem, como a análise cotou.

    A razão das taxas (destino ÷ origem) só vale quando a categoria inteira foi cotada
    por uma taxa: no banco, a taxa da origem é o preço efetivo da vCPU-hora e não serve
    para o storage dele; no egress, ela não desconta a franquia do destino
    (PLANO_PRECISAO_OCI). Sem o custo na entrada, vale a razão das taxas."""
    auxiliares = payload.get("custos_auxiliares") or {}
    taxas: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for chave, categoria in _CATEGORIAS.items():
        for e in auxiliares.get(chave) or []:
            if not e.get("provedor"):
                continue
            razao = _razao_do_custo(e)
            if razao is None:
                origem, destino = _decimal(e.get("taxa_origem")), _decimal(e.get("taxa_destino"))
                razao = destino / origem if origem and destino is not None else None
            if razao is not None:
                taxas[str(e["provedor"]).lower()][categoria] = razao
    return dict(taxas)


def _razao_do_custo(e: Mapping[str, Any]) -> Decimal | None:
    estimado, delta = _decimal(e.get("custo_mensal_estimado")), _decimal(e.get("delta_custo"))
    if estimado is None or delta is None:
        return None
    origem = estimado - delta
    return estimado / origem if origem > 0 else None


def recorte_do_plano(cotado: PlanoCotado) -> RecorteDoEscopo:
    """As unidades do plano → máquinas por (subrede, família), bancos e custo por categoria."""
    ctx, plano = cotado.contexto, cotado.plano
    unidades = set(plano.unidades)
    so_o_servico = plano.escolha.somente_os_servicos
    escolhidos = plano.escolha.servicos
    servico_de = ctx.catalogo.servico_do_recurso
    chaves = {
        chave
        for u in ctx.catalogo.analise.unidades
        if u.id in unidades
        for chave in u.recursos
        if not so_o_servico or servico_de.get(chave) in escolhidos
    }
    recursos = [ctx.recursos[c] for c in sorted(chaves) if c in ctx.recursos]
    maquinas: Counter[tuple[str | None, str]] = Counter()
    bancos: Counter[str | None] = Counter()
    no_escopo: Counter[tuple[str | None, str]] = Counter()
    na_conta: Counter[tuple[str | None, str]] = Counter()
    for r in ctx.recursos.values():
        if r.tipo == TipoDeRecurso.INSTANCIA and r.familia:
            na_conta[(r.regiao, r.familia.upper())] += 1
    for r in recursos:
        if r.tipo == TipoDeRecurso.INSTANCIA and r.familia:
            maquinas[(r.subredes[0] if r.subredes else None, r.familia.upper())] += 1
            no_escopo[(r.regiao, r.familia.upper())] += 1
        elif r.tipo == TipoDeRecurso.BANCO:
            bancos[r.classe] += 1
    custos: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    for linha in ctx.linhas:
        if linha.unidade not in unidades or linha.recurso is None:
            continue
        if so_o_servico and linha.servico not in escolhidos:
            continue
        recurso = ctx.recursos.get(linha.recurso)
        if linha.tipo is TipoDeRecurso.DISCO:
            hdd = recurso is not None and recurso.categoria_disco == "hdd"
            custos["armazenamento_hdd" if hdd else "armazenamento_ssd"] += linha.custo
        elif linha.tipo is TipoDeRecurso.BUCKET:
            custos["armazenamento_objetos"] += linha.custo
        elif linha.tipo is TipoDeRecurso.BANCO:
            custos["banco_dados"] += linha.custo
    nomes = {g.id: g.nome for g in ctx.catalogo.grupos}

    def ligacoes(vizinhos: tuple[GrupoVizinho, ...]) -> tuple[LigacaoEntreNuvens, ...]:
        return tuple(LigacaoEntreNuvens(g.nome or g.grupo, g.ordem, g.portas) for g in vizinhos)

    return RecorteDoEscopo(
        aplicacoes=plano.aplicacoes,
        unidades=len(unidades),
        maquinas=dict(maquinas),
        bancos=frozenset(r.chave for r in recursos if r.tipo == TipoDeRecurso.BANCO),
        bancos_por_classe=dict(bancos),
        por_familia={k: (no_escopo[k], n) for k, n in na_conta.items()},
        custos=dict(custos),
        ordens=tuple((o.ordem, tuple(nomes.get(g, g) for g in o.grupos)) for o in plano.ordens),
        antes=ligacoes(plano.antes),
        para_tras=ligacoes(plano.para_tras),
        avisos=(
            _fora_do_pacote(
                escolhidos,
                [
                    *recursos,
                    *(r for c, r in ctx.sem_aplicacao.items() if servico_de.get(c) in escolhidos),
                ],
                servico_de,
                ctx.no_inventario,
            )
            if so_o_servico
            else ()
        ),
        somente_os_servicos=so_o_servico,
        servicos=tuple(sorted(escolhidos)) if so_o_servico else (),
        fronteira=plano.fronteira,
        buckets=tuple(
            BucketDoEscopo(nome=r.nome or r.chave, custo=r.custo)
            for r in recursos
            if r.tipo == TipoDeRecurso.BUCKET
        ),
        clusters=_clusters(recursos),
        componentes=_componentes(
            recursos,
            [r for c, r in ctx.sem_aplicacao.items() if servico_de.get(c) in escolhidos]
            if so_o_servico
            else [],
            servico_de,
        ),
    )


# O que o pacote cria como serviço gerenciado (itens 15 e 19)
_COMPONENTES_DO_PACOTE = frozenset({"registro_de_imagens", "segredo", ZONA_DNS})


def _clusters(recursos: list[RecursoDoEscopo]) -> tuple[ClusterDoEscopo, ...]:
    """Os clusters do escopo com os nós dele agrupados pelo grupo de nós (ou pela família)."""
    clusters = []
    for c in sorted(
        (r for r in recursos if r.tipo == TipoDeRecurso.CLUSTER_KUBERNETES), key=lambda r: r.chave
    ):
        grupos: Counter[tuple[str, str, str | None]] = Counter()
        da_familia: Counter[str] = Counter(
            r.familia.upper() for r in recursos if r.tipo == TipoDeRecurso.INSTANCIA and r.familia
        )
        for r in recursos:
            if r.cluster == c.chave and r.tipo == TipoDeRecurso.INSTANCIA and r.familia:
                familia = r.familia.upper()
                grupos[(r.grupo_de_nos or familia.lower(), familia, r.regiao)] += 1
        clusters.append(
            ClusterDoEscopo(
                nome=c.nome or c.chave,
                versao=c.versao,
                grupos=tuple(
                    GrupoDeNos(nome=n, familia=f, regiao=reg, maquinas=q, da_familia=da_familia[f])
                    for (n, f, reg), q in sorted(grupos.items(), key=lambda kv: kv[0][:2])
                ),
                balanceadores=tuple(
                    sorted(
                        r.nome or r.chave
                        for r in recursos
                        if r.cluster == c.chave and r.tipo == TipoDeRecurso.BALANCEADOR
                    )
                ),
            )
        )
    return tuple(clusters)


def _componentes(
    recursos: list[RecursoDoEscopo],
    sem_aplicacao: list[RecursoDoEscopo],
    servico_de: Mapping[str, str],
) -> tuple[ComponenteDoEscopo, ...]:
    vistos: set[str] = set()
    componentes = []
    for r in [*recursos, *sem_aplicacao]:
        if r.componente in _COMPONENTES_DO_PACOTE and r.chave not in vistos:
            vistos.add(r.chave)
            componentes.append(
                ComponenteDoEscopo(r.componente, r.nome or r.chave, servico_de.get(r.chave))
            )
    return tuple(componentes)


# O que o pacote Terraform cria; o resto de um serviço escolhido fica de fora
_PROVISIONADOS = frozenset(
    {
        TipoDeRecurso.INSTANCIA,
        TipoDeRecurso.DISCO,
        TipoDeRecurso.BANCO,
        TipoDeRecurso.BUCKET,
        TipoDeRecurso.CLUSTER_KUBERNETES,
    }
)


def _provisionado(r: RecursoDoEscopo) -> bool:
    """Criado pelo pacote: os tipos de sempre, o cluster, o balanceador que nasce de um
    Service do cluster e os componentes que viram recurso (itens 15 e 19)."""
    return (
        TipoDeRecurso(r.tipo) in _PROVISIONADOS
        or (r.tipo == TipoDeRecurso.BALANCEADOR and r.cluster is not None)
        or r.componente in _COMPONENTES_DO_PACOTE
    )


def _no_inventario(
    indice: Indice, servico_do_recurso: Mapping[str, str]
) -> dict[str, ServicoNoInventario]:
    nativos: dict[str, Counter[str]] = defaultdict(Counter)
    fundacao: dict[str, bool] = {}
    for chave, recurso in indice.recursos.items():
        servico = servico_do_recurso.get(chave)
        if servico is None:
            continue
        nativos[servico][recurso.tipo_nativo] += 1
        fundacao[servico] = fundacao.get(servico, False) or recurso.tipo in FUNDACAO
    return {
        s: ServicoNoInventario(
            tuple(sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))), fundacao[s]
        )
        for s, c in nativos.items()
    }


# Tipos nativos citados por serviço na lista do que fica fora
_NATIVOS_CITADOS = 3


def _texto_dos_nativos(nativos: tuple[tuple[str, int], ...]) -> str:
    texto = ", ".join(f"{n} {tipo}" for tipo, n in nativos[:_NATIVOS_CITADOS])
    return texto + ("…" if len(nativos) > _NATIVOS_CITADOS else "")


def _fora_do_pacote(
    escolhidos: frozenset[str],
    recursos: list[RecursoDoEscopo],
    servico_de: Mapping[str, str],
    no_inventario: Mapping[str, ServicoNoInventario],
) -> tuple[str, ...]:
    """Serviço escolhido que o pacote não cria: dito na resposta e no README.

    Sem recurso nas aplicações do escopo há três casos, e cada um tem a sua frase: o
    recurso existe fora das aplicações, é a fundação de rede (que o espelho cria) ou o
    inventário não tem nada dele."""
    avisos = []
    for servico in sorted(escolhidos):
        tipos = {r.tipo for r in recursos if servico_de.get(r.chave) == servico}
        if not tipos:
            achado = no_inventario.get(servico)
            if achado is not None and achado.fundacao:
                avisos.append(
                    f"{servico}: a rede de origem ({_texto_dos_nativos(achado.nativos)}) vira "
                    "a rede do pacote pelo espelho, só com as subredes que as máquinas e os "
                    "bancos usam; gateways (internet, NAT, VPN) não são criados."
                )
            elif achado is not None:
                avisos.append(
                    f"{servico}: {_texto_dos_nativos(achado.nativos)} no inventário, fora das "
                    "aplicações do escopo; o pacote não o cria."
                )
            else:
                avisos.append(
                    f"{servico}: nenhum recurso dele no inventário. É serviço da conta "
                    "(gestão, cobrança), sem infraestrutura própria, ou um tipo que o "
                    "inventário não lista; o pacote não o cria."
                )
        elif not any(_provisionado(r) for r in recursos if servico_de.get(r.chave) == servico):
            avisos.append(
                f"{servico} não vira recurso neste pacote (o Terraform cria máquina, disco, "
                "banco, bucket, cluster Kubernetes, registro de imagens, cofre de segredos e "
                "zona de DNS): crie o equivalente dele no destino à parte."
            )
    return tuple(avisos)
