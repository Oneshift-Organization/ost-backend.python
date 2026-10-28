"""Visões combináveis e o recorte da análise compartilhada (financeira, técnica, estratégica)."""

import json
from datetime import UTC, datetime
from itertools import combinations
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_current_user
from app.api.v1.routes.institutions import institution_repository, router
from app.core.exceptions import ForbiddenError
from app.domain.entities.institution import (
    ALL_VIEWS,
    InstitutionProfile,
    InstitutionRole,
    InstitutionView,
    Membership,
    SharedAnalysisAccess,
    profile_of_views,
    views_of_profile,
)
from app.domain.entities.institution_views import (
    RESTRICTED,
    parse_views,
    shared_couplings,
    shared_detail,
    shared_payload,
    shared_result,
    without_money,
)
from app.infrastructure.database.session import get_db_session
from app.schemas.auth import UserClaims
from app.schemas.institutions import UpdateMemberRequest

F, T, S = InstitutionView.FINANCIAL, InstitutionView.TECHNICAL, InstitutionView.STRATEGIC
EVERY_COMBINATION = [frozenset(c) for n in (1, 2, 3) for c in combinations((F, T, S), n)]


def result():
    return {
        "viability_score": 640,
        "oneshift_scores": {"financial_score": 80, "technical_score": 70, "overall_score": 75},
        "recommended_provider": "oci",
        "recommendation": "Migrar para OCI",
        "recommendation_rationale": "OCI tem a maior economia ($5,399/mês, 6%).",
        "strategic_notes": ["Começar pela rede."],
        "action_plan": ["Validar a VPN."],
        "billing_baseline": {"provider": "aws", "monthly_cost": 92103.48},
        "savings_by_source": {"oci": {"official": 10.0, "estimated": 2.0}},
        "savings_summary": {"total_monthly_savings_usd": 5398.63, "percentage_reduction": 6},
        "provider_comparison": [
            {
                "provider": "oci",
                "overall_score": 75,
                "estimated_monthly_cost": 86704.85,
                "estimated_monthly_savings": 5398.63,
                "strengths": ["Maior economia: US$ 5.399/mês"],
            }
        ],
        "cross_cloud_equivalents": [
            {
                "workload": "EC2",
                "estimated_monthly_cost": 10.5,
                "cost_delta": -1.0,
                "pricing_evidence": [{"source": 1.0, "target": 0.5}],
            }
        ],
        "agent_views": {
            "finops": {"summary": "Total spend: $92,103.48"},
            "cloud_engineering": {"summary": "Pools x86.", "migration_complexity": "medium"},
            "risk_migration": {"summary": "Banco por último."},
        },
        "decision_support": {
            "selection_reason": "Menor custo.",
            "comparison_basis": [{"provider": "oci", "current_monthly_cost": 1.0}],
            "corporate_benefits": {"programs": [{"monthly_savings": 3.0}]},
            "workload_strategies": [
                {"workload": "EC2", "evidence": [{"target_metadata": {"price_per_hour_usd": 0.1}}]}
            ],
            "score": {
                "value": 75,
                "axes": [{"id": "cost", "value": 80, "weight": 0.4, "evidence": [{"custo": 9.0}]}],
            },
        },
    }


def leaves(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, f"{path}.{key}")
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item, f"{path}[]")
    else:
        yield path, value


def numbers(value):
    return {
        path
        for path, item in leaves(value)
        if isinstance(item, int | float) and not isinstance(item, bool)
    }


def test_profile_of_before_becomes_views_and_back():
    assert views_of_profile(InstitutionProfile.ADMINISTRATIVE) == ALL_VIEWS
    assert views_of_profile(InstitutionProfile.ECONOMIST) == {F}
    for view in (F, T, S):
        assert profile_of_views(frozenset({view})).value == view.value
    assert profile_of_views(frozenset({T, S})) == InstitutionProfile.ADMINISTRATIVE


def test_admin_sees_everything_member_sees_marked_views_or_old_profile():
    now = datetime.now(UTC)

    def member(role, profile, views=frozenset()):
        return Membership(uuid4(), uuid4(), "Ana", role, profile, False, now, views)

    assert member(InstitutionRole.ADMIN, InstitutionProfile.STRATEGIC, {S}).visible_views() == (
        ALL_VIEWS
    )
    assert member(InstitutionRole.MEMBER, InstitutionProfile.STRATEGIC, {F, T}).visible_views() == {
        F,
        T,
    }
    assert member(InstitutionRole.MEMBER, InstitutionProfile.TECHNICAL).visible_views() == {T}


@pytest.mark.parametrize("views", EVERY_COMBINATION, ids=lambda v: "+".join(sorted(v)))
def test_each_combination_shows_only_its_part(views):
    raw = result()
    before = json.dumps(raw, sort_keys=True)
    out = shared_result(raw, views)
    assert json.dumps(raw, sort_keys=True) == before, "o resultado do autor não muda"

    agents = out["agent_views"]
    assert ("finops" in agents) == (F in views)
    assert ("cloud_engineering" in agents) == (T in views)
    assert ("risk_migration" in agents) == (S in views)
    assert bool(out["cross_cloud_equivalents"]) == (T in views)
    assert bool(out["decision_support"]["workload_strategies"]) == (T in views)
    assert bool(out["strategic_notes"]) == (S in views)
    assert bool(out["action_plan"]) == (S in views)
    assert bool(out["recommendation_rationale"]) == (S in views)
    # O score e a recomendação são de todos: aparecem também na lista da instituição
    assert out["decision_support"]["score"]["value"] == 75
    assert out["recommendation"] == "Migrar para OCI"

    if F in views:
        assert out["savings_summary"]["total_monthly_savings_usd"] == 5398.63
        assert out["provider_comparison"][0]["estimated_monthly_cost"] == 86704.85
        assert out["billing_baseline"]["monthly_cost"] == 92103.48
        return
    money = [
        path
        for path in numbers(out)
        if not path.endswith(("score", ".value", ".weight", "percentage_reduction"))
    ]
    assert money == []
    assert out["savings_summary"]["percentage_reduction"] is None
    assert "billing_baseline" not in out
    assert "savings_by_source" not in out
    assert out["decision_support"]["comparison_basis"] == []
    assert out["decision_support"]["corporate_benefits"] is None
    texts = " ".join(item for _, item in leaves(out) if isinstance(item, str))
    assert "$" not in texts
    if S in views:
        assert RESTRICTED in out["recommendation_rationale"]


def test_money_subtrees_and_quoted_values_are_removed():
    data = {
        "economia_por_fonte": {"preco_oficial": 10.0, "estimada": 2.0},
        "destino": {"aws": 12.5, "oci": None},
        "fracao": 0.4,
        "nome": "R$ 300 mil por mês; 1200 USD; 30% do total",
        "vizinhos": [{"servico": "RDS", "custo": 4.0, "natureza": "dados"}],
    }
    out = without_money(data)
    assert out["economia_por_fonte"] == {"preco_oficial": None, "estimada": None}
    assert out["destino"] == {"aws": None, "oci": None}
    assert out["fracao"] == 0.4
    assert out["nome"] == f"{RESTRICTED} por mês; {RESTRICTED}; 30% do total"
    assert out["vizinhos"] == [{"servico": "RDS", "custo": None, "natureza": "dados"}]
    assert shared_detail(data, ALL_VIEWS) == data


def test_payload_keeps_only_what_the_screen_reads():
    payload = {
        "moeda": "USD",
        "prompt": "segredo",
        "linhas": [{"custo": 1}],
        "premissas": {"moeda_origem": "BRL", "fx": {"taxa": "5.70"}, "outra": 1},
        "resumo_executivo": {
            "provedor_atual": "gcp",
            "custo_mensal_atual": 4890.22,
            "por_provedor": [{"provedor": "oci", "economia_por_fonte": {}, "custo": 9}],
        },
    }
    assert shared_payload(payload, frozenset({T, S})) == {
        "moeda": "USD",
        "resumo_executivo": {"provedor_atual": "gcp"},
    }
    full = shared_payload(payload, frozenset({F}))
    assert full["resumo_executivo"]["custo_mensal_atual"] == 4890.22
    assert full["resumo_executivo"]["por_provedor"] == [
        {"provedor": "oci", "economia_por_fonte": {}, "itens_gratis": None}
    ]
    assert full["premissas"] == {"moeda_origem": "BRL", "fx": {"taxa": "5.70"}}
    assert "segredo" not in json.dumps(full)
    assert shared_payload(None, frozenset({S}))["resumo_executivo"] == {"provedor_atual": None}


def test_view_filter_from_the_query():
    assert parse_views(None) is None
    assert parse_views("") is None
    assert parse_views("financial, technical") == {F, T}
    assert parse_views("financial,admin") is None


def test_member_update_takes_views_or_the_old_profile():
    body = {"role": "member", "can_publish": False}
    assert UpdateMemberRequest(**body, views=["technical", "strategic"]).chosen_views() == {T, S}
    assert UpdateMemberRequest(**body, profile="economist").chosen_views() == {F}
    for wrong in ({}, {"views": []}, {"views": ["admin"]}, {"views": ["financial"], "x": 1}):
        with pytest.raises(ValidationError):
            UpdateMemberRequest(**body, **wrong)


def no_session():
    # A recusa vem antes de qualquer consulta: a sessão nunca é usada
    return MagicMock()


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    actor = uuid4()
    repo = AsyncMock()
    app.dependency_overrides[get_current_user] = lambda: UserClaims(sub=str(actor))
    app.dependency_overrides[institution_repository] = lambda: repo
    app.dependency_overrides[get_db_session] = no_session
    with TestClient(app) as client:
        yield client, repo, actor


def test_patch_member_saves_the_chosen_views(api):
    client, repo, actor = api
    institution, member = uuid4(), uuid4()
    response = client.patch(
        f"/v1/institutions/{institution}/members/{member}",
        json={"role": "member", "views": ["financial", "technical"], "can_publish": True},
    )
    assert response.status_code == 204
    repo.update_member.assert_awaited_once_with(
        actor,
        institution,
        member,
        role=InstitutionRole.MEMBER,
        views=frozenset({F, T}),
        can_publish=True,
    )


def couplings():
    return {
        "provedor_origem": "gcp",
        "custo_atual": 4890.22,
        "resumo": {"servicos": 2, "low": 0, "medium": 1, "high": 1, "a_confirmar": 0},
        "servicos": [
            {
                "id": "s1",
                "nome": "Compute Engine",
                "categoria": "compute",
                "service_type": "vm",
                "tipo_inferido": False,
                "custo": 1972.78,
                "fracao": 0.4,
                "nivel": "medium",
                "fonte": "catalogo",
                "nivel_sugerido": None,
                "entra_no_score": True,
                "sugestoes": [{"nivel": "high", "motivo": "x86"}],
                "por_destino": {"oci": "medium"},
                "alertas": ["Licença do Windows"],
                "dependencias": {"nivel": "medium", "migra_junto_com": [{"servico": "VPC"}]},
                "equivalente_por_destino": {"oci": "Compute"},
                "opcoes_por_destino": {"oci": [{"servico": "Compute", "custo_estimado": 900.0}]},
                "pools": [{"familia": "n2", "regiao": "us-east1", "custo": 1972.78}],
            }
        ],
        "pools_sem_servico": [{"familia": "e2", "regiao": "us-central1", "custo": 12.0}],
        "reconciliacao": {"soma_linhas": 4900.0, "fora_do_escopo": 10.0, "status": "fecha"},
    }


@pytest.mark.parametrize("views", EVERY_COMBINATION, ids=lambda v: "+".join(sorted(v)))
def test_every_view_sees_the_services_with_its_depth(views):
    # Ambiente atual e Portabilidade aparecem para todos; o detalhe técnico e o dinheiro, não
    raw = couplings()
    out = shared_couplings(raw, views)
    assert raw == couplings(), "a leitura do autor não muda"
    service = out["servicos"][0]
    assert (service["nome"], service["categoria"], service["nivel"]) == (
        "Compute Engine",
        "compute",
        "medium",
    )
    assert out["resumo"]["medium"] == 1
    assert service["pools"][0]["regiao"] == "us-east1"
    if T in views:
        assert service["opcoes_por_destino"]["oci"][0]["servico"] == "Compute"
        assert service["dependencias"]["migra_junto_com"] == [{"servico": "VPC"}]
        assert service["pools"][0]["familia"] == "n2"
    else:
        assert service["sugestoes"] == [] and service["alertas"] == []
        assert service["dependencias"] is None and service["por_destino"] == {}
        assert "opcoes_por_destino" not in service and "equivalente_por_destino" not in service
        assert set(service["pools"][0]) == {"regiao", "custo"}
        assert set(out["pools_sem_servico"][0]) == {"regiao", "custo"}
    if F in views:
        assert service["custo"] == 1972.78 and service["fracao"] == 0.4
        assert out["custo_atual"] == 4890.22
        return
    assert service["fracao"] is None
    assert [path for path in numbers(out) if not path.startswith(".resumo.")] == []


@pytest.mark.parametrize("detail,views", [("applications", {S}), ("applications", {F})])
def test_details_need_their_view(api, detail, views):
    # Aplicações: só a técnica (a lista de serviços é de todos)
    client, repo, actor = api
    institution, publication = uuid4(), uuid4()
    repo.shared_analysis.return_value = SharedAnalysisAccess(uuid4(), uuid4(), frozenset(views))
    response = client.get(
        f"/v1/institutions/{institution}/publications/{publication}/{detail}?views=strategic"
    )
    assert response.status_code == 403
    repo.shared_analysis.assert_awaited_once_with(actor, institution, publication, frozenset({S}))


def test_any_of_the_views_opens_the_part():
    access = SharedAnalysisAccess(uuid4(), uuid4(), frozenset({F}))
    access.require(T, F)
    with pytest.raises(ForbiddenError):
        access.require(T)
