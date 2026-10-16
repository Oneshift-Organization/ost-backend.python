"""Envio da análise do agente contra um snapshot (PLANO_SNAPSHOT_AGENTE, item D).

A ordem das verificações é o contrato (MCP.PLAN/09, §3.3):

1. o snapshot existe e é do usuário (lock de linha até o fim da transação);
2. o mesmo corpo já foi enviado para ele: devolve o gravado, **mesmo que o
   snapshot tenha vencido ou ficado desatualizado depois** — o resultado já foi
   comunicado, e recusar a repetição faria o cliente perder um envio que existe;
3. vencido: recusa;
4. versão, inventário ou relatório mudaram: recusa;
5. aplica a autoridade sobre o payload **do snapshot** e grava a revisão nova.

A idempotência é pelo conteúdo: o resultado é função pura do snapshot e do
corpo, então o mesmo corpo é o mesmo envio.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.entities.agent_snapshot import (
    AgentSnapshot,
    AgentSubmission,
    agora,
    marcador_do_inventario,
)
from app.domain.entities.billing_report import JobStatus
from app.domain.repositories.i_acoes_de_preco_repo import IAcoesDePrecoRepository
from app.domain.repositories.i_agent_snapshot_repo import (
    IAgentSnapshotRepository,
    IAgentSubmissionRepository,
)
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.fingerprint_payload import hash_canonico
from app.use_cases.agent.versoes import divergencias
from app.use_cases.analysis.agent_result import receber_analise_do_agente
from app.use_cases.analysis.payload_do_agente import Categoria, PayloadDoAgenteError


class EnvioRecusadoError(PayloadDoAgenteError):
    """Recusa do envio, com o código do envelope da API."""


@dataclass(frozen=True, slots=True)
class EnvioRegistrado:
    envio: AgentSubmission
    snapshot: AgentSnapshot
    repetido: bool


class SubmeterAnaliseUseCase:
    def __init__(
        self,
        *,
        snapshots: IAgentSnapshotRepository,
        envios: IAgentSubmissionRepository,
        relatorios: IBillingReportRepository,
        inventarios: IInventoryRepository,
        relogio: Callable[[], datetime] = agora,
        acoes: IAcoesDePrecoRepository | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._envios = envios
        self._relatorios = relatorios
        self._inventarios = inventarios
        self._relogio = relogio
        self._acoes = acoes

    async def execute(
        self, user_id: UUID, snapshot_id: UUID, entrada: dict[str, Any]
    ) -> EnvioRegistrado:
        """``entrada`` é o corpo já validado, em JSON (`model_dump(mode="json")`)."""
        snapshot = await self._snapshots.travar(snapshot_id, user_id)
        if snapshot is None:
            raise EnvioRecusadoError(
                "SNAPSHOT_NAO_ENCONTRADO",
                f"snapshot {snapshot_id} não existe para este usuário",
                categoria=Categoria.NAO_ENCONTRADO,
            )

        request_hash = hash_canonico(entrada)
        gravado = await self._envios.por_conteudo(snapshot.id, request_hash)
        if gravado is not None:
            return EnvioRegistrado(envio=gravado, snapshot=snapshot, repetido=True)

        if snapshot.vencido(self._relogio()):
            raise EnvioRecusadoError(
                "SNAPSHOT_EXPIRADO",
                f"o snapshot venceu em {snapshot.expires_at.isoformat()}; "
                "prepare outro e refaça a análise",
                categoria=Categoria.EXPIRADO,
            )
        await self._exigir_atual(snapshot)

        parecer = entrada.get("parecer")
        resposta_agente = {k: v for k, v in entrada.items() if k != "parecer"}
        resultado = receber_analise_do_agente(
            payload_backend=snapshot.payload,
            resposta_agente=resposta_agente,
            sugestoes=list(entrada.get("sugestoes") or []),
        )
        # O parecer fica fora da autoridade e volta como veio: é julgamento, e
        # nenhuma etapa da correção pode reescrevê-lo.
        analise = {**resultado.resposta, "parecer_agente": parecer}
        envio = AgentSubmission(
            snapshot_id=snapshot.id,
            user_id=user_id,
            report_id=snapshot.report_id,
            request_hash=request_hash,
            revision=await self._envios.proxima_revisao(snapshot.id),
            agent_raw=entrada,
            analysis=analise,
            campos_sobrescritos=len(resultado.autoridade.substituicoes),
        )
        await self._envios.gravar(envio)
        # V4.1: o preço contestado que não se verificou vira pedido de sync, e o preço
        # que o agente sugeriu fica no ciclo de vida, sem reviver nada
        if self._acoes is not None and (
            resultado.pedidos_de_sync or resultado.observacoes_do_agente
        ):
            await self._acoes.registrar_contestacoes(
                resultado.pedidos_de_sync, resultado.observacoes_do_agente, snapshot.id
            )
        return EnvioRegistrado(envio=envio, snapshot=snapshot, repetido=False)

    async def _exigir_atual(self, snapshot: AgentSnapshot) -> None:
        diferentes = divergencias(snapshot.versions)
        if diferentes:
            raise _desatualizado(
                "versao",
                f"o backend mudou desde a preparação ({', '.join(diferentes)})",
                versoes_divergentes=diferentes,
            )
        report = await self._relatorios.get_by_id(snapshot.report_id, snapshot.user_id)
        if report is None or report.status is not JobStatus.DONE:
            raise _desatualizado("relatorio", "o relatório deixou de estar pronto")
        if snapshot.inventory_id is not None:
            inventario = await self._inventarios.obter(snapshot.inventory_id, snapshot.user_id)
            atual = (
                marcador_do_inventario(
                    inventario.fingerprint, inventario.declaracoes_atualizadas_em
                )
                if inventario is not None
                else None
            )
            if atual != snapshot.inventory_marker:
                raise _desatualizado("inventario", "o inventário do snapshot mudou ou foi apagado")


def _desatualizado(motivo: str, mensagem: str, **extra: Any) -> EnvioRecusadoError:
    return EnvioRecusadoError(
        "SNAPSHOT_DESATUALIZADO",
        f"{mensagem}; prepare outro snapshot e refaça a análise",
        categoria=Categoria.CONFLITO,
        motivo=motivo,
        **extra,
    )
