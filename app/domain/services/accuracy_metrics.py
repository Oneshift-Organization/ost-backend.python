"""Deterministic SKU-matching accuracy metrics (spec §8).

Computed from the aggregator output so the same numbers drive both the
governance flag and the golden-report merge gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AccuracyMetrics:
    total_cost: Decimal
    orphan_cost: Decimal
    mapped_cost: Decimal
    coverage: Decimal
    orphan_ratio: Decimal
    confidence_breakdown: dict[str, Decimal]


def compute_accuracy(
    total_cost: Decimal,
    orphan_cost: Decimal,
    confidence_breakdown: dict[str, Decimal] | None = None,
) -> AccuracyMetrics:
    mapped_cost = total_cost - orphan_cost
    if total_cost > 0:
        coverage = mapped_cost / total_cost
        orphan_ratio = orphan_cost / total_cost
    else:
        coverage = Decimal("0")
        orphan_ratio = Decimal("0")
    return AccuracyMetrics(
        total_cost=total_cost,
        orphan_cost=orphan_cost,
        mapped_cost=mapped_cost,
        coverage=coverage,
        orphan_ratio=orphan_ratio,
        confidence_breakdown=dict(confidence_breakdown or {}),
    )
