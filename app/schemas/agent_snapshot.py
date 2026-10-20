"""Contrato HTTP do snapshot do agente (MCP.PLAN/09-CONTRATO-SNAPSHOT.md, v2)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent import AgentAnalysisIn


class PrepararSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    report_id: UUID = Field(description="O `report_id` de `GET /v1/billing-files`.")
    contract: Literal["agent-snapshot-v1", "agent-snapshot-v2"] = "agent-snapshot-v1"
    inventory_id: UUID | None = Field(
        default=None, description="Com inventário, o payload ganha `topologia`."
    )


class SnapshotOut(BaseModel):
    snapshot_id: UUID
    report_id: UUID
    inventory_id: UUID | None
    fingerprint: str = Field(
        description="Identidade do snapshot interno: SHA-256 de {versions, payload completo}."
    )
    payload_fingerprint: str = Field(
        description="SHA-256 do JSON canônico do `payload` desta resposta."
    )
    versions: dict[str, str | None]
    created_at: datetime
    expires_at: datetime
    reused: bool


class SnapshotPreparadoOut(BaseModel):
    snapshot: SnapshotOut
    payload: dict[str, Any] = Field(
        description="O payload determinístico, com a topologia compacta."
    )
    pools_sem_equivalente: list[str]


class DecisionSupportSnapshotOut(BaseModel):
    snapshot_id: UUID
    decision_support: dict[str, Any]
    sha256: str


class PosicaoDoParecer(StrEnum):
    CONCORDA = "concorda"
    DIVERGE = "diverge"


class ParecerIn(BaseModel):
    """O agente concorda ou diverge da decisão do backend, e por quê.

    Julgamento: volta intacto em `analysis.parecer_agente` e nunca altera a
    decisão nem os números. Existe porque o backend reescreve `recommendation`,
    e sem este campo a discordância do agente se perdia.
    """

    model_config = ConfigDict(extra="ignore")

    posicao: PosicaoDoParecer = Field(
        description="Em relação a `decision_support.selected_provider` (inclusive `null`)."
    )
    motivo: str = Field(min_length=1, max_length=2000)
    riscos: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list, max_length=20)


class EnvioDoAgenteIn(AgentAnalysisIn):
    """O corpo do envio por snapshot: o de `/v1/agent/analyses` mais o parecer.

    Separado de `AgentAnalysisIn` para a rota antiga não mudar de contrato.
    """

    parecer: ParecerIn | None = None


class EnvioOut(BaseModel):
    submission_id: UUID
    snapshot_id: UUID
    report_id: UUID
    revision: int
    fingerprint: str = Field(description="Do snapshot usado.")
    request_hash: str = Field(description="SHA-256 do JSON canônico do corpo validado.")
    versions: dict[str, str | None]
    campos_sobrescritos: int = Field(description="Quantos valores do agente o backend corrigiu.")
    created_at: datetime


class EnvioRegistradoOut(BaseModel):
    submission: EnvioOut
    analysis: dict[str, Any] = Field(
        description="A análise corrigida. É ela que o agente deve narrar ao usuário."
    )


class ResumoDaAnaliseOut(BaseModel):
    recommended_provider: str | None
    recommendation: str | None
    analysis_confidence: str | None
    sugestoes: int = Field(description="Quantidade de sugestões (do agente e do backend).")


class EnvioResumidoOut(BaseModel):
    """Item da lista de envios. A análise completa vem por
    `GET /v1/agent/submissions/{submission_id}`."""

    submission: EnvioOut
    analysis_summary: ResumoDaAnaliseOut
