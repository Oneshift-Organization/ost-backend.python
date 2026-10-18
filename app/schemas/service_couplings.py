"""Resposta de `GET …/service-couplings` (PLANO_LOCKIN_INVENTARIO, §4).

Uma linha por serviço da fatura, com o nível que o score usa e, à parte, as
sugestões que ele não usa. Os contadores da tela vêm de `resumo`, nunca da
contagem de linhas de `cross_cloud_equivalents`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.services.acoplamento_por_servico import (
    AcoplamentoDeServico,
    AcoplamentoPorServico,
    OpcaoNoDestino,
    PoolDoServico,
    Sugestao,
)

Nivel = Literal["low", "medium", "high"]


def _dinheiro(valor: Decimal) -> float:
    return float(valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class SugestaoOut(BaseModel):
    nivel: Nivel
    fonte: Literal["classe", "arquitetura", "inventario", "dependencias"]
    motivo: str

    @classmethod
    def de(cls, s: Sugestao) -> SugestaoOut:
        return cls.model_validate({"nivel": s.nivel, "fonte": s.fonte, "motivo": s.motivo})


class PoolOut(BaseModel):
    familia: str
    regiao: str | None = Field(description="null quando o billing não informa a região")
    custo: float
    arquitetura: Literal["x86", "arm"] | None
    burstable: bool | None
    coberto_por_compromisso: bool
    destinos_sem_mesma_arquitetura: list[str]
    sugestoes: list[SugestaoOut]

    @classmethod
    def de(cls, p: PoolDoServico) -> PoolOut:
        return cls.model_validate(
            {
                "familia": p.familia,
                "regiao": p.regiao,
                "custo": _dinheiro(p.custo),
                "arquitetura": p.arquitetura,
                "burstable": p.burstable,
                "coberto_por_compromisso": p.coberto_por_compromisso,
                "destinos_sem_mesma_arquitetura": list(p.destinos_sem_mesma_arquitetura),
                "sugestoes": [SugestaoOut.de(s) for s in p.sugestoes],
            }
        )


class VizinhoOut(BaseModel):
    servico: str
    custo: float | None = None
    natureza: str = Field(
        description="mesma_unidade | dados | encaminhamento | servico | declarada | "
        "administrativo | mesmo_grupo"
    )


class ExclusividadeOut(BaseModel):
    """Pelos atributos dos recursos do serviço no inventário (motor do banco, tipo nativo)."""

    nivel: Nivel
    motivo: str
    fracao: float = Field(description="fração do custo no inventário com recurso nesse nível")
    recursos: list[str]


class DependenciasOut(BaseModel):
    """Do inventário: o que precisa migrar junto ou antes (PLANO_LOCKIN_INVENTARIO B)."""

    nivel: Nivel
    motivo: str
    grupos: list[str]
    ondas: list[int] = Field(description="vazio quando o grupo é independente (sem ordem)")
    migra_junto_com: list[VizinhoOut]
    depende_de: list[VizinhoOut] = Field(description="dependência de ordem: o outro vai antes")
    dependentes: int
    banco_compartilhado: bool
    grupo_grande: bool
    custo_do_grupo: float
    cobertura: float | None = Field(
        description="fração do custo do serviço com recurso encontrado no inventário"
    )
    regioes: list[str]
    exclusividade: ExclusividadeOut | None = None


class OpcaoOut(BaseModel):
    servico: str
    motor: Literal["mysql", "postgresql", "oracle", "sqlserver"] | None = Field(
        description="motor do banco desta opção; null fora do banco"
    )
    custo_estimado: float | None = Field(description="preço por mês no destino; null sem cotação")
    cotacao: Literal["motor", "servico", "sem_cotacao"] = Field(
        description="motor: o banco cotado no motor da opção; servico: o preço do serviço no "
        "destino, o mesmo para todas as opções; sem_cotacao: o catálogo não tem a tarifa"
    )
    no_total: bool = Field(description="o preço desta opção é o que entrou no total do destino")
    motivo: str | None = Field(default=None, description="por que a opção não tem cotação")

    @classmethod
    def de(cls, o: OpcaoNoDestino) -> OpcaoOut:
        return cls.model_validate(
            {
                "servico": o.servico,
                "motor": o.motor,
                "custo_estimado": _dinheiro(o.custo_estimado)
                if o.custo_estimado is not None
                else None,
                "cotacao": o.cotacao,
                "no_total": o.no_total,
                "motivo": o.motivo,
            }
        )


class ServicoOut(BaseModel):
    id: str
    nome: str
    categoria: str
    service_type: str | None
    tipo_inferido: bool
    custo: float
    fracao: float | None = Field(description="custo ÷ gasto total do período")
    nivel: Nivel | None = Field(
        description="lado da origem, o mesmo de onde o score parte; null = a confirmar"
    )
    fonte: Literal["catalogo", "informado"] | None
    nivel_sugerido: Nivel | None = Field(
        description="maior sugestão, só quando subiria ou preencheria o nível"
    )
    sugestoes: list[SugestaoOut]
    por_destino: dict[str, Nivel | None] = Field(
        description="o que o eixo técnico do score usa em cada cenário"
    )
    pools: list[PoolOut]
    alertas: list[str]
    dependencias: DependenciasOut | None = Field(
        default=None,
        description="null sem inventário ou sem recurso deste serviço encontrado nele",
    )
    entra_no_score: bool = Field(
        description="false para gasto sem categoria identificada: listado, mas fora do score"
    )
    equivalente_por_destino: dict[str, str | None] = Field(
        description="serviço equivalente em cada cenário; null sem mapeamento único"
    )
    refatoracao_por_destino: dict[str, bool | None] = Field(
        description="exige refatoração no destino (pior entre origem e destino); "
        "null quando um dos lados não diz"
    )
    classe_migracao: str | None = Field(description="classe 7R de partida no catálogo")
    esforco: str | None = Field(description="esforço de migração no catálogo")
    opcoes_por_destino: dict[str, list[OpcaoOut]] = Field(
        default_factory=dict,
        description="cada equivalente de cada destino com o preço dele; no banco, pelo motor",
    )

    @classmethod
    def de(cls, s: AcoplamentoDeServico) -> ServicoOut:
        return cls.model_validate(
            {
                "id": s.id,
                "nome": s.nome,
                "categoria": s.categoria,
                "service_type": s.service_type,
                "tipo_inferido": s.tipo_inferido,
                "custo": _dinheiro(s.custo),
                "fracao": float(s.fracao) if s.fracao is not None else None,
                "nivel": s.nivel,
                "fonte": s.fonte,
                "nivel_sugerido": s.nivel_sugerido,
                "sugestoes": [SugestaoOut.de(x) for x in s.sugestoes],
                "por_destino": dict(s.por_destino),
                "pools": [PoolOut.de(p) for p in s.pools],
                "alertas": list(s.alertas),
                "dependencias": dict(s.dependencias) if s.dependencias else None,
                "entra_no_score": s.entra_no_score,
                "equivalente_por_destino": dict(s.equivalente_por_destino),
                "refatoracao_por_destino": dict(s.refatoracao_por_destino),
                "classe_migracao": s.classe_migracao,
                "esforco": s.esforco,
                "opcoes_por_destino": {
                    destino: [OpcaoOut.de(o) for o in opcoes]
                    for destino, opcoes in s.opcoes_por_destino.items()
                },
            }
        )


class ResumoOut(BaseModel):
    servicos: int
    low: int
    medium: int
    high: int
    a_confirmar: int
    a_confirmar_com_sugestao: int
    migram_em_grupo: int
    fora_do_score: int


class ReconciliacaoOut(BaseModel):
    soma_linhas: float
    fora_do_escopo: float = Field(
        description="imposto, crédito, desconto, compromisso, suporte e custo zero"
    )
    custo_atual: float | None
    diferenca: float | None
    status: Literal["fecha", "diferenca_de_escopo", "sem_custo_atual"]


class ServiceCouplingsResponse(BaseModel):
    versao: str
    moeda: str
    provedor_origem: str | None
    custo_atual: float | None
    resumo: ResumoOut
    servicos: list[ServicoOut]
    pools_sem_servico: list[PoolOut]
    reconciliacao: ReconciliacaoOut
    com_inventario: bool
    eixo_de_dependencia: bool = Field(
        description="a análise ligou recurso do inventário a serviço da fatura"
    )

    @classmethod
    def de(cls, r: AcoplamentoPorServico) -> ServiceCouplingsResponse:
        rec = r.reconciliacao
        return cls.model_validate(
            {
                "versao": r.versao,
                "moeda": r.moeda,
                "provedor_origem": r.provedor_origem,
                "custo_atual": _dinheiro(r.custo_atual) if r.custo_atual is not None else None,
                "resumo": ResumoOut.model_validate(r.resumo, from_attributes=True),
                "servicos": [ServicoOut.de(s) for s in r.servicos],
                "pools_sem_servico": [PoolOut.de(p) for p in r.pools_sem_servico],
                "reconciliacao": {
                    "soma_linhas": _dinheiro(rec.soma_linhas),
                    "fora_do_escopo": _dinheiro(rec.fora_do_escopo),
                    "custo_atual": (
                        _dinheiro(rec.custo_atual) if rec.custo_atual is not None else None
                    ),
                    "diferenca": _dinheiro(rec.diferenca) if rec.diferenca is not None else None,
                    "status": rec.status,
                },
                "com_inventario": r.com_inventario,
                "eixo_de_dependencia": r.eixo_de_dependencia,
            }
        )
