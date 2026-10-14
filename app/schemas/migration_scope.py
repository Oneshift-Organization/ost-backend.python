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

    `liga_custo=false`: sem custo por aplicação nem plano por serviço.
    """

    status: Literal["ok", "parcial", "sem_id_de_recurso"]
    liga_custo: bool
    fracao_encontrada: float | None
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


class MigrationPlanRequest(BaseModel):
    aplicacoes: list[str] = Field(default_factory=list)
    servicos: list[str] = Field(default_factory=list)
    ambiente: str | None = Field(default=None, description="null = todos os ambientes")
    incluir_grupos: list[str] = Field(
        default_factory=list, description="ids de grupos de `antes`/`para_tras` incluídos à mão"
    )


class EscolhaOut(BaseModel):
    aplicacoes: list[str]
    servicos: list[str]
    ambiente: str | None
    incluir_grupos: list[str]


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
    ligacoes: list[LigacaoOut]
    ordens: list[OrdemOut]
    fora_do_plano: list[CustoPorNome]
    correspondencia: CorrespondenciaOut
    limite_das_dependencias: str = LIMITE_DAS_DEPENDENCIAS
    premissas: list[PremissaOut]

    @classmethod
    def de(cls, r: PlanoCotado) -> MigrationPlanResponse:
        p, ctx = r.plano, r.contexto
        return cls(
            moeda=ctx.moeda,
            provedor_origem=ctx.provedor_origem,
            provedor_recomendado=ctx.provedor_recomendado,
            escolha=EscolhaOut(
                aplicacoes=sorted(p.escolha.aplicacoes),
                servicos=sorted(p.escolha.servicos),
                ambiente=p.escolha.ambiente,
                incluir_grupos=sorted(p.escolha.incluir_grupos),
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
            ligacoes=[
                LigacaoOut(de=x.de, para=x.para, natureza=x.natureza.value, portas=list(x.portas))
                for x in p.ligacoes
            ],
            ordens=[OrdemOut(ordem=o.ordem, grupos=list(o.grupos)) for o in p.ordens],
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
