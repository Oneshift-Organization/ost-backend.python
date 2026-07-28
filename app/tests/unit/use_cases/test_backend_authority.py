"""Backend manda no número; o LLM só narra (Onda 7 / E4).

Invariante: nenhum número financeiro da resposta pode vir do modelo. Se o LLM
inventar custo/economia/provedor, o backend sobrescreve com o cálculo
determinístico — e o que o modelo escreveu de qualitativo é preservado.
"""
from __future__ import annotations

from app.schemas.analysis_v2 import (
    AnalysisV2Response,
    ProviderComparison,
    SavingsSummaryV2,
)
from app.use_cases.analysis.backend_authority import apply_backend_authority

# Verdade do backend (formato de `MigrationContext.resumo_executivo`).
_RESUMO = {
    "provedor_atual": "aws",
    "custo_mensal_atual": 92103.48,
    "recomendado": "oci",
    "por_provedor": [
        {"provedor": "oci", "custo_estimado_total": 60103.25,
         "economia_mensal": 32000.23, "economia_anual": 384002.79,
         "confianca": "reference", "cobertura": 0.79},
        {"provedor": "azure", "custo_estimado_total": 109375.74,
         "economia_mensal": -17272.26, "economia_anual": -207267.12,
         "confianca": "reference", "cobertura": 0.79},
    ],
}


def _llm_response_com_numeros_inventados() -> AnalysisV2Response:
    return AnalysisV2Response(
        recommended_provider="AZURE",           # errado: backend diz OCI
        recommendation="Migrar para Azure",
        recommendation_rationale="Narrativa qualitativa do modelo.",
        provider_comparison=[
            ProviderComparison(
                provider="oci",
                estimated_monthly_cost=1.0,      # inventado
                estimated_monthly_savings=999.0,  # inventado
                strengths=["preço competitivo"],
                weaknesses=["menos regiões"],
                migration_complexity="medium",
            ),
        ],
        savings_summary=SavingsSummaryV2(
            total_monthly_savings_usd=12345.0,   # inventado
            percentage_reduction=99.0,           # inventado
        ),
        strategic_notes=["nota do modelo"],
    )


def test_numeros_do_llm_sao_sobrescritos() -> None:
    out = apply_backend_authority(_llm_response_com_numeros_inventados(), _RESUMO)
    oci = next(p for p in out.provider_comparison if p.provider.lower() == "oci")
    assert oci.estimated_monthly_cost == 60103.25
    assert oci.estimated_monthly_savings == 32000.23
    assert oci.confidence_level == "reference"


def test_provedor_recomendado_vem_do_backend() -> None:
    out = apply_backend_authority(_llm_response_com_numeros_inventados(), _RESUMO)
    assert out.recommended_provider == "OCI"  # não AZURE, que o modelo disse


def test_savings_summary_derivado_do_recomendado() -> None:
    out = apply_backend_authority(_llm_response_com_numeros_inventados(), _RESUMO)
    assert out.savings_summary.total_monthly_savings_usd == 32000.23
    # 32000.23 / 92103.48 = 34.74%
    assert out.savings_summary.percentage_reduction == 34.74


def test_qualitativo_do_modelo_e_preservado() -> None:
    out = apply_backend_authority(_llm_response_com_numeros_inventados(), _RESUMO)
    oci = next(p for p in out.provider_comparison if p.provider.lower() == "oci")
    assert oci.strengths == ["preço competitivo"]
    assert oci.weaknesses == ["menos regiões"]
    assert oci.migration_complexity == "medium"
    assert out.recommendation_rationale == "Narrativa qualitativa do modelo."
    assert out.strategic_notes == ["nota do modelo"]


def test_provedor_omitido_pelo_modelo_entra_com_o_numero() -> None:
    """O modelo só narrou OCI; Azure não pode sumir do relatório."""
    out = apply_backend_authority(_llm_response_com_numeros_inventados(), _RESUMO)
    provedores = {p.provider.lower() for p in out.provider_comparison}
    assert provedores == {"oci", "azure"}
    azure = next(p for p in out.provider_comparison if p.provider.lower() == "azure")
    assert azure.estimated_monthly_cost == 109375.74


def test_provedor_alucinado_pelo_modelo_e_descartado() -> None:
    resp = _llm_response_com_numeros_inventados()
    resp.provider_comparison.append(
        ProviderComparison(provider="ibm-cloud", estimated_monthly_cost=5.0)
    )
    out = apply_backend_authority(resp, _RESUMO)
    assert "ibm-cloud" not in {p.provider.lower() for p in out.provider_comparison}


def test_sem_resumo_degrada_sem_quebrar() -> None:
    original = _llm_response_com_numeros_inventados()
    assert apply_backend_authority(original, None) is original
    assert apply_backend_authority(original, {}) is original


def test_backend_sem_economia_zera_o_resumo() -> None:
    """Nenhum provedor elegível → economia 0, não o número do modelo."""
    resumo = {**_RESUMO, "recomendado": None}
    out = apply_backend_authority(_llm_response_com_numeros_inventados(), resumo)
    assert out.recommended_provider == ""
    assert out.savings_summary.total_monthly_savings_usd == 0.0
    assert out.savings_summary.percentage_reduction == 0.0
