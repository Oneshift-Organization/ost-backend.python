"""Backend é a autoridade sobre número; o LLM só narra (Onda 7 / E4).

O schema de saída pede ao modelo campos que o backend **já calcula de forma
determinística** em `resumo_executivo` (Onda 5):

    ProviderComparison.estimated_monthly_cost
    ProviderComparison.estimated_monthly_savings
    SavingsSummaryV2.total_monthly_savings_usd / percentage_reduction
    recommended_provider

Deixar o modelo produzi-los viola a regra inegociável do projeto ("IA narra
resultados já calculados; nunca invocar LLM para fazer matemática") e, pior,
permite que a resposta da API **contradiga** o cálculo auditável — o número que
o cliente vê deixaria de bater com a reconciliação.

Este módulo sobrescreve esses campos com a verdade do backend DEPOIS do parse,
preservando tudo que é genuinamente do modelo: rationale, strengths/weaknesses,
strategic_notes, action_plan, scores qualitativos.

Função pura sobre o schema: testável sem LLM e sem banco.
"""

from __future__ import annotations

from typing import Any

from app.schemas.analysis_v2 import AnalysisV2Response, ProviderComparison

# Ordem de força da confiança, para derivar a confiança do resumo.
_CONFIDENCE_RANK = {"official": 3, "reference": 2, "low": 1, "missing": 0}


def apply_backend_authority(
    response: AnalysisV2Response,
    resumo: dict[str, Any] | None,
) -> AnalysisV2Response:
    """Sobrescreve os campos numéricos/decisórios com o cálculo do backend.

    Sem `resumo` (contexto antigo ou sem equivalentes), devolve a resposta
    intacta — degradação graciosa, não quebra.
    """
    if not resumo:
        return response

    por_provedor: list[dict[str, Any]] = list(resumo.get("por_provedor") or [])
    if not por_provedor:
        return response

    # 1. Comparação por provedor: custo e economia vêm do backend. Mantém o que
    #    o modelo escreveu de qualitativo (strengths, weaknesses, complexidade).
    by_provider = {
        str(p.get("provedor", "")).lower(): p for p in por_provedor
    }
    merged: list[ProviderComparison] = []
    seen: set[str] = set()

    for pc in response.provider_comparison:
        key = pc.provider.lower()
        truth = by_provider.get(key)
        if truth is None:
            continue  # provedor que o modelo inventou e o backend não avaliou
        seen.add(key)
        merged.append(
            pc.model_copy(
                update={
                    "estimated_monthly_cost": float(truth["custo_estimado_total"]),
                    "estimated_monthly_savings": float(truth["economia_mensal"]),
                    "confidence_level": str(truth.get("confianca", "low")),
                }
            )
        )

    # Provedores que o backend avaliou e o modelo omitiu entram mesmo sem
    # narrativa — o número não pode sumir do relatório.
    for key, truth in by_provider.items():
        if key in seen:
            continue
        merged.append(
            ProviderComparison(
                provider=str(truth.get("provedor", key)).upper(),
                estimated_monthly_cost=float(truth["custo_estimado_total"]),
                estimated_monthly_savings=float(truth["economia_mensal"]),
                confidence_level=str(truth.get("confianca", "low")),
            )
        )

    merged.sort(key=lambda p: p.estimated_monthly_savings, reverse=True)

    # 2. Provedor recomendado: decisão determinística do backend.
    recomendado = resumo.get("recomendado")
    best = by_provider.get(str(recomendado).lower()) if recomendado else None

    # 3. Resumo de economia: do provedor recomendado; zero quando o backend
    #    concluiu que não vale migrar (nenhum elegível).
    atual = float(resumo.get("custo_mensal_atual") or 0.0)
    economia = float(best["economia_mensal"]) if best else 0.0
    reducao = round(economia / atual * 100, 2) if atual > 0 else 0.0
    savings = response.savings_summary.model_copy(
        update={
            "total_monthly_savings_usd": economia,
            "percentage_reduction": reducao,
            "confidence_level": str(best.get("confianca", "low")) if best else "low",
        }
    )

    return response.model_copy(
        update={
            "provider_comparison": merged,
            "recommended_provider": str(recomendado).upper() if recomendado else "",
            "savings_summary": savings,
        }
    )
