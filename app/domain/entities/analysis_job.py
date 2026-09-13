from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from uuid import UUID


class AnalysisStatus(StrEnum):
    """Estados do job de análise.

    Tipado (antes era string livre) porque um consumidor — agente, front ou
    MCP — precisa saber quais valores existem e quais são **terminais**. Sem
    enum, o OpenAPI declarava apenas `string` e "estado final" era conhecimento
    tribal.
    """

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in (AnalysisStatus.COMPLETED, AnalysisStatus.FAILED)


# Estados em que o job ainda ocupa o report — base da idempotência.
ACTIVE_STATUSES = (AnalysisStatus.PENDING, AnalysisStatus.PROCESSING)


class DispStatus(IntEnum):
    """Visibilidade da análise para o usuário — nada a ver com `AnalysisStatus`.

    `AnalysisStatus` descreve a execução (rodou? falhou?). Este descreve se o
    usuário quer continuar vendo o resultado. Uma análise `COMPLETED` pode estar
    `DEPRECIADA`, e uma `FAILED` pode estar `ATIVA`.

    Existe em vez de um DELETE porque apagar leva junto o `llm_input_payload`,
    que é a fonte do Terraform e de qualquer reanálise: quem exclui por engano
    perde o pacote que já baixou, sem nenhuma forma de voltar.
    """

    DEPRECIADA = 0
    ATIVA = 1


def agora_utc() -> datetime:
    """UTC sem tzinfo — as colunas do schema são `TIMESTAMP WITHOUT TIME ZONE`.

    Devolver um datetime *aware* aqui faz o asyncpg recusar o INSERT, e misturar
    aware com naive na mesma tabela quebraria as comparações do reaper.
    """
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class AnalysisJob:
    job_id: UUID
    user_id: UUID
    report_id: UUID
    external_id: UUID | None = None
    status: str = AnalysisStatus.PENDING
    llm_output: str | None = None
    error: str | None = None
    ai_structured_output: dict | None = None
    llm_input_payload: dict | None = None
    agent_raw_output: dict | None = None
    sugestoes: list | None = None
    disp_status: int = DispStatus.ATIVA
    disp_status_updated_at: datetime | None = None
    created_at: datetime = field(default_factory=agora_utc)
    # Quando a execução começou de fato e quando o estado mudou pela última vez.
    # Sem isso não há como distinguir "acabou de entrar na fila" de "travado há
    # duas horas" — os dois apareciam como PENDING.
    started_at: datetime | None = None
    updated_at: datetime | None = None

    def mark_processing(self) -> None:
        self.status = AnalysisStatus.PROCESSING
        self.started_at = agora_utc()
        self.updated_at = self.started_at

    def mark_completed(
        self,
        output: str,
        structured: dict | None = None,
        llm_input: dict | None = None,
    ) -> None:
        self.status = AnalysisStatus.COMPLETED
        self.llm_output = output
        self.ai_structured_output = structured
        self.llm_input_payload = llm_input
        self.updated_at = agora_utc()

    def mark_failed(self, error: str) -> None:
        self.status = AnalysisStatus.FAILED
        self.error = error
        self.updated_at = agora_utc()

    def definir_disponibilidade(self, valor: int) -> None:
        """Marca a data da mudança — é ela que ancora a carência do expurgo.

        Sem carimbar, "apaga depois de um mês" não teria de quando contar, e o
        primeiro expurgo levaria tudo que já estava depreciado.
        """
        self.disp_status = int(valor)
        self.disp_status_updated_at = agora_utc()
        self.updated_at = self.disp_status_updated_at

    @property
    def esta_depreciada(self) -> bool:
        return self.disp_status == DispStatus.DEPRECIADA

    @property
    def is_active(self) -> bool:
        """Ainda ocupa o report (não terminou)."""
        return self.status in ACTIVE_STATUSES
