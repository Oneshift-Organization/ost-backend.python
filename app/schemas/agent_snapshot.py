"""Contrato HTTP do snapshot do agente (MCP.PLAN/09-CONTRATO-SNAPSHOT.md, v2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PrepararSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    report_id: UUID = Field(description="O `report_id` de `GET /v1/billing-files`.")
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
