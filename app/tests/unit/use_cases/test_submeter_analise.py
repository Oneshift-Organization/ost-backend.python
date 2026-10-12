"""Ordem das verificações do envio (D2, contrato §3.3), sem banco."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from app.domain.entities.agent_snapshot import (
    AgentSnapshot,
    AgentSubmission,
    marcador_do_inventario,
)
from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.entities.inventario_enviado import InventarioEnviado
from app.domain.services.fingerprint_payload import hash_canonico
from app.use_cases.agent import submeter_analise as modulo
from app.use_cases.agent.submeter_analise import EnvioRecusadoError, SubmeterAnaliseUseCase
from app.use_cases.agent.versoes import AUTHORITY_VERSION, politica_de_decisao_atual

USER = UUID("00000000-0000-0000-0000-0000000000d1")
AGORA = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
DECLARADO_EM = AGORA - timedelta(days=1)


def _snapshot(**extra: Any) -> AgentSnapshot:
    base: dict[str, Any] = {
        "user_id": USER,
        "report_id": uuid4(),
        "inventory_id": None,
        "inventory_marker": None,
        "fingerprint": "f" * 64,
        "payload_fingerprint": "p" * 64,
        "versions": {
            "contract": "agent-snapshot-v1",
            "decision_policy": politica_de_decisao_atual(),
            "topology": None,
            "authority": AUTHORITY_VERSION,
        },
        "payload": {"gasto_total": 1.0},
        "expires_at": AGORA + timedelta(hours=1),
    }
    base.update(extra)
    return AgentSnapshot(**base)


class _Snapshots:
    def __init__(self, snapshot: AgentSnapshot) -> None:
        self.snapshot = snapshot

    async def travar(self, snapshot_id: UUID, user_id: UUID) -> AgentSnapshot | None:
        s = self.snapshot
        return s if (s.id, s.user_id) == (snapshot_id, user_id) else None


class _Envios:
    def __init__(self) -> None:
        self.gravados: list[AgentSubmission] = []

    async def por_conteudo(self, snapshot_id: UUID, request_hash: str) -> AgentSubmission | None:
        return next(
            (
                e
                for e in self.gravados
                if (e.snapshot_id, e.request_hash) == (snapshot_id, request_hash)
            ),
            None,
        )

    async def proxima_revisao(self, snapshot_id: UUID) -> int:
        return 1 + sum(1 for e in self.gravados if e.snapshot_id == snapshot_id)

    async def gravar(self, envio: AgentSubmission) -> None:
        self.gravados.append(envio)


class _Relatorios:
    def __init__(self, status: JobStatus = JobStatus.DONE) -> None:
        self.status = status

    async def get_by_id(self, report_id: UUID, user_id: UUID) -> BillingReport:
        r = BillingReport(user_id=user_id, source_filename="x.csv", id=report_id)
        r.status = self.status
        return r


class _Inventarios:
    def __init__(self, existe: bool = True) -> None:
        self.existe = existe

    async def obter(self, inventario_id: UUID, user_id: UUID) -> InventarioEnviado | None:
        if not self.existe:
            return None
        return InventarioEnviado(
            user_id=user_id,
            id=inventario_id,
            fingerprint="fp",
            declaracoes_atualizadas_em=DECLARADO_EM,
        )


def _uc(
    snapshot: AgentSnapshot,
    envios: _Envios | None = None,
    relatorios: _Relatorios | None = None,
    inventarios: _Inventarios | None = None,
) -> tuple[SubmeterAnaliseUseCase, _Envios]:
    envios = envios or _Envios()
    uc = SubmeterAnaliseUseCase(
        snapshots=cast(Any, _Snapshots(snapshot)),
        envios=cast(Any, envios),
        relatorios=cast(Any, relatorios or _Relatorios()),
        inventarios=cast(Any, inventarios or _Inventarios()),
        relogio=lambda: AGORA,
    )
    return uc, envios


_ENTRADA = {"recommendation": "x", "sugestoes": [], "parecer": {"posicao": "concorda"}}


async def _codigo(uc: SubmeterAnaliseUseCase, snapshot_id: UUID) -> tuple[str, dict[str, Any]]:
    with pytest.raises(EnvioRecusadoError) as erro:
        await uc.execute(USER, snapshot_id, _ENTRADA)
    return erro.value.codigo, erro.value.extra


async def test_de_outro_usuario_ou_inexistente() -> None:
    alheio = _snapshot(user_id=uuid4())
    uc, _ = _uc(alheio)
    assert (await _codigo(uc, alheio.id))[0] == "SNAPSHOT_NAO_ENCONTRADO"
    assert (await _codigo(uc, uuid4()))[0] == "SNAPSHOT_NAO_ENCONTRADO"


async def test_grava_a_revisao_com_hash_do_corpo() -> None:
    s = _snapshot()
    uc, envios = _uc(s)
    r = await uc.execute(USER, s.id, _ENTRADA)
    assert not r.repetido
    assert r.envio.revision == 1
    assert r.envio.request_hash == hash_canonico(_ENTRADA)
    assert r.envio.agent_raw == _ENTRADA
    assert envios.gravados == [r.envio]


async def test_parecer_fica_fora_da_autoridade_e_volta_intacto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vistos: list[dict[str, Any]] = []
    original = getattr(modulo, "receber_analise_do_agente")  # noqa: B009

    def espiao(**kw: Any) -> Any:
        vistos.append(kw["resposta_agente"])
        return original(**kw)

    monkeypatch.setattr(modulo, "receber_analise_do_agente", espiao)
    s = _snapshot()
    r = await _uc(s)[0].execute(USER, s.id, _ENTRADA)
    assert "parecer" not in vistos[0]
    assert r.envio.analysis["parecer_agente"] == {"posicao": "concorda"}


async def test_repeticao_vem_antes_da_validade_e_da_versao() -> None:
    s = _snapshot()
    uc, envios = _uc(s)
    primeiro = await uc.execute(USER, s.id, _ENTRADA)
    # Agora o snapshot está vencido e de versão antiga: a repetição ainda vale.
    velho = replace(
        s, expires_at=AGORA - timedelta(hours=1), versions={**s.versions, "authority": "v0"}
    )
    uc2, _ = _uc(velho, envios=envios)
    repetido = await uc2.execute(USER, s.id, _ENTRADA)
    assert repetido.repetido and repetido.envio == primeiro.envio
    assert len(envios.gravados) == 1
    # Corpo novo no mesmo snapshot vencido: recusa.
    with pytest.raises(EnvioRecusadoError) as erro:
        await uc2.execute(USER, s.id, {**_ENTRADA, "recommendation": "y"})
    assert erro.value.codigo == "SNAPSHOT_EXPIRADO"


async def test_vencido_e_recusado() -> None:
    s = _snapshot(expires_at=AGORA)
    assert (await _codigo(_uc(s)[0], s.id))[0] == "SNAPSHOT_EXPIRADO"


@pytest.mark.parametrize(
    ("versoes", "divergentes"),
    [
        ({"authority": "agent-authority-v0"}, ["authority"]),
        ({"decision_policy": "score-7rs-v2"}, ["decision_policy"]),
        ({"topology": "oneshift-topologia-v0"}, ["topology"]),
    ],
)
async def test_versao_divergente(versoes: dict[str, str], divergentes: list[str]) -> None:
    base = _snapshot()
    s = replace(base, versions={**base.versions, **versoes})
    codigo, extra = await _codigo(_uc(s)[0], s.id)
    assert codigo == "SNAPSHOT_DESATUALIZADO"
    assert extra == {"motivo": "versao", "versoes_divergentes": divergentes}


async def test_relatorio_fora_de_done() -> None:
    s = _snapshot()
    codigo, extra = await _codigo(_uc(s, relatorios=_Relatorios(JobStatus.PROCESSING))[0], s.id)
    assert (codigo, extra["motivo"]) == ("SNAPSHOT_DESATUALIZADO", "relatorio")


async def test_inventario_igual_aceita_e_mudado_ou_apagado_recusa() -> None:
    igual = _snapshot(
        inventory_id=uuid4(), inventory_marker=marcador_do_inventario("fp", DECLARADO_EM)
    )
    assert not (await _uc(igual)[0].execute(USER, igual.id, _ENTRADA)).repetido

    mudado = replace(igual, inventory_marker=marcador_do_inventario("fp", AGORA))
    assert (await _codigo(_uc(mudado)[0], mudado.id))[1]["motivo"] == "inventario"

    apagado_uc, _ = _uc(igual, inventarios=_Inventarios(existe=False))
    assert (await _codigo(apagado_uc, igual.id))[1]["motivo"] == "inventario"
