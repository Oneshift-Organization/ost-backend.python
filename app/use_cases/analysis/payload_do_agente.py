"""Montagem do payload determinístico que o agente lê.

Saiu de dentro de `GET /v1/analyses/{report_id}/request` para que a rota e a
preparação de snapshot (`POST /v1/agent/snapshots`, PLANO_SNAPSHOT_AGENTE) usem
o mesmo código. Dois caminhos que montassem o payload cada um a seu modo
divergiriam, e o agente leria num lugar um número diferente do que é corrigido
no outro.

As checagens saem como `PayloadDoAgenteError`, com o código do envelope da API e uma
categoria, não com status HTTP: quem traduz para HTTP é a rota.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.analysis import MigrationContext
from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.use_cases.analysis.run_analysis_pipeline import (
    build_pipeline_llm_input_payload,
    conversor_da_analise,
)
from app.use_cases.analysis.topologia_no_payload import (
    MontarTopologiaUseCase,
    validar_inventario_da_analise,
)
from app.use_cases.inventory.consultas import InventarioNaoProcessadoError
from app.use_cases.inventory.correlacao import ProvedorDiferenteError

_EM_ANDAMENTO = frozenset({JobStatus.QUEUED, JobStatus.PROCESSING})


class Categoria(StrEnum):
    """O tipo do erro, que a rota traduz para o status HTTP."""

    NAO_ENCONTRADO = "nao_encontrado"
    CONFLITO = "conflito"
    INVALIDO = "invalido"
    EXPIRADO = "expirado"


class PayloadDoAgenteError(Exception):
    """Erro com o código do envelope (`codigo`, `mensagem`, `retryable`)."""

    def __init__(
        self,
        codigo: str,
        mensagem: str,
        *,
        categoria: Categoria,
        retryable: bool = False,
        **extra: Any,
    ) -> None:
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.categoria = categoria
        self.retryable = retryable
        self.extra = extra


@dataclass(frozen=True, slots=True)
class PayloadDoAgente:
    """O payload completo (com `topologia.detalhe`, quando há inventário)."""

    payload: dict[str, Any]
    pools_sem_equivalente: list[str] = field(default_factory=list)


ConstrutorDeContexto = Callable[[UUID, UUID], Awaitable[MigrationContext]]


class MontarPayloadDoAgenteUseCase:
    def __init__(
        self,
        *,
        relatorios: IBillingReportRepository,
        inventarios: IInventoryRepository,
        contexto: ConstrutorDeContexto,
        topologia: MontarTopologiaUseCase,
        inventario_habilitado: bool,
    ) -> None:
        self._relatorios = relatorios
        self._inventarios = inventarios
        self._contexto = contexto
        self._topologia = topologia
        self._inventario_habilitado = inventario_habilitado

    async def validar_inventario(self, user_id: UUID, report_id: UUID, inventario_id: UUID) -> None:
        """RI14: inventário do usuário, processado e da mesma nuvem do billing.

        Também usada pelo `POST /v1/analyses/start`, antes de criar o job.
        """
        if not self._inventario_habilitado:
            raise PayloadDoAgenteError(
                "INVENTARIO_DESLIGADO",
                "o inventário não está habilitado neste ambiente",
                categoria=Categoria.NAO_ENCONTRADO,
            )
        report = await self._relatorios.get_by_id(report_id, user_id)
        if report is None:
            raise _report_nao_encontrado(report_id)
        try:
            await validar_inventario_da_analise(
                self._inventarios,
                inventario_id=inventario_id,
                user_id=user_id,
                provedor_do_report=report.provider.value if report.provider else None,
            )
        except InventarioNaoProcessadoError as e:
            raise PayloadDoAgenteError(
                "INVENTARIO_NAO_PROCESSADO", str(e), categoria=Categoria.CONFLITO, retryable=True
            ) from e
        except ProvedorDiferenteError as e:
            raise PayloadDoAgenteError(
                "INVENTARIO_DE_OUTRA_NUVEM", str(e), categoria=Categoria.INVALIDO
            ) from e
        except NotFoundError as e:
            raise PayloadDoAgenteError(
                "INVENTARIO_NAO_ENCONTRADO", str(e), categoria=Categoria.NAO_ENCONTRADO
            ) from e

    async def exigir_pronto(self, user_id: UUID, report_id: UUID) -> BillingReport:
        """O relatório existe, é do usuário e terminou a ingestão."""
        report = await self._relatorios.get_by_id(report_id, user_id)
        if report is None:
            raise _report_nao_encontrado(report_id)
        if report.status in _EM_ANDAMENTO:
            raise PayloadDoAgenteError(
                "REPORT_NAO_PRONTO",
                f"ingestão ainda em andamento (status={report.status})",
                categoria=Categoria.CONFLITO,
                retryable=True,
                status_report=report.status.value,
            )
        if report.status is not JobStatus.DONE:
            raise PayloadDoAgenteError(
                "REPORT_FALHOU",
                f"ingestão não concluiu (status={report.status})",
                categoria=Categoria.INVALIDO,
                status_report=report.status.value,
            )
        return report

    async def montar(
        self, user_id: UUID, report_id: UUID, inventario_id: UUID | None
    ) -> PayloadDoAgente:
        """Payload completo, sem as checagens: chame `validar_inventario` e
        `exigir_pronto` antes."""
        try:
            ctx = await self._contexto(user_id, report_id)
        except NotFoundError as e:
            raise PayloadDoAgenteError(
                "CONTEXTO_INDISPONIVEL", str(e), categoria=Categoria.NAO_ENCONTRADO
            ) from e
        except ValueError as e:
            raise PayloadDoAgenteError(
                "CONTEXTO_INDISPONIVEL", str(e), categoria=Categoria.INVALIDO
            ) from e

        payload, sem_equivalente = build_pipeline_llm_input_payload(ctx)
        if inventario_id is not None:
            payload["topologia"] = await self._topologia.execute(
                user_id=user_id,
                inventario_id=inventario_id,
                report_id=report_id,
                ctx=ctx,
                converter=conversor_da_analise(ctx),
            )
        return PayloadDoAgente(payload=payload, pools_sem_equivalente=list(sem_equivalente))

    async def execute(
        self, user_id: UUID, report_id: UUID, inventario_id: UUID | None = None
    ) -> PayloadDoAgente:
        """As checagens na ordem do `/request`, depois a montagem."""
        if inventario_id is not None:
            await self.validar_inventario(user_id, report_id, inventario_id)
        await self.exigir_pronto(user_id, report_id)
        return await self.montar(user_id, report_id, inventario_id)


def _report_nao_encontrado(report_id: UUID) -> PayloadDoAgenteError:
    return PayloadDoAgenteError(
        "REPORT_NAO_ENCONTRADO",
        f"report {report_id} não existe para este usuário",
        categoria=Categoria.NAO_ENCONTRADO,
    )
