"""Contratos da superfície de agente (`/v1/agent/*`).

Separado de `schemas/analysis.py` de propósito: o legado tem contrato próprio e
não deve ser arrastado por mudanças aqui.

O que o agente **não** envia é tão importante quanto o que envia — não há campo
de custo, economia, percentual ou score na entrada. Esses o backend já tem, e
aceitar do agente seria convidá-lo a fazer conta.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TipoSugestao(StrEnum):
    CONTESTACAO_PRECO = "contestacao_preco"
    CONTESTACAO_EQUIVALENCIA = "contestacao_equivalencia"
    OPORTUNIDADE = "oportunidade"
    RISCO = "risco"
    DADO_FALTANTE = "dado_faltante"


class Severidade(StrEnum):
    ALTA = "alta"
    MEDIA = "media"
    BAIXA = "baixa"


class SugestaoIn(BaseModel):
    """Uma sugestão do agente.

    `efeito_backend` NÃO existe aqui de propósito — é o backend que o preenche
    na saída. Se o agente pudesse enviá-lo, poderia afirmar na tela que "a
    confiança foi rebaixada" quando não foi.
    """

    model_config = ConfigDict(extra="ignore")

    tipo: TipoSugestao
    severidade: Severidade
    titulo: str = Field(max_length=120, description="Uma linha; vira título na tela.")
    detalhe: str = Field(max_length=600)
    referencia: str | None = Field(
        default=None,
        description="O que a sugestão aponta: 'GCP/n2-standard-8/us-central1', 'pool:E2'.",
    )


class PaybackIn(BaseModel):
    """Estimativa de payback com premissa obrigatória.

    O backend não modela custo de migração, então não calcula payback. Em vez de
    proibir — devolver `null` é honesto e inútil — exigimos a premissa. Um número
    com a premissa exposta é contestável; o mesmo número num campo
    indistinguível dos calculados é mentira por omissão.
    """

    model_config = ConfigDict(extra="ignore")

    meses: int = Field(gt=0)
    premissa_custo_migracao_usd: float = Field(
        ge=0, description="OBRIGATÓRIO: o custo de migração assumido."
    )
    base: str = Field(max_length=300, description="Como chegou nessa premissa.")


class AgentAnalysisIn(BaseModel):
    """O que o agente devolve.

    Repare no que falta: nenhum campo de custo, economia, percentual ou score.
    O backend já tem todos — aceitá-los seria convidar o agente a fazer conta.
    """

    model_config = ConfigDict(extra="ignore")

    recommendation: str = Field(max_length=2000)
    recommendation_rationale: str = Field(default="", max_length=4000)
    strategic_notes: list[str] = Field(default_factory=list)
    action_plan: list[str] = Field(default_factory=list)
    sugestoes: list[SugestaoIn] = Field(default_factory=list)
    payback: PaybackIn | None = None

    # Qualitativo por provedor, casado por nome (não por índice).
    provider_comparison: list[dict[str, Any]] = Field(default_factory=list)


class SugestaoOut(SugestaoIn):
    """Sugestão como sai para a tela, com o efeito que o backend aplicou."""

    efeito_backend: str = Field(
        description="`confianca_rebaixada` ou `informativo`. Preenchido pelo backend."
    )


class AgentAnalysisOut(BaseModel):
    """A análise corrigida.

    É esta que o agente deve usar ao falar com o usuário — não o que ele enviou.
    """

    model_config = ConfigDict(extra="allow")

    job_id: UUID
    report_id: UUID
    analysis_confidence: str
    sugestoes: list[SugestaoOut] = Field(default_factory=list)
    campos_sobrescritos: int = Field(
        description="Quantos números do agente o backend corrigiu."
    )
    created_at: datetime
