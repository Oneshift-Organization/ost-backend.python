"""Repositórios do snapshot do agente contra Postgres real (B2).

O que importa aqui são as garantias que o contrato promete ao time do MCP e que
só o banco dá: reaproveitamento sob concorrência, `NULLS NOT DISTINCT` no
inventário, lock de linha, unicidade de conteúdo e de revisão, expurgo que
preserva quem tem envio e a cascata da exclusão do relatório.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.entities.agent_snapshot import AgentSnapshot, AgentSubmission, agora
from app.domain.entities.billing_report import BillingReport
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_agent_snapshot_repo import (
    PgAgentSnapshotRepository,
    PgAgentSubmissionRepository,
)
from app.infrastructure.repositories.pg_billing_report_repo import PgBillingReportRepository

USER = UUID("00000000-0000-0000-0000-0000000000b2")
OUTRO = UUID("00000000-0000-0000-0000-0000000000b3")

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _report() -> UUID:
    sm = get_sessionmaker()
    async with sm() as s:
        report = BillingReport(user_id=USER, source_filename="repo.csv")
        await PgBillingReportRepository(s).save(report)
        await s.commit()
    return report.id


async def _apagar(report_id: UUID) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(text("delete from billing_reports where id = :r"), {"r": report_id})
        await s.commit()


def _snap(
    report_id: UUID, *, fingerprint: str = "f" * 64, horas: int = 24, **extra: Any
) -> AgentSnapshot:
    return AgentSnapshot(
        user_id=extra.pop("user_id", USER),
        report_id=report_id,
        inventory_id=extra.pop("inventory_id", None),
        inventory_marker=None,
        fingerprint=fingerprint,
        payload_fingerprint="p" * 64,
        versions={"authority": "agent-authority-v1"},
        payload={"gasto_total": 10.5},
        expires_at=agora() + timedelta(hours=horas),
        **extra,
    )


async def _gravar(snapshot: AgentSnapshot) -> tuple[AgentSnapshot, bool]:
    sm = get_sessionmaker()
    async with sm() as s:
        resultado = await PgAgentSnapshotRepository(s).gravar_ou_reaproveitar(snapshot)
        await s.commit()
    return resultado


def _envio(snapshot: AgentSnapshot, request_hash: str, revision: int) -> AgentSubmission:
    return AgentSubmission(
        snapshot_id=snapshot.id,
        user_id=snapshot.user_id,
        report_id=snapshot.report_id,
        request_hash=request_hash,
        revision=revision,
        agent_raw={"recommendation": "x"},
        analysis={"recommendation": "y"},
        campos_sobrescritos=1,
    )


async def test_mesmo_conteudo_reaproveita_e_renova_a_validade(_db_ready: None) -> None:
    rid = await _report()
    try:
        primeiro, reaproveitado = await _gravar(_snap(rid, horas=1))
        assert not reaproveitado
        segundo, reaproveitado = await _gravar(_snap(rid, horas=24))
        assert reaproveitado
        assert segundo.id == primeiro.id
        assert segundo.expires_at > primeiro.expires_at
        assert segundo.created_at == primeiro.created_at
    finally:
        await _apagar(rid)


async def test_inventario_nulo_conta_como_igual(_db_ready: None) -> None:
    # Sem NULLS NOT DISTINCT, cada preparação sem inventário criaria um snapshot.
    rid = await _report()
    try:
        a, _ = await _gravar(_snap(rid, inventory_id=None))
        b, reaproveitado = await _gravar(_snap(rid, inventory_id=None))
        assert reaproveitado and a.id == b.id
        c, reaproveitado = await _gravar(_snap(rid, inventory_id=uuid4()))
        assert not reaproveitado and c.id != a.id
    finally:
        await _apagar(rid)


async def test_conteudo_diferente_gera_outro(_db_ready: None) -> None:
    rid = await _report()
    try:
        a, _ = await _gravar(_snap(rid, fingerprint="a" * 64))
        b, reaproveitado = await _gravar(_snap(rid, fingerprint="b" * 64))
        assert not reaproveitado and a.id != b.id
    finally:
        await _apagar(rid)


async def test_vencido_com_o_mesmo_conteudo_volta_a_valer(_db_ready: None) -> None:
    rid = await _report()
    try:
        vencido, _ = await _gravar(_snap(rid, horas=-2))
        assert vencido.vencido(agora())
        renovado, reaproveitado = await _gravar(_snap(rid, horas=24))
        assert reaproveitado and renovado.id == vencido.id
        assert not renovado.vencido(agora())
    finally:
        await _apagar(rid)


async def test_preparacoes_simultaneas_dao_um_snapshot(_db_ready: None) -> None:
    rid = await _report()
    try:
        resultados = await asyncio.gather(*(_gravar(_snap(rid)) for _ in range(6)))
        assert len({s.id for s, _ in resultados}) == 1
        assert sum(1 for _, reaproveitado in resultados if not reaproveitado) == 1
        sm = get_sessionmaker()
        async with sm() as s:
            n = (
                await s.execute(
                    text("select count(*) from agent_snapshots where report_id = :r"), {"r": rid}
                )
            ).scalar()
        assert n == 1
    finally:
        await _apagar(rid)


async def test_travar_isola_por_usuario(_db_ready: None) -> None:
    rid = await _report()
    try:
        snap, _ = await _gravar(_snap(rid))
        sm = get_sessionmaker()
        async with sm() as s:
            repo = PgAgentSnapshotRepository(s)
            assert (await repo.travar(snap.id, USER)) is not None
            assert (await repo.travar(snap.id, OUTRO)) is None
            assert (await repo.travar(uuid4(), USER)) is None
    finally:
        await _apagar(rid)


async def test_envio_unico_por_conteudo_e_por_revisao(_db_ready: None) -> None:
    rid = await _report()
    try:
        snap, _ = await _gravar(_snap(rid))
        sm = get_sessionmaker()
        async with sm() as s:
            repo = PgAgentSubmissionRepository(s)
            assert await repo.proxima_revisao(snap.id) == 1
            await repo.gravar(_envio(snap, "h1" * 32, 1))
            await s.commit()
            assert await repo.proxima_revisao(snap.id) == 2
            assert (await repo.por_conteudo(snap.id, "h1" * 32)) is not None
            assert (await repo.por_conteudo(snap.id, "h2" * 32)) is None
        for duplicado in (_envio(snap, "h1" * 32, 2), _envio(snap, "h3" * 32, 1)):
            async with sm() as s:
                with pytest.raises(IntegrityError):
                    await PgAgentSubmissionRepository(s).gravar(duplicado)
    finally:
        await _apagar(rid)


async def test_listar_do_mais_recente_com_isolamento(_db_ready: None) -> None:
    rid = await _report()
    try:
        snap, _ = await _gravar(_snap(rid))
        sm = get_sessionmaker()
        async with sm() as s:
            repo = PgAgentSubmissionRepository(s)
            for revisao in (1, 2, 3):
                await repo.gravar(_envio(snap, f"{revisao}" * 64, revisao))
            await s.commit()
            por_relatorio = await repo.listar(USER, report_id=rid)
            assert [e.revision for e in por_relatorio] == [3, 2, 1]
            assert [e.revision for e in await repo.listar(USER, snapshot_id=snap.id, limite=2)] == [
                3,
                2,
            ]
            assert await repo.listar(OUTRO, report_id=rid) == []
            assert (await repo.obter(por_relatorio[0].id, OUTRO)) is None
            assert (await repo.obter(por_relatorio[0].id, USER)) is not None
    finally:
        await _apagar(rid)


async def test_expurgo_preserva_quem_tem_envio(_db_ready: None) -> None:
    rid = await _report()
    try:
        sem_envio, _ = await _gravar(_snap(rid, fingerprint="1" * 64, horas=-24 * 10))
        com_envio, _ = await _gravar(_snap(rid, fingerprint="2" * 64, horas=-24 * 10))
        valido, _ = await _gravar(_snap(rid, fingerprint="3" * 64))
        sm = get_sessionmaker()
        async with sm() as s:
            await PgAgentSubmissionRepository(s).gravar(_envio(com_envio, "e" * 64, 1))
            await s.commit()
        async with sm() as s:
            await PgAgentSnapshotRepository(s).expurgar(agora() - timedelta(days=7))
            await s.commit()
        async with sm() as s:
            ids = {
                r[0]
                for r in await s.execute(
                    text("select id from agent_snapshots where report_id = :r"), {"r": rid}
                )
            }
        assert ids == {com_envio.id, valido.id}
        assert sem_envio.id not in ids
    finally:
        await _apagar(rid)


async def test_apagar_o_relatorio_leva_snapshots_e_envios(_db_ready: None) -> None:
    rid = await _report()
    snap, _ = await _gravar(_snap(rid))
    sm = get_sessionmaker()
    async with sm() as s:
        await PgAgentSubmissionRepository(s).gravar(_envio(snap, "c" * 64, 1))
        await s.commit()
    await _apagar(rid)
    async with sm() as s:
        for tabela in ("agent_snapshots", "agent_submissions"):
            n = (
                await s.execute(
                    text(f"select count(*) from {tabela} where report_id = :r"), {"r": rid}
                )
            ).scalar()
            assert n == 0, tabela
