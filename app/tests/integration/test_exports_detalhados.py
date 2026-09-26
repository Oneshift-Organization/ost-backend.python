"""Exports detalhados atravessando o pipeline real (PLANO_CUR_BIGQUERY, E5 em diante).

Os mocks saem dos bills reais versionados, então os números esperados são
conhecidos: US$ 92.103,48 sem imposto no CUR e R$ 27.825,01 no BigQuery.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest
import pytest_asyncio

from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.use_cases.analysis.leitura_completa import listar_todos

from .conftest import TEST_USER, _cleanup, _context, _ingest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_CUR_SEM_IMPOSTO = Decimal("92103.4834133795")
_BQ_TOTAL = Decimal("27825.01")


async def _enriquecidas(report_id):
    sm = get_sessionmaker()
    async with sm() as s:
        return await listar_todos(
            PgBillingEnrichedRepository(s).list_by_report, report_id, TEST_USER
        )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def cur(_db_ready, mocks_billing):
    rid = await _ingest(mocks_billing.cur)
    try:
        yield rid, await _enriquecidas(rid)
    finally:
        await _cleanup(rid)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def bq(_db_ready, mocks_billing):
    rid = await _ingest(mocks_billing.bigquery)
    try:
        yield rid, await _enriquecidas(rid)
    finally:
        await _cleanup(rid)


def _orfao(linhas) -> Decimal:
    return sum((x.cost for x in linhas if x.component_type == "unclassified"), Decimal("0"))


# ── CUR ─────────────────────────────────────────────────────────────────────


async def test_cur_enriquecido_e_o_bill_sem_imposto(cur) -> None:
    _, linhas = cur
    total = sum((x.cost for x in linhas), Decimal("0"))
    # NUMERIC(18,6) arredonda cada linha; 1.8 mil linhas cabem em 1 centavo.
    assert abs(total - _CUR_SEM_IMPOSTO) < Decimal("0.01")


async def test_cur_instancia_classificada_pelo_tipo(cur) -> None:
    _, linhas = cur
    instancias = [
        x
        for x in linhas
        if "BoxUsage" in (x.usage_type or "") and (x.attributes or {}).get("charge_kind") == "usage"
    ]
    assert instancias
    assert {x.component_type for x in instancias} == {"core"}
    # O defeito do descritor: nada de "AmazonEC2" como descritor de compute.
    assert not any(x.usage_type == "AmazonEC2" for x in linhas)


async def test_cur_compromisso_vai_pela_natureza(cur) -> None:
    _, linhas = cur
    compromisso = [x for x in linhas if x.component_type == "commitment"]
    assert compromisso
    assert {x.match_layer for x in compromisso} == {"LN"}
    # Amortizado: só o que ficou OCIOSO — 5% dos dois SPs e as reservas de
    # banco que excederam o uso. US$ 10 mil de taxa viram US$ 1,9 mil.
    ocioso = sum((x.cost for x in compromisso), Decimal("0"))
    assert abs(ocioso - Decimal("1872.47")) < Decimal("0.05")


async def test_cur_recurso_e_projeto_chegam_no_enriquecido(cur) -> None:
    _, linhas = cur
    instancias = [x for x in linhas if x.component_type == "core"]
    assert all(x.resource_id for x in instancias if "BoxUsage" in (x.usage_type or ""))
    assert {x.project_id for x in instancias} <= {
        "222222222222",
        "333333333333",
        "444444444444",
    }
    assert all("vcpus" in (x.attributes or {}) for x in instancias if x.resource_id)


async def test_cur_cobertura_de_classificacao(cur) -> None:
    _, linhas = cur
    total = sum((x.cost for x in linhas), Decimal("0"))
    # 94% hoje. O órfão restante (CloudWatch, Translate, KMS, WAF, requisições)
    # não tem balde no modelo de capacidade — é não classificado por desenho.
    assert _orfao(linhas) / total <= Decimal("0.07")


# ── BigQuery ────────────────────────────────────────────────────────────────


async def test_bq_enriquecido_bate_com_o_cost_table(bq) -> None:
    _, linhas = bq
    total = sum((x.cost for x in linhas), Decimal("0"))
    assert abs(total - _BQ_TOTAL) < Decimal("0.01")


async def test_bq_core_em_horas_e_ram_em_gib_hora(bq) -> None:
    _, linhas = bq
    unidades = defaultdict(set)
    for x in linhas:
        unidades[x.component_type].add(x.usage_unit)
    assert unidades["core"] == {"hours"}
    assert unidades["ram"] == {"gibibyte hour"}


async def test_bq_cobertura_e_projeto(bq) -> None:
    _, linhas = bq
    total = sum((x.cost for x in linhas), Decimal("0"))
    assert _orfao(linhas) / total <= Decimal("0.02")
    projetos = {x.project_id for x in linhas if x.component_type == "core"}
    assert projetos and all(p.startswith("proj-id-") for p in projetos)


async def test_contexto_roda_com_os_dois_mocks(cur, bq) -> None:
    """O contexto de migração inteiro aceita os exports detalhados."""
    for rid, _ in (cur, bq):
        ctx = await _context(rid)
        assert ctx.total_spend > 0
        assert ctx.premissas["reconciliacao"]["conservacao_custo"]["bate"] is True
