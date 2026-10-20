"""Contratos de `/v1/inventories` (I5 do PLANO_INVENTARIO_API).

Metadados do envio em inglês, como no billing e no Terraform (`inventory_id`,
`status`, `provider`). O conteúdo da topologia e as declarações usam o
vocabulário do domínio, em português (`ondas`, `grupos`, `criticidade`), o
mesmo do payload da análise.

Nenhum campo aceita credencial (RNF3).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.services.topologia.modelo import Nivel

_MAX_ENTRADAS = 5000
_MAX_REF = 1024


class InventoryUploadResponse(BaseModel):
    inventory_id: UUID
    status: str
    source_files: list[str]
    created_at: datetime


class InventorySummary(BaseModel):
    inventory_id: UUID
    status: str
    provider: str | None = None
    source_files: list[str] = Field(default_factory=list)
    resource_count: int = 0
    captured_at: datetime | None = None
    created_at: datetime


class InventoryDetail(InventorySummary):
    detected_formats: dict[str, str] = Field(default_factory=dict)
    captured_at_estimated: bool = False
    relation_count: int = 0
    rule_count: int = 0
    counts_by_kind: dict[str, int] = Field(default_factory=dict)
    # O que foi descartado ou não modelado, com o motivo na chave.
    counts: dict[str, int] = Field(default_factory=dict)
    fingerprint: str | None = None
    declarations: dict[str, Any] = Field(default_factory=dict)
    declarations_updated_at: datetime | None = None
    error: str | None = None


class ResourceItem(BaseModel):
    key: str
    kind: str
    native_type: str
    name: str | None = None
    account: str | None = None
    region: str | None = None
    zone: str | None = None
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    tags: dict[str, str] = Field(default_factory=dict)


class ResourcesPage(BaseModel):
    items: list[ResourceItem]
    total: int
    limit: int
    offset: int


# ── declarações ─────────────────────────────────────────────────────────


class CriticidadeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recursos: dict[str, Nivel] = Field(default_factory=dict)
    aplicacoes: dict[str, Nivel] = Field(default_factory=dict)

    @field_validator("recursos", "aplicacoes")
    @classmethod
    def _limite(cls, v: dict[str, Nivel]) -> dict[str, Nivel]:
        if len(v) > _MAX_ENTRADAS:
            raise ValueError(f"no máximo {_MAX_ENTRADAS} entradas")
        if any(len(k) > _MAX_REF or not k.strip() for k in v):
            raise ValueError("chave vazia ou longa demais")
        return v


class DependenciaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    de: str = Field(min_length=1, max_length=_MAX_REF)
    para: str = Field(min_length=1, max_length=_MAX_REF)
    acoplamento: Literal["forte", "fraco"] = "fraco"
    motivo: str | None = Field(default=None, max_length=300)


class DeclaracoesIn(BaseModel):
    """`null` detecta a chave de tag; `""` desliga o agrupamento. Referência a
    unidade: a chave de um recurso ou `aplicacao:<valor da tag>`."""

    model_config = ConfigDict(extra="forbid")

    chave_aplicacao: str | None = Field(default=None, max_length=128)
    chave_ambiente: str | None = Field(default=None, max_length=128)
    criticidade: CriticidadeIn = Field(default_factory=CriticidadeIn)
    dependencias: list[DependenciaIn] = Field(default_factory=list, max_length=_MAX_ENTRADAS)
    ferramentas: list[str] = Field(default_factory=list, max_length=_MAX_ENTRADAS)


# ── topologia ───────────────────────────────────────────────────────────


class _Aberto(BaseModel):
    model_config = ConfigDict(extra="allow")


class TopologyResponse(_Aberto):
    """A análise da RI5 a RI12. As listas saem cortadas em `limites.maximo_por_lista`,
    e `limites.cortadas` diz o que foi cortado e o total de cada uma."""

    inventory_id: UUID
    provider: str | None
    captured_at: datetime | None
    fingerprint: str
    resumo: dict[str, Any]
    premissas: list[dict[str, Any]]
    ondas: list[dict[str, Any]]
    independentes: list[str]
    ferramentas: list[str]
    grupos: list[dict[str, Any]]
    unidades: list[dict[str, Any]]
    dependencias: list[dict[str, Any]]
    dependencias_entre_ondas: list[dict[str, Any]]
    riscos: list[dict[str, Any]]
    ociosos: list[dict[str, Any]]
    limites: dict[str, Any]


class CorrelationResponse(_Aberto):
    """RI13. Dinheiro na moeda do billing, sem conversão."""

    inventory_id: UUID
    report_id: UUID
    moeda: str
    custo_da_base: float
    custo_com_recurso: float
    custo_encontrado: float
    fracao_encontrada: float | None
    # Ligado ao recurso pelo tipo de uso, não pelo ID (fatura sem ID, máquina trocada)
    custo_estimado: float = 0.0
    recursos_do_billing: int
    recursos_encontrados: int
    ambiguos: int
    lacunas_por_escopo: list[dict[str, Any]]
    lacunas_por_servico: list[dict[str, Any]]
    sem_cobranca: dict[str, int]
    ociosos: list[dict[str, Any]]
    alinhamento: dict[str, Any]
    riscos: list[dict[str, Any]]
