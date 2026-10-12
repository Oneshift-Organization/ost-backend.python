"""Preparação do snapshot que o agente lê (PLANO_SNAPSHOT_AGENTE, item C).

Monta o payload determinístico, calcula as versões e os dois hashes e grava, ou
reaproveita o snapshot com o mesmo conteúdo. O agente recebe a representação
compacta (sem `topologia.detalhe`); o backend guarda o payload completo.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from app.domain.entities.agent_snapshot import AgentSnapshot, agora, marcador_do_inventario
from app.domain.repositories.i_agent_snapshot_repo import IAgentSnapshotRepository
from app.domain.repositories.i_inventory_repo import IInventoryRepository
from app.domain.services.fingerprint_payload import hash_canonico
from app.use_cases.agent.versoes import versoes_do_payload
from app.use_cases.analysis.payload_do_agente import MontarPayloadDoAgenteUseCase
from app.use_cases.analysis.topologia_no_payload import compactar_topologia


@dataclass(frozen=True, slots=True)
class SnapshotPreparado:
    snapshot: AgentSnapshot
    # A representação entregue ao agente; `snapshot.payload_fingerprint` é dela.
    payload: dict[str, Any]
    reaproveitado: bool


class PrepararSnapshotUseCase:
    def __init__(
        self,
        *,
        montador: MontarPayloadDoAgenteUseCase,
        snapshots: IAgentSnapshotRepository,
        inventarios: IInventoryRepository,
        validade: timedelta,
        relogio: Callable[[], datetime] = agora,
    ) -> None:
        self._montador = montador
        self._snapshots = snapshots
        self._inventarios = inventarios
        self._validade = validade
        self._relogio = relogio

    async def execute(
        self, user_id: UUID, report_id: UUID, inventario_id: UUID | None = None
    ) -> SnapshotPreparado:
        """Levanta `PayloadDoAgenteError` nas mesmas condições do `/request`."""
        montado = await self._montador.execute(user_id, report_id, inventario_id)
        versoes = versoes_do_payload(montado.payload)
        entregue = compactar_topologia(montado.payload)
        snapshot = AgentSnapshot(
            user_id=user_id,
            report_id=report_id,
            inventory_id=inventario_id,
            inventory_marker=await self._marcador(user_id, inventario_id),
            # As versões entram na identidade: o mesmo payload interpretado por
            # outras regras é outro snapshot.
            fingerprint=hash_canonico({"versions": versoes, "payload": montado.payload}),
            payload_fingerprint=hash_canonico(entregue),
            versions=versoes,
            payload=montado.payload,
            pools_sem_equivalente=montado.pools_sem_equivalente,
            expires_at=self._relogio() + self._validade,
        )
        gravado, reaproveitado = await self._snapshots.gravar_ou_reaproveitar(snapshot)
        return SnapshotPreparado(snapshot=gravado, payload=entregue, reaproveitado=reaproveitado)

    async def _marcador(self, user_id: UUID, inventario_id: UUID | None) -> str | None:
        if inventario_id is None:
            return None
        inventario = await self._inventarios.obter(inventario_id, user_id)
        if inventario is None:  # validado antes; some só se apagado no meio
            return None
        return marcador_do_inventario(inventario.fingerprint, inventario.declaracoes_atualizadas_em)
