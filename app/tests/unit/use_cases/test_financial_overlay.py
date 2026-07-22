from app.schemas.analysis_v2 import AnalysisV2Response, ProviderComparison, SavingsSummaryV2
from app.use_cases.analysis.financial_overlay import apply_financial_overlay

PAYLOAD = {
    "gasto_total": 1000.0,
    "pools_computacao": [
        {
            "familia": "e2",
            "regiao": "us-east1",
            "equivalentes": [
                {
                    "provedor": "AWS",
                    "financeiro": {"custo_mensal_estimado": "700", "delta_custo": "-300"},
                },
                {
                    "provedor": "OCI",
                    "financeiro": {"custo_mensal_estimado": "600", "delta_custo": "-400"},
                },
            ],
        }
    ],
}


def _resp() -> AnalysisV2Response:
    return AnalysisV2Response(
        recommended_provider="OCI",
        provider_comparison=[
            ProviderComparison(
                provider="OCI", estimated_monthly_cost=999.0, estimated_monthly_savings=1.0
            )
        ],
        savings_summary=SavingsSummaryV2(
            total_monthly_savings_usd=123.0,
            percentage_reduction=99.0,
            payback_period_months=6,
        ),
    )


def test_overwrites_savings_from_payload_deltas():
    out = apply_financial_overlay(_resp(), PAYLOAD)
    assert out.savings_summary.total_monthly_savings_usd == 400.0
    assert out.savings_summary.percentage_reduction == 40.0
    assert out.savings_summary.payback_period_months is None


def test_overwrites_provider_comparison_and_flags_divergence():
    out = apply_financial_overlay(_resp(), PAYLOAD)
    oci = next(p for p in out.provider_comparison if p.provider.upper() == "OCI")
    assert oci.estimated_monthly_cost == 600.0
    assert oci.estimated_monthly_savings == 400.0
    assert any("LLM_NUMERIC_DIVERGENCE" in str(f) for f in out.flags.get("numeric_validation", []))


def test_unknown_recommended_provider_becomes_inconclusive():
    r = _resp()
    r.recommended_provider = "AZURE"  # não está no payload
    out = apply_financial_overlay(r, PAYLOAD)
    assert out.recommended_provider == "inconclusive"


def test_scores_clamped_to_100():
    r = _resp()
    r.oneshift_scores.financial_score = 850  # LLM alucinou escala
    out = apply_financial_overlay(r, PAYLOAD)
    assert out.oneshift_scores.financial_score == 100


LEGACY_PAYLOAD = {
    "source_provider": "aws",
    "billing_period": "2026-06",
    "currency": "USD",
    "total_spend": 1000.0,
    "workloads": [
        {
            "canonical_service": "virtual_server",
            "category": "compute",
            "service_name": "e2 Compute (proj-1)",
            "equivalents": [
                {
                    "provider": "AWS",
                    "financials": {"estimated_monthly_cost": 700.0, "cost_delta": -300.0},
                },
                {
                    "provider": "OCI",
                    "financials": {"estimated_monthly_cost": 600.0, "cost_delta": -400.0},
                },
            ],
        }
    ],
}


def test_legacy_workloads_shape_aggregates_same_as_pools():
    """serialize_as_legacy() output (EN keys, workloads[]/equivalents[]) must
    aggregate through the overlay exactly like the PT pools_computacao shape."""
    out = apply_financial_overlay(_resp(), LEGACY_PAYLOAD)
    oci = next(p for p in out.provider_comparison if p.provider.upper() == "OCI")
    assert oci.estimated_monthly_cost == 600.0
    assert oci.estimated_monthly_savings == 400.0
    assert out.savings_summary.total_monthly_savings_usd == 400.0
    assert out.savings_summary.percentage_reduction == 40.0
