"""Um inventário enviado pelo cliente, com o ciclo de processamento (I5).

O conteúdo normalizado (recursos, relações, regras) vive em tabelas próprias; aqui
fica o que descreve o envio: estado, formato detectado, quando foi capturado,
o que ficou de fora e as declarações do cliente.

Ciclo igual ao do billing: QUEUED → PROCESSING → DONE | FAILED. Depois de DONE
o conteúdo não muda — é o que permite congelar uma análise pelo fingerprint.
As declarações mudam (PUT), e por isso ficam fora do fingerprint do conteúdo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.exceptions import ValidationError
from app.domain.entities.billing_report import JobStatus

_MAX_ERRO = 2000


def _agora() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class InventarioEnviado:
    user_id: UUID
    arquivos: list[str] = field(default_factory=list)
    id: UUID = field(default_factory=uuid4)
    status: JobStatus = JobStatus.QUEUED
    provedor: str | None = None
    # arquivo (ou membro de zip) → formato detectado
    formatos: dict[str, str] = field(default_factory=dict)
    capturado_em: datetime | None = None
    # O Resource Graph não diz quando foi capturado: usa-se a data do envio.
    capturado_em_estimado: bool = False
    recursos: int = 0
    relacoes: int = 0
    regras: int = 0
    contagens: dict[str, int] = field(default_factory=dict)
    fingerprint: str | None = None
    declaracoes: dict[str, Any] = field(default_factory=dict)
    declaracoes_atualizadas_em: datetime | None = None
    erro: str | None = None
    created_at: datetime = field(default_factory=_agora)
    updated_at: datetime = field(default_factory=_agora)

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UUID):
            raise ValidationError("user_id precisa ser UUID")

    def marcar_processando(self) -> None:
        if self.status is not JobStatus.QUEUED:
            raise ValidationError(f"não dá para processar a partir de {self.status}")
        self.status = JobStatus.PROCESSING
        self.updated_at = _agora()

    def marcar_concluido(
        self,
        *,
        provedor: str,
        formatos: dict[str, str],
        capturado_em: datetime | None,
        recursos: int,
        relacoes: int,
        regras: int,
        contagens: dict[str, int],
        fingerprint: str,
    ) -> None:
        if self.status is not JobStatus.PROCESSING:
            raise ValidationError(f"não dá para concluir a partir de {self.status}")
        self.status = JobStatus.DONE
        self.provedor = provedor
        self.formatos = formatos
        self.capturado_em = capturado_em or self.created_at
        self.capturado_em_estimado = capturado_em is None
        self.recursos = recursos
        self.relacoes = relacoes
        self.regras = regras
        self.contagens = contagens
        self.fingerprint = fingerprint
        self.updated_at = _agora()

    def marcar_falha(self, erro: str) -> None:
        self.status = JobStatus.FAILED
        self.erro = erro[:_MAX_ERRO]
        self.updated_at = _agora()

    def declarar(self, declaracoes: dict[str, Any]) -> None:
        if self.status is not JobStatus.DONE:
            raise ValidationError("declarações só depois do processamento concluído")
        self.declaracoes = declaracoes
        self.declaracoes_atualizadas_em = _agora()
        self.updated_at = self.declaracoes_atualizadas_em
