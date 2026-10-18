"""Respostas de `GET …/applications` e `POST …/migration-plans` (PLANO_ESCOPO_DE_MIGRACAO).

O cálculo é do backend; a tela só mostra. Os textos de motivo saem prontos para
exibir. "Ordem" é a onda de migração da topologia: 1 vai primeiro, quem depende
dela vem depois, `null` não depende de ninguém.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.services.cotacao_do_escopo import CotacaoDoEscopo
from app.domain.services.topologia.escopo import (
    GrupoIncluido,
    GrupoVizinho,
    ServicoLigado,
    texto_da_fronteira,
)
from app.use_cases.analysis.escopo_de_migracao import (
    EXPLICACAO_DA_ORDEM,
    LIMITE_DAS_DEPENDENCIAS,
    ContextoDoEscopo,
    PlanoCotado,
)

VERSAO = "escopo-de-migracao-v1"
Natureza = Literal["dados", "servico", "encaminhamento", "declarada", "administrativo"]


def _dinheiro(valor: Decimal) -> float:
    return float(valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class PremissaOut(BaseModel):
    campo: str
    valor: str | None
    base: str


def _premissas(ctx: ContextoDoEscopo) -> list[PremissaOut]:
    return [
        *(PremissaOut(campo=c, valor=v, base=b) for c, v, b in ctx.premissas),
        PremissaOut(campo="ordem", valor=None, base=EXPLICACAO_DA_ORDEM),
    ]


class CorrespondenciaOut(BaseModel):
    """O inventário casa com a fatura?

    `liga_custo=false`: sem custo por aplicação. `estimado`: a fatura não traz o ID de
    cada recurso e o custo foi ligado pelo tipo (`fracao_estimada` do gasto).
    """

    status: Literal["ok", "parcial", "estimado", "sem_id_de_recurso"]
    liga_custo: bool
    fracao_encontrada: float | None
    fracao_estimada: float | None = None
    contas_da_fatura: list[str]
    contas_do_inventario: list[str]
    contas_sem_inventario: list[str]
    mensagem: str

    @classmethod
    def de(cls, ctx: ContextoDoEscopo) -> CorrespondenciaOut:
        c = ctx.correspondencia
        return cls.model_validate(
            {
                "status": c.status,
                "liga_custo": c.liga_custo,
                "fracao_encontrada": (
                    float(c.fracao_encontrada) if c.fracao_encontrada is not None else None
                ),
                "fracao_estimada": (
                    float(round(c.fracao_estimada, 4)) if c.fracao_estimada is not None else None
                ),
                "contas_da_fatura": list(c.contas_da_fatura),
                "contas_do_inventario": list(c.contas_do_inventario),
                "contas_sem_inventario": list(c.contas_sem_inventario),
                "mensagem": c.mensagem,
            }
        )


class CustoPorNome(BaseModel):
    nome: str
    custo: float


class ServicoLigadoOut(BaseModel):
    servico: str
    natureza: Natureza
    portas: list[str]

    @classmethod
    def de(cls, s: ServicoLigado) -> ServicoLigadoOut:
        return cls(servico=s.servico, natureza=s.natureza.value, portas=list(s.portas))


class CargaNoClusterOut(BaseModel):
    nome: str
    fontes: list[str] = Field(description="imagem, segredo, service discovery, balanceador")


class ClusterDaAplicacaoOut(BaseModel):
    """Indício do que roda dentro do cluster: nomes de imagens, segredos e serviços.
    O inventário da nuvem não vê o Kubernetes por dentro; não vira unidade nem custo."""

    cluster: str
    cargas: list[CargaNoClusterOut]
    namespaces: list[str]


class AplicacaoOut(BaseModel):
    nome: str
    ambientes: list[str]
    regioes: list[str]
    custo: float
    recursos: int
    ordens: list[int] = Field(description="ordem de migração dos grupos dela; vazio = livre")
    migra_junto_com: list[str] = Field(
        description="outras aplicações no mesmo grupo de migração (dependência forte)"
    )
    servicos: list[CustoPorNome]
    clusters: list[ClusterDaAplicacaoOut] = Field(
        default_factory=list, description="prováveis aplicações dentro dos clusters dela"
    )


class UsoDoServicoOut(BaseModel):
    nome: str
    aplicacoes: list[CustoPorNome]
    sem_aplicacao: float = Field(description="custo do serviço que não casa com aplicação")
    depende_de: list[ServicoLigadoOut]
    usado_por: list[ServicoLigadoOut]


class GrupoOut(BaseModel):
    id: str
    nome: str
    ordem: int | None
    aplicacoes: list[str]
    custo: float


class ApplicationsResponse(BaseModel):
    versao: str = VERSAO
    moeda: str
    provedor_origem: str | None
    provedor_recomendado: str | None
    inventario_id: str
    chave_aplicacao: str | None = Field(
        description="tag de onde vem a aplicação; null = sem tag, cada unidade conta como uma"
    )
    aplicacoes: list[AplicacaoOut]
    servicos: list[UsoDoServicoOut]
    grupos: list[GrupoOut]
    ferramentas: list[str] = Field(description="acesso (bastion): fora das ondas e do plano")
    sem_aplicacao: float
    correspondencia: CorrespondenciaOut
    limite_das_dependencias: str = LIMITE_DAS_DEPENDENCIAS
    premissas: list[PremissaOut]

    @classmethod
    def de(cls, ctx: ContextoDoEscopo) -> ApplicationsResponse:
        c = ctx.catalogo
        return cls(
            moeda=ctx.moeda,
            provedor_origem=ctx.provedor_origem,
            provedor_recomendado=ctx.provedor_recomendado,
            inventario_id=ctx.inventario_id,
            chave_aplicacao=c.chave_aplicacao,
            aplicacoes=[
                AplicacaoOut(
                    nome=a.nome,
                    ambientes=list(a.ambientes),
                    regioes=list(a.regioes),
                    custo=_dinheiro(a.custo),
                    recursos=a.recursos,
                    ordens=list(a.ordens),
                    migra_junto_com=list(a.migra_junto_com),
                    servicos=[CustoPorNome(nome=n, custo=_dinheiro(v)) for n, v in a.servicos],
                    clusters=[
                        ClusterDaAplicacaoOut(
                            cluster=k.cluster,
                            cargas=[
                                CargaNoClusterOut(nome=x.nome, fontes=list(x.fontes))
                                for x in k.cargas
                            ],
                            namespaces=list(k.namespaces),
                        )
                        for u in a.unidades
                        if (k := ctx.cargas_no_cluster.get(u)) is not None
                    ],
                )
                for a in c.aplicacoes
            ],
            servicos=[
                UsoDoServicoOut(
                    nome=s.nome,
                    aplicacoes=[CustoPorNome(nome=n, custo=_dinheiro(v)) for n, v in s.aplicacoes],
                    sem_aplicacao=_dinheiro(s.sem_aplicacao),
                    depende_de=[ServicoLigadoOut.de(x) for x in s.depende_de],
                    usado_por=[ServicoLigadoOut.de(x) for x in s.usado_por],
                )
                for s in c.servicos
            ],
            grupos=[
                GrupoOut(
                    id=g.id,
                    nome=g.nome,
                    ordem=g.ordem,
                    aplicacoes=list(g.aplicacoes),
                    custo=_dinheiro(g.custo),
                )
                for g in c.grupos
            ],
            ferramentas=list(c.ferramentas),
            sem_aplicacao=_dinheiro(c.sem_aplicacao),
            correspondencia=CorrespondenciaOut.de(ctx),
            premissas=_premissas(ctx),
        )


# ── plano ───────────────────────────────────────────────────────────────


_SOMENTE_OS_SERVICOS = (
    "true = migra só os serviços escolhidos, sem as aplicações inteiras que os usam; o que "
    "o recorte corta volta em `fronteira`. Não combina com `aplicacoes` nem `incluir_grupos`"
)


class MigrationPlanRequest(BaseModel):
    aplicacoes: list[str] = Field(default_factory=list)
    servicos: list[str] = Field(default_factory=list)
    ambiente: str | None = Field(default=None, description="null = todos os ambientes")
    incluir_grupos: list[str] = Field(
        default_factory=list, description="ids de grupos de `antes`/`para_tras` incluídos à mão"
    )
    somente_os_servicos: bool = Field(default=False, description=_SOMENTE_OS_SERVICOS)


class EscolhaOut(BaseModel):
    aplicacoes: list[str]
    servicos: list[str]
    ambiente: str | None
    incluir_grupos: list[str]
    somente_os_servicos: bool = False


class FronteiraOut(BaseModel):
    """Dependência que o escopo de só o serviço corta: um lado migra, o outro fica."""

    sentido: Literal["sai", "entra"] = Field(
        description="sai: o que migra chama o que fica; entra: o que fica chama o que migra"
    )
    de: str
    para: str
    natureza: Natureza
    portas: list[str]
    forte: bool = Field(description="dados ou encaminhamento: para a aplicação sem conexão")
    texto: str = Field(description="o que quebra e o que fazer, pronto para exibir")


class UnidadeQueVaiJuntoOut(BaseModel):
    unidade: str
    nome: str
    aplicacao: str
    custo: float
    motivo: str
    incluida_por_voce: bool
    grupo: str


class ServicoQueVemJuntoOut(BaseModel):
    servico: str
    custo: float
    motivo: str


class GrupoVizinhoOut(BaseModel):
    grupo: str
    nome: str
    ordem: int | None
    naturezas: list[Natureza]
    portas: list[str]
    custo: float
    nos_dois_sentidos: bool
    ligado_a: list[str] = Field(description="grupos do escopo com que este conversa")

    @classmethod
    def de(cls, v: GrupoVizinho) -> GrupoVizinhoOut:
        return cls(
            grupo=v.grupo,
            nome=v.nome,
            ordem=v.ordem,
            naturezas=[n.value for n in v.naturezas],
            portas=list(v.portas),
            custo=_dinheiro(v.custo),
            nos_dois_sentidos=v.nos_dois_sentidos,
            ligado_a=list(v.ligado_a),
        )


class GrupoIncluidoOut(BaseModel):
    """Grupo de fora posto no plano à mão, com o lado da migração em que fica."""

    grupo: str
    nome: str
    lado: Literal["antes", "depois", "sem_ligacao"] = Field(
        description="antes: pré-migração (o escopo o chama); depois: pós-migração (ele "
        "chama o escopo); sem_ligacao: o que o ligava ao plano saiu"
    )
    ordem: int | None
    custo: float
    aplicacoes: list[str]
    ligado_a: list[str] = Field(
        description="pré-migração: grupos do plano que o chamam; pós: grupos do plano que ele chama"
    )
    naturezas: list[Natureza]
    portas: list[str]

    @classmethod
    def de(cls, i: GrupoIncluido) -> GrupoIncluidoOut:
        return cls(
            grupo=i.grupo,
            nome=i.nome,
            lado=i.lado,
            ordem=i.ordem,
            custo=_dinheiro(i.custo),
            aplicacoes=list(i.aplicacoes),
            ligado_a=list(i.ligado_a),
            naturezas=[n.value for n in i.naturezas],
            portas=list(i.portas),
        )


Bloco = Literal["pre", "principal", "pos"]
_BLOCO_DO_LADO: dict[str, Bloco] = {"antes": "pre", "depois": "pos", "sem_ligacao": "principal"}
# Recursos por unidade na resposta: os mais caros; a lista inteira vai na planilha
RECURSOS_POR_UNIDADE = 10


class GrupoNoPlanoOut(BaseModel):
    """Um grupo de migração do plano e o bloco em que migra."""

    grupo: str
    nome: str
    ordem: int | None = Field(description="etapa da conta; null = sem ordem")
    custo: float
    bloco: Bloco = Field(
        description="pre: incluído e o plano o chama; pos: incluído e ele chama o plano; "
        "principal: o escolhido e o que vai junto"
    )


class ConteudoOut(BaseModel):
    tipo: str = Field(description="tipo normalizado (instancia, banco, disco…)")
    tipo_nativo: str = Field(description="tipo na nuvem de origem (AWS::RDS::DBInstance…)")
    quantidade: int


class RecursoDaUnidadeOut(BaseModel):
    nome: str
    tipo: str
    tipo_nativo: str
    custo: float = Field(description="custo do mês da fatura; 0 quando ela não cita o recurso")


class UnidadeNoPlanoOut(BaseModel):
    """O que é cada unidade do plano: quem é, o papel e o que tem dentro."""

    unidade: str
    nome: str
    aplicacao: str | None
    ambiente: str | None
    regiao: str | None
    conta: str | None
    grupo: str
    papel: Literal["escolhido", "vai_junto", "incluido"]
    motivo: str | None = Field(description="por que vai junto (dependência forte)")
    custo: float
    conteudo: list[ConteudoOut] = Field(description="quantos recursos de cada tipo, do maior grupo")
    recursos: list[RecursoDaUnidadeOut] = Field(
        description=f"os {RECURSOS_POR_UNIDADE} recursos mais caros; a planilha tem todos"
    )
    recursos_total: int
    clusters: list[ClusterDaAplicacaoOut] = Field(
        default_factory=list,
        description="prováveis aplicações dentro do cluster da unidade (indício: migram com ele)",
    )


def _composicao(r: PlanoCotado) -> tuple[list[GrupoNoPlanoOut], list[UnidadeNoPlanoOut], int]:
    """Grupos e unidades do plano, cada um com o bloco e o papel; e quantas etapas a conta tem."""
    p, ctx = r.plano, r.contexto
    analise = ctx.catalogo.analise
    unidades = {u.id: u for u in analise.unidades}
    nome_do_grupo = {g.id: g.nome for g in analise.grupos}
    ordem_do_grupo = {g: o.numero for o in analise.ondas for g in o.grupos}
    bloco_do_grupo = {i.grupo: _BLOCO_DO_LADO[i.lado] for i in p.incluidos}
    junto = {v.unidade: v for v in p.vao_junto}

    def custo(u: str) -> Decimal:
        return sum(ctx.catalogo.custo_por_unidade.get(u, {}).values(), Decimal(0))

    grupos: dict[str, Decimal] = {}
    saida = []
    for u in p.unidades:
        unidade = unidades[u]
        g = analise.grupo_de.get(u, u)
        grupos[g] = grupos.get(g, Decimal(0)) + custo(u)
        v = junto.get(u)
        papel: Literal["escolhido", "vai_junto", "incluido"] = (
            "escolhido" if v is None else "incluido" if v.incluida_por_voce else "vai_junto"
        )
        recursos = [ctx.recursos[k] for k in unidade.recursos if k in ctx.recursos]
        contagem: dict[tuple[str, str], int] = {}
        for x in recursos:
            contagem[(x.tipo, x.tipo_nativo)] = contagem.get((x.tipo, x.tipo_nativo), 0) + 1
        saida.append(
            UnidadeNoPlanoOut(
                unidade=u,
                nome=unidade.nome,
                aplicacao=unidade.aplicacao,
                ambiente=unidade.ambiente,
                regiao=unidade.regiao,
                conta=unidade.conta,
                grupo=g,
                papel=papel,
                motivo=v.motivo if papel == "vai_junto" and v else None,
                custo=_dinheiro(custo(u)),
                conteudo=[
                    ConteudoOut(tipo=tipo, tipo_nativo=nativo, quantidade=n)
                    for (tipo, nativo), n in sorted(
                        contagem.items(), key=lambda kv: (-kv[1], kv[0])
                    )
                ],
                recursos=[
                    RecursoDaUnidadeOut(
                        nome=x.nome or x.chave.rsplit("/", 1)[-1],
                        tipo=x.tipo,
                        tipo_nativo=x.tipo_nativo,
                        custo=_dinheiro(x.custo),
                    )
                    for x in sorted(recursos, key=lambda x: (-x.custo, x.nome or x.chave))[
                        :RECURSOS_POR_UNIDADE
                    ]
                ],
                recursos_total=len(recursos),
                clusters=(
                    [
                        ClusterDaAplicacaoOut(
                            cluster=k.cluster,
                            cargas=[
                                CargaNoClusterOut(nome=x.nome, fontes=list(x.fontes))
                                for x in k.cargas
                            ],
                            namespaces=list(k.namespaces),
                        )
                    ]
                    if (k := ctx.cargas_no_cluster.get(u)) is not None and k.cargas
                    else []
                ),
            )
        )
    ordem_do_bloco = {"pre": 0, "principal": 1, "pos": 2}
    grupos_out = sorted(
        (
            GrupoNoPlanoOut(
                grupo=g,
                nome=nome_do_grupo.get(g, g),
                ordem=ordem_do_grupo.get(g),
                custo=_dinheiro(c),
                bloco=bloco_do_grupo.get(g, "principal"),
            )
            for g, c in grupos.items()
        ),
        key=lambda g: (ordem_do_bloco[g.bloco], g.ordem is None, g.ordem or 0, g.nome),
    )
    papel_primeiro = {"escolhido": 0, "incluido": 1, "vai_junto": 2}
    saida.sort(key=lambda u: (u.grupo, papel_primeiro[u.papel], -u.custo, u.nome))
    return grupos_out, saida, max((o.numero for o in analise.ondas), default=0)


class LigacaoOut(BaseModel):
    de: str
    para: str
    natureza: Natureza
    portas: list[str]


class OrdemOut(BaseModel):
    ordem: int | None
    grupos: list[str]


class ServicoNoEscopoOut(BaseModel):
    nome: str
    custo: float
    destino: dict[str, float | None] = Field(
        description="provedor → custo estimado; null = sem cotação"
    )
    metodos: list[str] = Field(
        description="pool | pool_da_familia | media_dos_pools | categoria; vazio = sem cotação"
    )
    sem_cotacao: dict[str, str] = Field(
        default_factory=dict,
        description="provedor → por que parte do serviço ficou sem cotação, pronto para exibir",
    )


class TotalNoDestinoOut(BaseModel):
    provedor: str
    custo_estimado: float
    custo_origem_cotado: float = Field(description="a parte do escopo que tem cotação")
    sem_cotacao: float
    variacao: float | None = Field(description="custo_estimado ÷ custo_origem_cotado - 1")


class MigrationPlanResponse(BaseModel):
    versao: str = VERSAO
    moeda: str
    provedor_origem: str | None
    provedor_recomendado: str | None
    escolha: EscolhaOut
    aplicacoes: list[str]
    unidades: int
    recursos: int
    custo: float
    servicos: list[ServicoNoEscopoOut]
    destino: list[TotalNoDestinoOut]
    vao_junto: list[UnidadeQueVaiJuntoOut]
    servicos_que_vem_junto: list[ServicoQueVemJuntoOut]
    antes: list[GrupoVizinhoOut]
    para_tras: list[GrupoVizinhoOut]
    incluidos: list[GrupoIncluidoOut]
    grupos_do_plano: list[GrupoNoPlanoOut] = Field(
        description="cada grupo do plano com o bloco (pré, principal, pós) e a etapa"
    )
    unidades_do_plano: list[UnidadeNoPlanoOut] = Field(
        description="cada unidade com o papel, o que tem dentro e os recursos mais caros"
    )
    etapas_da_conta: int = Field(description="quantas etapas a ordem de migração da conta tem")
    ligacoes: list[LigacaoOut]
    ordens: list[OrdemOut]
    fronteira: list[FronteiraOut] = Field(
        default_factory=list,
        description="só o serviço: cada dependência que o recorte corta, com o que fazer",
    )
    fora_do_plano: list[CustoPorNome]
    correspondencia: CorrespondenciaOut
    limite_das_dependencias: str = LIMITE_DAS_DEPENDENCIAS
    premissas: list[PremissaOut]

    @classmethod
    def de(cls, r: PlanoCotado) -> MigrationPlanResponse:
        p, ctx = r.plano, r.contexto
        grupos, unidades, etapas = _composicao(r)
        return cls(
            moeda=ctx.moeda,
            provedor_origem=ctx.provedor_origem,
            provedor_recomendado=ctx.provedor_recomendado,
            escolha=EscolhaOut(
                aplicacoes=sorted(p.escolha.aplicacoes),
                servicos=sorted(p.escolha.servicos),
                ambiente=p.escolha.ambiente,
                incluir_grupos=sorted(p.escolha.incluir_grupos),
                somente_os_servicos=p.escolha.somente_os_servicos,
            ),
            aplicacoes=list(p.aplicacoes),
            unidades=len(p.unidades),
            recursos=p.recursos,
            custo=_dinheiro(p.custo),
            servicos=_servicos(r.cotacao),
            destino=[
                TotalNoDestinoOut(
                    provedor=t.provedor,
                    custo_estimado=_dinheiro(t.custo_estimado),
                    custo_origem_cotado=_dinheiro(t.custo_origem_cotado),
                    sem_cotacao=_dinheiro(t.sem_cotacao),
                    variacao=(
                        round(float(t.custo_estimado / t.custo_origem_cotado - 1), 4)
                        if t.custo_origem_cotado > 0
                        else None
                    ),
                )
                for t in r.cotacao.por_provedor
            ],
            vao_junto=[
                UnidadeQueVaiJuntoOut(
                    unidade=v.unidade,
                    nome=v.nome,
                    aplicacao=v.aplicacao,
                    custo=_dinheiro(v.custo),
                    motivo=v.motivo,
                    incluida_por_voce=v.incluida_por_voce,
                    grupo=v.grupo,
                )
                for v in p.vao_junto
            ],
            servicos_que_vem_junto=[
                ServicoQueVemJuntoOut(servico=v.servico, custo=_dinheiro(v.custo), motivo=v.motivo)
                for v in p.servicos_que_vem_junto
            ],
            antes=[GrupoVizinhoOut.de(v) for v in p.antes],
            para_tras=[GrupoVizinhoOut.de(v) for v in p.para_tras],
            incluidos=[GrupoIncluidoOut.de(i) for i in p.incluidos],
            grupos_do_plano=grupos,
            unidades_do_plano=unidades,
            etapas_da_conta=etapas,
            ligacoes=[
                LigacaoOut(de=x.de, para=x.para, natureza=x.natureza.value, portas=list(x.portas))
                for x in p.ligacoes
            ],
            ordens=[OrdemOut(ordem=o.ordem, grupos=list(o.grupos)) for o in p.ordens],
            fronteira=[
                FronteiraOut(
                    sentido=f.sentido,
                    de=f.de,
                    para=f.para,
                    natureza=f.natureza.value,
                    portas=list(f.portas),
                    forte=f.forte,
                    texto=texto_da_fronteira(f),
                )
                for f in p.fronteira
            ],
            fora_do_plano=[CustoPorNome(nome=n, custo=_dinheiro(v)) for n, v in p.fora_do_plano],
            correspondencia=CorrespondenciaOut.de(ctx),
            premissas=_premissas(ctx),
        )


def _servicos(cotacao: CotacaoDoEscopo) -> list[ServicoNoEscopoOut]:
    return [
        ServicoNoEscopoOut(
            nome=s.servico,
            custo=_dinheiro(s.custo),
            destino={k: (_dinheiro(v) if v is not None else None) for k, v in s.destino.items()},
            metodos=list(s.metodos),
            sem_cotacao=dict(s.sem_cotacao),
        )
        for s in cotacao.por_servico
    ]
