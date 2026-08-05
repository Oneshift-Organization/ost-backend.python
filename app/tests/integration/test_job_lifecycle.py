"""Ciclo de vida do job de análise: estado visível + idempotência.

Os dois defeitos que estes testes travam atingem um agente diretamente:

- **Estado invisível.** `mark_processing()` só mutava o objeto ORM; o único
  commit vinha DEPOIS da chamada ao LLM. Quem fazia polling via PENDING por
  30-60s e depois COMPLETED, e um processo morto no meio deixava o job preso em
  PENDING para sempre — indistinguível de recém-criado.
- **Sem idempotência.** Retry do agente é comportamento normal, e cada chamada
  criava um job novo: uma segunda análise PAGA.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.deps import _DEV_USER
from app.api.v1.routes import analysis as _rotas_analysis
from app.domain.entities.analysis_job import AnalysisJob, AnalysisStatus
from app.domain.entities.billing_report import BillingReport
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_analysis_job_repo import PgAnalysisJobRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.main import create_app

HTTP_USER = UUID(_DEV_USER.sub)


@pytest.fixture(scope="module")
def _app():
    return create_app()


async def _report_pronto() -> UUID:
    """Report em estado `done` — o suficiente para /start aceitar."""
    sm = get_sessionmaker()
    async with sm() as s:
        rep = BillingReport(user_id=HTTP_USER, source_filename="lifecycle.csv")
        await PgBillingReportRepository(s).save(rep)
        await s.execute(
            text("update billing_reports set status='done', provider='aws' where id=:r"),
            {"r": rep.id},
        )
        await s.commit()
    return rep.id


async def _limpar(report_id: UUID) -> None:
    sm = get_sessionmaker()
    async with sm() as s:
        await s.execute(
            text("delete from analysis_jobs where report_id=:r"), {"r": report_id}
        )
        await s.execute(text("delete from billing_reports where id=:r"), {"r": report_id})
        await s.commit()


# ── estado visível ──────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_processing_e_commitado_em_transacao_propria(_db_ready) -> None:
    """PROCESSING precisa existir no banco ENQUANTO a análise roda."""
    rid = await _report_pronto()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()

        # Transição commitada por si só — sem depender do commit final.
        async with sm() as s:
            await PgAnalysisJobRepository(s).marcar_processando(job.job_id)

        # Outra sessão (como faria um GET de status concorrente) já enxerga.
        async with sm() as s:
            visto = await PgAnalysisJobRepository(s).get_by_id(job.job_id, HTTP_USER)
            assert visto is not None
            assert visto.status == AnalysisStatus.PROCESSING
            assert visto.started_at is not None
    finally:
        await _limpar(rid)


# ── idempotência ────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_start_duas_vezes_devolve_o_mesmo_job(
    _app, _db_ready, monkeypatch
) -> None:
    """Retry do agente NÃO pode gerar uma segunda análise paga.

    A pipeline é neutralizada de propósito: sob `ASGITransport` a BackgroundTask
    roda até o fim antes de o cliente receber a resposta, então o primeiro job já
    estaria terminal na segunda chamada — e criar um job novo aí é o
    comportamento CORRETO (reanálise depois de terminar). O que este teste trava
    é a janela em que a análise ainda está em andamento.
    """

    async def _nao_executa(*_a, **_k) -> None:
        return None

    monkeypatch.setattr(_rotas_analysis, "_run_analysis_pipeline", _nao_executa)

    rid = await _report_pronto()
    try:
        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r1 = await c.post(f"/v1/analyses/start/{rid}")
            r2 = await c.post(f"/v1/analyses/start/{rid}")
        assert r1.status_code in (200, 202), r1.text
        assert r2.status_code in (200, 202), r2.text
        assert r1.json()["job_id"] == r2.json()["job_id"], "criou um segundo job"

        sm = get_sessionmaker()
        async with sm() as s:
            n = (
                await s.execute(
                    text("select count(*) from analysis_jobs where report_id=:r"),
                    {"r": rid},
                )
            ).scalar()
        assert n == 1, f"esperava 1 job, achou {n}"
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_banco_impede_segundo_job_ativo(_db_ready) -> None:
    """O índice único parcial é a garantia final — vale mesmo se a rota falhar."""
    rid = await _report_pronto()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            await PgAnalysisJobRepository(s).create(
                AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            )
            await s.commit()
        with pytest.raises(IntegrityError):
            async with sm() as s:
                await PgAnalysisJobRepository(s).create(
                    AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
                )
                await s.commit()
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_job_terminal_libera_o_report(_db_ready) -> None:
    """Depois de COMPLETED/FAILED, uma nova análise pode ser disparada —
    senão o report ficaria bloqueado para sempre."""
    rid = await _report_pronto()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            repo = PgAnalysisJobRepository(s)
            primeiro = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await repo.create(primeiro)
            await s.commit()
            primeiro.mark_failed("erro qualquer")
            await repo.update(primeiro)
            await s.commit()
        async with sm() as s:
            await PgAnalysisJobRepository(s).create(
                AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            )
            await s.commit()  # não deve levantar
    finally:
        await _limpar(rid)


# ── reaper ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_reaper_libera_job_travado(_db_ready) -> None:
    """Sem o reaper, um job preso bloquearia o report para sempre (o índice
    único impediria criar outro). É o que torna a idempotência segura."""
    rid = await _report_pronto()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()
            # Envelhece o job para além do limite.
            antigo = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=3)
            await s.execute(
                text("update analysis_jobs set updated_at=:t where job_id=:j"),
                {"t": antigo, "j": job.job_id},
            )
            await s.commit()

        async with sm() as s:
            n = await PgAnalysisJobRepository(s).expirar_travados(timedelta(minutes=30))
        assert n >= 1

        async with sm() as s:
            visto = await PgAnalysisJobRepository(s).get_by_id(job.job_id, HTTP_USER)
            assert visto is not None
            assert visto.status == AnalysisStatus.FAILED
            assert "TIMEOUT" in (visto.error or "")

        # E o report volta a aceitar nova análise.
        async with sm() as s:
            await PgAnalysisJobRepository(s).create(
                AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            )
            await s.commit()
    finally:
        await _limpar(rid)


@pytest.mark.asyncio(loop_scope="session")
async def test_reaper_nao_toca_job_recente(_db_ready) -> None:
    """Um job que começou agora não pode ser morto pelo reaper."""
    rid = await _report_pronto()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()
        async with sm() as s:
            await PgAnalysisJobRepository(s).expirar_travados(timedelta(minutes=30))
        async with sm() as s:
            visto = await PgAnalysisJobRepository(s).get_by_id(job.job_id, HTTP_USER)
            assert visto is not None
            assert visto.status == AnalysisStatus.PENDING
    finally:
        await _limpar(rid)


# ── resiliência da listagem ─────────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="session")
async def test_registro_corrompido_nao_derruba_a_listagem(_app, _db_ready) -> None:
    """Era o 500 de produção: `recommendation` como objeto em vez de string
    fazia o Pydantic recusar, e o usuário perdia acesso a TODAS as análises."""
    rid = await _report_pronto()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            job = AnalysisJob(job_id=uuid4(), user_id=HTTP_USER, report_id=rid)
            await PgAnalysisJobRepository(s).create(job)
            await s.commit()
            # Formato que o LLM pode devolver e o schema não aceita.
            await s.execute(
                text(
                    "update analysis_jobs set status='COMPLETED', "
                    "ai_structured_output = cast(:p as jsonb) where job_id = :j"
                ),
                {
                    "p": '{"recommendation": {"texto": "migrar"}, '
                    '"viability_score": 7.5, "analysis_confidence": 0.9}',
                    "j": job.job_id,
                },
            )
            await s.commit()

        transport = ASGITransport(app=_app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/v1/analyses?limit=100")

        assert r.status_code == 200, f"a listagem caiu inteira: {r.text[:200]}"
        assert any(j["job_id"] == str(job.job_id) for j in r.json()), (
            "o job corrompido sumiu da lista em vez de degradar"
        )
    finally:
        await _limpar(rid)
