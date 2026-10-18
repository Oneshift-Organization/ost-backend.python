"""Tipo, portabilidade e risco na tabela de equivalentes (PLANO_EQUIVALENTES_E_TARIFAS §2).

Os três campos saíam "unknown" em todas as linhas: nenhum seed os preenchia. Agora vêm da
evidência que a linha já mostra (refatoração e lock-in), escritos na metadata do destino
na preparação do payload; a projeção da tabela não muda.
"""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.analysis import (
    BillingItemSummary,
    EquivalentService,
    MigrationContext,
    ServiceCategoryGroup,
)
from app.domain.services.qualidade_da_equivalencia import classificar, portabilidade, risco
from app.infrastructure.llm.orchestrator import _compact_decision_payload
from app.use_cases.analysis.equivalence_projection import project_equivalences
from app.use_cases.analysis.run_analysis_pipeline import (
    _com_qualidade_do_pool,
    build_pipeline_llm_input_payload,
)


@pytest.mark.parametrize(
    ("refatora", "lock_in", "esperado"),
    [
        (True, "low", "high"),
        (False, "high", "high"),
        (None, "high", "high"),
        (False, "medium", "medium"),
        (None, "medium", "medium"),
        (False, "low", "low"),
        # Lock-in baixo sem a refatoração confirmada: o limite conservador, nunca baixo
        (None, "low", "medium"),
        # Sem lock-in conhecido não há como avaliar
        (False, None, "unknown"),
        (None, None, "unknown"),
    ],
)
def test_risco_pela_pior_restricao_conhecida(refatora, lock_in, esperado):
    assert risco(refatora, lock_in) == esperado


def test_portabilidade_pelo_lock_in():
    assert [portabilidade(n) for n in ("low", "medium", "high", None)] == [
        "portable",
        "semi_portable",
        "non_portable",
        "unknown",
    ]
    assert classificar(False, "low", tipo="hardware_match") == {
        "equivalence_type": "hardware_match",
        "portability": "portable",
        "migration_risk": "low",
    }


def _contexto(
    type_source: str, *, meta_destino: dict[str, Any], tipo_destino: str
) -> MigrationContext:
    destinos = [
        EquivalentService(
            provider=p,
            service_name=f"{p}-banco",
            category="database",
            service_type=tipo_destino,
            metadata=meta_destino,
        )
        for p in ("oci", "azure")
    ]
    servico = BillingItemSummary(
        service_name="AmazonRDS",
        provider="aws",
        total_cost=Decimal(100),
        service_type="managed_relational_database",
        equivalents=destinos,
        type_source=type_source,
        migration_metadata={"requires_refactoring": False, "vendor_lock_in_level": "low"},
    )
    return MigrationContext(
        source_provider="aws",
        billing_period="2026-03",
        total_spend=Decimal(100),
        categories=[ServiceCategoryGroup("database", Decimal(100), [servico])],
    )


def test_linha_do_servico_curado_ganha_os_tres_campos():
    ctx = _contexto(
        "curado",
        meta_destino={"requires_refactoring": True, "vendor_lock_in_level": "medium"},
        tipo_destino="managed_relational_database",
    )
    payload, _ = build_pipeline_llm_input_payload(ctx)
    linhas = [r for r in project_equivalences(payload) if r.workload == "AmazonRDS"]
    assert {r.target_provider for r in linhas} == {"OCI", "AZURE"}
    for r in linhas:
        assert (r.equivalence_type, r.portability, r.migration_risk) == (
            "functional_match",
            "semi_portable",
            "high",
        )
        # A evidência que já aparecia continua a mesma
        assert (r.requires_refactoring, r.vendor_lock_in_level) == (True, "medium")


def test_tipo_diferente_no_destino_nao_e_equivalencia_funcional():
    ctx = _contexto(
        "curado",
        meta_destino={"requires_refactoring": False, "vendor_lock_in_level": "low"},
        tipo_destino="oracle_database_on_bare_metal",
    )
    payload, _ = build_pipeline_llm_input_payload(ctx)
    linha = next(r for r in project_equivalences(payload) if r.workload == "AmazonRDS")
    assert linha.equivalence_type == "unknown"
    assert (linha.portability, linha.migration_risk) == ("portable", "low")


def test_tipo_inferido_continua_desconhecido():
    ctx = _contexto(
        "inferido",
        meta_destino={"requires_refactoring": False, "vendor_lock_in_level": "low"},
        tipo_destino="managed_relational_database",
    )
    payload, _ = build_pipeline_llm_input_payload(ctx)
    linha = next(r for r in project_equivalences(payload) if r.workload == "AmazonRDS")
    assert (linha.equivalence_type, linha.portability, linha.migration_risk) == (
        "unknown",
        "unknown",
        "unknown",
    )


def test_destino_do_pool_e_correspondencia_de_hardware():
    equivalente = {"provedor": "OCI", "nome_servico": "OCI Compute", "sku": "E5"}
    com = _com_qualidade_do_pool(equivalente)
    assert com["metadados"] == {
        "equivalence_type": "hardware_match",
        "portability": "portable",
        # A origem dispensa refatoração; o destino sem nota não confirma
        "migration_risk": "medium",
    }
    nota = {**equivalente, "metadados": {"requires_refactoring": False}}
    assert _com_qualidade_do_pool(nota)["metadados"]["migration_risk"] == "low"
    snapshot = {
        "provedor_origem": "aws",
        "pools_computacao": [{"familia": "M7G", "regiao": "us-east-1", "equivalentes": [com]}],
        "decision_support": {"provider_scores": [], "workload_strategies": []},
    }
    linha = project_equivalences(snapshot)[0]
    assert (linha.equivalence_type, linha.migration_risk) == ("hardware_match", "medium")


def test_o_modelo_nao_recebe_os_campos_do_pool():
    com = _com_qualidade_do_pool({"provedor": "OCI", "nome_servico": "OCI Compute"})
    nota = _com_qualidade_do_pool(
        {"provedor": "AZURE", "nome_servico": "VMs", "metadados": {"notes": "curado"}}
    )
    payload: dict[str, Any] = {
        "pools_computacao": [{"familia": "M7G", "equivalentes": [com, nota]}],
        "decision_support": {"provider_scores": [], "workload_strategies": []},
    }
    enviado = _compact_decision_payload(payload)
    sem, com_nota = enviado["pools_computacao"][0]["equivalentes"]
    assert "metadados" not in sem
    assert com_nota["metadados"] == {"notes": "curado"}
    # O payload persistido continua com os campos
    assert payload["pools_computacao"][0]["equivalentes"][0]["metadados"]
