"""Preparação do snapshot do agente (C1), sem banco."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.domain.entities.agent_snapshot import AgentSnapshot, marcador_do_inventario
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.services.fingerprint_payload import hash_canonico
from app.use_cases.agent import versoes
from app.use_cases.agent.preparar_snapshot import PrepararSnapshotUseCase
from app.use_cases.analysis.payload_do_agente import PayloadDoAgente

USER = UUID("00000000-0000-0000-0000-0000000000c1")
REPORT = UUID("00000000-0000-0000-0000-0000000000c2")
INICIO = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)

_PAYLOAD = {
    "gasto_total": 100.0,
    "decision_support": {"policy_version": "score-7rs-v3", "selected_provider": "oci"},
}
_COM_TOPOLOGIA = {
    **_PAYLOAD,
    "topologia": {"versao": "oneshift-topologia-v1", "resumo": {"ondas": []}, "detalhe": {"x": 1}},
}


class _Montador:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def execute(
        self, user_id: UUID, report_id: UUID, inventario_id: UUID | None = None
    ) -> PayloadDoAgente:
        return PayloadDoAgente(payload=copy.deepcopy(self.payload), pools_sem_equivalente=["p1"])


class _Snapshots:
    """Em memória, com a mesma regra do índice de conteúdo."""

    def __init__(self) -> None:
        self.linhas: dict[tuple[Any, ...], AgentSnapshot] = {}

    async def gravar_ou_reaproveitar(self, s: AgentSnapshot) -> tuple[AgentSnapshot, bool]:
        chave = (s.user_id, s.report_id, s.inventory_id, s.fingerprint)
        if chave in self.linhas:
            atual = replace(
                self.linhas[chave], expires_at=s.expires_at, inventory_marker=s.inventory_marker
            )
            self.linhas[chave] = atual
            return atual, True
        self.linhas[chave] = s
        return s, False


class _Inventarios:
    def __init__(self, declaracoes_em: datetime) -> None:
        self.declaracoes_em = declaracoes_em

    async def obter(self, inventario_id: UUID, user_id: UUID) -> InventarioEnviado:
        return InventarioEnviado(
            user_id=user_id,
            id=inventario_id,
            fingerprint="fp-inv",
            declaracoes_atualizadas_em=self.declaracoes_em,
        )


class _Relogio:
    def __init__(self) -> None:
        self.agora = INICIO

    def __call__(self) -> datetime:
        return self.agora


def _uc(
    payload: dict[str, Any], snapshots: _Snapshots | None = None, relogio: _Relogio | None = None
) -> tuple[PrepararSnapshotUseCase, _Snapshots, _Relogio, _Inventarios]:
    snapshots = snapshots or _Snapshots()
    relogio = relogio or _Relogio()
    inventarios = _Inventarios(INICIO - timedelta(days=1))
    uc = PrepararSnapshotUseCase(
        montador=cast(Any, _Montador(payload)),
        snapshots=cast(Any, snapshots),
        inventarios=cast(Any, inventarios),
        validade=timedelta(hours=24),
        relogio=relogio,
    )
    return uc, snapshots, relogio, inventarios


pytestmark = pytest.mark.asyncio


async def test_cria_com_versoes_hashes_e_validade() -> None:
    uc, _, _, _ = _uc(_PAYLOAD)
    p = await uc.execute(USER, REPORT)
    s = p.snapshot
    assert not p.reaproveitado
    assert s.versions == versoes.versoes_do_payload(_PAYLOAD)
    assert s.fingerprint == hash_canonico({"versions": s.versions, "payload": _PAYLOAD})
    assert s.payload_fingerprint == hash_canonico(p.payload)
    assert s.expires_at == INICIO + timedelta(hours=24)
    assert s.pools_sem_equivalente == ["p1"]
    assert s.inventory_id is None and s.inventory_marker is None


async def test_agente_recebe_a_topologia_compacta_e_o_backend_guarda_o_detalhe() -> None:
    uc, _, _, _ = _uc(_COM_TOPOLOGIA)
    p = await uc.execute(USER, REPORT, uuid4())
    assert "detalhe" not in p.payload["topologia"]
    assert p.snapshot.payload["topologia"]["detalhe"] == {"x": 1}
    assert p.snapshot.payload_fingerprint == hash_canonico(p.payload)
    assert p.snapshot.payload_fingerprint != hash_canonico(p.snapshot.payload)
    assert p.snapshot.versions["topology"] == "oneshift-topologia-v1"


async def test_mesmo_conteudo_reaproveita_com_validade_renovada() -> None:
    uc, snapshots, relogio, _ = _uc(_PAYLOAD)
    primeiro = await uc.execute(USER, REPORT)
    relogio.agora = INICIO + timedelta(hours=30)  # o primeiro já venceu
    segundo = await uc.execute(USER, REPORT)
    assert segundo.reaproveitado
    assert segundo.snapshot.id == primeiro.snapshot.id
    assert segundo.snapshot.expires_at == relogio.agora + timedelta(hours=24)
    assert len(snapshots.linhas) == 1


async def test_conteudo_diferente_gera_outro_snapshot() -> None:
    snapshots = _Snapshots()
    a = await _uc(_PAYLOAD, snapshots)[0].execute(USER, REPORT)
    b = await _uc({**_PAYLOAD, "gasto_total": 101.0}, snapshots)[0].execute(USER, REPORT)
    assert not b.reaproveitado and a.snapshot.id != b.snapshot.id


async def test_versao_de_autoridade_diferente_gera_outro_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = _Snapshots()
    a = await _uc(_PAYLOAD, snapshots)[0].execute(USER, REPORT)
    monkeypatch.setattr(versoes, "AUTHORITY_VERSION", "agent-authority-v2")
    b = await _uc(_PAYLOAD, snapshots)[0].execute(USER, REPORT)
    assert b.snapshot.payload == a.snapshot.payload
    assert b.snapshot.fingerprint != a.snapshot.fingerprint
    assert not b.reaproveitado


async def test_com_inventario_grava_o_marcador() -> None:
    uc, _, _, inventarios = _uc(_COM_TOPOLOGIA)
    p = await uc.execute(USER, REPORT, uuid4())
    assert p.snapshot.inventory_marker == marcador_do_inventario(
        "fp-inv", inventarios.declaracoes_em
    )
