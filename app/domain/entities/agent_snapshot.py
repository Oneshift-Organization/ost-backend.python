"""Snapshot do agente e envio de análise (PLANO_SNAPSHOT_AGENTE).

O snapshot é o payload determinístico congelado no momento em que o agente o
leu. O envio é a análise do agente corrigida contra **esse** payload, e nunca
contra "a última análise" do relatório.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


def agora() -> datetime:
    """UTC com fuso: as colunas destas tabelas são `timestamptz`."""
    return datetime.now(UTC)


def marcador_do_inventario(fingerprint: str | None, declaracoes_em: datetime | None) -> str:
    """O que muda quando o conteúdo ou as declarações do inventário mudam."""
    return f"{fingerprint or ''}|{declaracoes_em.isoformat() if declaracoes_em else ''}"


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    user_id: UUID
    report_id: UUID
    inventory_id: UUID | None
    inventory_marker: str | None
    fingerprint: str
    payload_fingerprint: str
    versions: dict[str, Any]
    payload: dict[str, Any]
    expires_at: datetime
    pools_sem_equivalente: list[str] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=agora)

    def vencido(self, instante: datetime) -> bool:
        return self.expires_at <= instante


@dataclass(frozen=True, slots=True)
class AgentSubmission:
    snapshot_id: UUID
    user_id: UUID
    report_id: UUID
    request_hash: str
    revision: int
    agent_raw: dict[str, Any]
    analysis: dict[str, Any]
    campos_sobrescritos: int
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=agora)
