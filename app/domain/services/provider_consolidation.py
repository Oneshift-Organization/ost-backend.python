"""Consolidação por provedor + recomendação (Precision Engine v3 — Wave 5).

O payload já tem deltas por pool e por categoria. Um CFO precisa do roll-up:
"migrar para X custaria N/mês (economia Y), confiança Z". Esse cálculo é
**determinístico e feito no backend** — a IA só narra o resultado, nunca calcula
(regra inegociável).

Honestidade: cada provedor só reprecifica o que ELE consegue precificar; o resto
do gasto fica ao custo atual. Então:

    custo_estimado_total = custo_atual + (custo_equivalentes - custo_origem_coberto)
    economia_mensal      = custo_origem_coberto - custo_equivalentes

``cobertura`` = fração do gasto atual que aquele provedor precificou — um provedor
que só cobre 20% não deve ser recomendado com a mesma força que um que cobre 90%.

Função pura, sem I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Cobertura mínima para um provedor ser elegível a recomendação. Abaixo disso a
# comparação é fraca demais para sustentar uma decisão.
_MIN_COVERAGE_FOR_RECOMMENDATION = Decimal("0.5")
_CONFIDENCE_RANK = {"official": 3, "reference": 2, "low": 1, "missing": 0}


@dataclass(frozen=True, slots=True)
class ProviderTally:
    """Acumulado de um provedor-alvo (custos já em moeda de análise)."""

    analyzed_cost: Decimal      # soma dos equivalentes do provedor
    source_covered: Decimal     # custo de ORIGEM que o provedor conseguiu precificar
    confidence: str             # elo mais fraco entre os equivalentes usados


def weakest_confidence(confidences: list[str]) -> str:
    """Elo mais fraco — a estimativa é tão confiável quanto seu pior insumo."""
    if not confidences:
        return "missing"
    return min(confidences, key=lambda c: _CONFIDENCE_RANK.get(c, 0))


@dataclass(frozen=True, slots=True)
class _Row:
    provedor: str
    custo_estimado_total: float
    delta_vs_atual: float
    economia_mensal: float
    economia_anual: float
    confianca: str
    cobertura: float

    def as_dict(self) -> dict[str, object]:
        return {
            "provedor": self.provedor,
            "custo_estimado_total": self.custo_estimado_total,
            "delta_vs_atual": self.delta_vs_atual,
            "economia_mensal": self.economia_mensal,
            "economia_anual": self.economia_anual,
            "confianca": self.confianca,
            "cobertura": self.cobertura,
        }


def consolidate(
    *,
    source_provider: str,
    current_total: Decimal,
    tallies: dict[str, ProviderTally],
) -> dict[str, object]:
    """Roll-up por provedor + recomendação determinística.

    Retorna um dict pronto para o payload (`resumo_executivo`).
    """
    rows: list[_Row] = []
    for provider, t in tallies.items():
        estimated_total = current_total - t.source_covered + t.analyzed_cost
        delta = estimated_total - current_total  # = analyzed - source_covered
        economia_mensal = -delta
        cobertura = (t.source_covered / current_total) if current_total > 0 else Decimal("0")
        rows.append(_Row(
            provedor=provider,
            custo_estimado_total=round(float(estimated_total), 2),
            delta_vs_atual=round(float(delta), 2),
            economia_mensal=round(float(economia_mensal), 2),
            economia_anual=round(float(economia_mensal * 12), 2),
            confianca=t.confidence,
            cobertura=round(float(cobertura), 4),
        ))

    rows.sort(key=lambda r: r.economia_mensal, reverse=True)

    # Recomendação: maior economia entre os elegíveis (economia > 0 e cobertura
    # suficiente). Desempate por confiança. Nenhum elegível → recomenda manter.
    min_cov = float(_MIN_COVERAGE_FOR_RECOMMENDATION)
    elegiveis = [r for r in rows if r.economia_mensal > 0 and r.cobertura >= min_cov]
    elegiveis.sort(
        key=lambda r: (r.economia_mensal, _CONFIDENCE_RANK.get(r.confianca, 0)),
        reverse=True,
    )
    if elegiveis:
        rec = elegiveis[0]
        recomendado: str | None = rec.provedor
        justificativa = (
            f"{rec.provedor}: economia estimada de {rec.economia_mensal}/mês "
            f"({rec.economia_anual}/ano), cobrindo {rec.cobertura * 100:.0f}% "
            f"do gasto com confiança '{rec.confianca}'."
        )
    else:
        recomendado = None
        justificativa = (
            "Nenhum provedor apresenta economia com cobertura suficiente; "
            "manter o provedor atual ou coletar mais dados de preço."
        )

    return {
        "provedor_atual": source_provider,
        "custo_mensal_atual": round(float(current_total), 2),
        "por_provedor": [r.as_dict() for r in rows],
        "recomendado": recomendado,
        "justificativa": justificativa,
    }
