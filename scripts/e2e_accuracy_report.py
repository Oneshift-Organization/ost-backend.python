"""Real-CSV end-to-end accuracy report (no DB).

Parses a billing CSV with the production parsers, classifies every line via the
SkuMatcher (catalog + rules loaded from the seed constants), aggregates, and
prints the spec §8 accuracy metrics plus the largest orphan descriptors.

Usage:
    python scripts/e2e_accuracy_report.py <csv> [<csv> ...]
"""

from __future__ import annotations

import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.cloud_sku import CloudSku
from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.accuracy_metrics import compute_accuracy
from app.domain.services.sku_matcher import SkuMatcher
from app.infrastructure.database.seed_catalog_pareto import BASE_DEFAULTS, CATALOG_SEED
from app.infrastructure.database.seed_classification_rules import GCP_RULES
from app.infrastructure.file_ingestion.detector import detect_and_parse
from app.services.billing_aggregator import BillingAggregator

try:
    from app.infrastructure.database.seed_classification_rules import AWS_RULES
except ImportError:
    AWS_RULES = []


def _catalog(provider: str) -> list[CloudSku]:
    out: list[CloudSku] = []
    for raw in CATALOG_SEED:
        entry = {**BASE_DEFAULTS, **raw}
        if str(entry["provider"]).lower() != provider:
            continue
        out.append(
            CloudSku(
                sku_id=str(entry["sku_id"]),
                provider=str(entry["provider"]),
                service_name=str(entry["service_name"]),
                currency=str(entry["currency"]),
                service_type=entry.get("service_type"),
                category=entry.get("category"),
                component_type=str(entry.get("component_type", "unclassified")),
                vcpus=Decimal(str(entry["vcpus"])) if "vcpus" in entry else None,
                ram_gb=Decimal(str(entry["ram_gb"])) if "ram_gb" in entry else None,
            )
        )
    return out


def _rules(provider: str) -> list[SkuClassificationRule]:
    src = GCP_RULES if provider == "gcp" else AWS_RULES
    return [
        SkuClassificationRule(
            provider=provider, pattern=p, kind=k, component_type=c,
            family=fam, confidence=conf, priority=prio,
        )
        for (p, k, c, fam, conf, prio) in src
    ]


def _descriptor(rp: dict) -> str | None:
    return (
        rp.get("SKU description")
        or rp.get("UsageType")
        or rp.get("sku_description")
        or rp.get("usage_type")
    )


def run(csv: Path) -> None:
    parser, df = detect_and_parse(csv, uuid4(), uuid4())
    provider = str(df.iloc[0]["provider"]).lower()
    matcher = SkuMatcher(_catalog(provider), _rules(provider))

    items: list[BillingEnriched] = []
    total = Decimal("0")
    orphan_by_desc: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for _, row in df.iterrows():
        rp = row["raw_payload"] if isinstance(row["raw_payload"], dict) else {}
        cost_type = str(rp.get("Cost type", "")).lower()
        record_type = rp.get("RecordType", "")
        if (
            record_type in ("StatementTotal", "InvoiceTotal")
            or rp.get("ProductCode") == "Tax"
            or cost_type in ("tax", "total", "rounding error")
        ):
            continue
        descriptor = _descriptor(rp) or row.get("service_raw") or row["service"]
        cost = Decimal(str(row["cost"]))
        total += cost
        m = matcher.match(descriptor, usage_unit=row.get("usage_unit"))
        if m.component_type == "unclassified":
            orphan_by_desc[str(descriptor)] += cost
        meta = {"component_type": m.component_type}
        if m.family:
            meta["family"] = m.family
        items.append(
            BillingEnriched(
                user_id=uuid4(), report_id=uuid4(), provider=provider,
                billing_period="2025-12", service_name=str(row["service"]),
                cost=cost, currency="USD", source_line_hash=uuid4().hex,
                pricing_source="sku_catalog" if m.sku_id else "fallback",
                pricing_confidence="low", usage_type=str(descriptor),
                usage_amount=Decimal(str(row["usage_amount"])) if row.get("usage_amount") else None,
                usage_unit=row.get("usage_unit"), region=row.get("region"),
                component_type=m.component_type, mapping_confidence=m.mapping_confidence,
                match_layer=m.match_layer, sku_metadata=meta,
            )
        )

    import asyncio

    result = asyncio.run(BillingAggregator().aggregate(items, provider))
    metrics = compute_accuracy(total, result.auxiliary.orphan, result.confidence_breakdown)
    aux = result.auxiliary

    print(f"\n===== {csv.name}  ({type(parser).__name__}, {len(items)} lines) =====")
    print(f"total       ${float(metrics.total_cost):,.2f}")
    print(f"mapped      ${float(metrics.mapped_cost):,.2f}")
    print(f"orphan      ${float(metrics.orphan_cost):,.2f}")
    print(f"COVERAGE    {float(metrics.coverage) * 100:.1f}%   orphan {float(metrics.orphan_ratio) * 100:.1f}%")
    print(f"confidence  " + ", ".join(f"{k}=${float(v):,.0f}" for k, v in metrics.confidence_breakdown.items()))
    print(
        f"buckets     pools=${float(sum((p.monthly_cost for p in result.pools), Decimal('0'))):,.0f}  "
        f"gke=${float(aux.gke_control_plane):,.0f}  db=${float(aux.database):,.0f}  "
        f"egress=${float(aux.network_egress):,.0f}  ssd=${float(aux.storage_ssd):,.0f}  hdd=${float(aux.storage_hdd):,.0f}"
    )
    if orphan_by_desc:
        print("top orphan descriptors:")
        for desc, c in sorted(orphan_by_desc.items(), key=lambda x: x[1], reverse=True)[:12]:
            print(f"   ${float(c):>10,.2f}  {desc[:70]}")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        run(Path(arg))
