from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.analysis import (
    BillingItemSummary,
    EquivalentService,
    MigrationContext,
    ServiceCategoryGroup,
    ServiceComparison,
)
from app.domain.interfaces.i_service_repositories import (
    IServiceComparisonRepository,
    IServiceMetadataRepository,
)
from app.domain.repositories.i_billing_item_repo import IBillingItemRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository


@dataclass(frozen=True)
class BuildMigrationContextInput:
    user_id: UUID
    report_id: UUID


PROVIDER_SERVICE_ALIASES: dict[str, dict[str, str]] = {
    "aws": {
        "EC2-Instances": "Amazon EC2",
        "EC2-Other": "Amazon EC2",
        "EC2 Container Registry (ECR)": "Amazon Elastic Container Registry (ECR)",
        "Elastic Container Service for Kubernetes": "Amazon Elastic Kubernetes Service (EKS)",
        "Elastic Container Service": "Amazon Elastic Container Service (ECS)",
        "Relational Database Service": "Amazon RDS",
        "Elastic File System": "Amazon Elastic File System (EFS)",
        "Key Management Service": "AWS Key Management Service (KMS)",
        "Lambda": "AWS Lambda",
        "Elastic Load Balancing": "Elastic Load Balancing",
        "DMS": "AWS Database Migration Service",
        "Q": "Amazon Q Business",
        "DocumentDB (with MongoDB compatibility)": "Amazon DocumentDB",
        "CloudWatch Events": "Amazon CloudWatch",
        "Elastic Container Registry Public": "Amazon Elastic Container Registry (ECR)",
        "Kinesis Firehose": "Amazon Kinesis Data Streams",
        "Simple Workflow Service": "AWS Step Functions",
    },
}


def _normalize(name: str) -> str:
    return " ".join(name.lower().split())


class BuildMigrationContextUseCase:
    def __init__(
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingItemRepository,
        metadata_repo: IServiceMetadataRepository,
        comparison_repo: IServiceComparisonRepository,
    ) -> None:
        self._report_repo = report_repo
        self._item_repo = item_repo
        self._metadata_repo = metadata_repo
        self._comparison_repo = comparison_repo

    async def execute(self, payload: BuildMigrationContextInput) -> MigrationContext:  # noqa: PLR0915
        report = await self._report_repo.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"report {payload.report_id} not found")
        if report.status != "done":
            raise ValueError(f"report {payload.report_id} status is {report.status}, expected done")
        if report.provider is None:
            raise ValueError(f"report {payload.report_id} has no provider set")

        source_provider = report.provider.value

        items = await self._item_repo.list_by_report(
            payload.report_id, payload.user_id, limit=100000, offset=0,
        )
        if not items:
            raise ValueError(f"report {payload.report_id} has no billing items")

        billing_period = items[0].billing_period
        total_spend = Decimal("0")

        # ── 1. Aggregate billing items by service ──────────────
        service_agg: dict[str, dict[str, Any]] = {}
        for item in items:
            total_spend += item.cost
            key = item.service
            if key not in service_agg:
                service_agg[key] = {
                    "service_name": item.service,
                    "provider": item.provider.value,
                    "total_cost": Decimal("0"),
                    "currency": item.currency,
                    "line_count": 0,
                }
            service_agg[key]["total_cost"] += item.cost
            service_agg[key]["line_count"] += 1

        # ── 2. Load source provider catalog ─────────────────────
        source_comparisons = await self._comparison_repo.list_by_provider(source_provider)
        source_metadata = await self._metadata_repo.list_by_providers([source_provider])
        canonicals = {_normalize(c.service_name): c for c in source_comparisons}
        meta_map = {_normalize(m.service_name): m for m in source_metadata}

        # ── 3. Load target provider metadata ────────────────────
        target_providers = list(
            {c.provider for c in await self._comparison_repo.list_all()
             if c.provider != source_provider}
        )
        target_metadata_all = await self._metadata_repo.list_by_providers(target_providers)
        target_meta_map: dict[tuple[str, str], Any] = {
            (m.provider, m.service_name): m.metadata for m in target_metadata_all
        }
        target_docs_map: dict[tuple[str, str], str | None] = {
            (m.provider, m.service_name): m.docs_costs for m in target_metadata_all
        }
        # Build cache: (category, service_type) → list of equivalents from all other providers
        equivalents_cache: dict[tuple[str, str], list[EquivalentService]] = {}
        async def _get_equivalents(category: str, service_type: str) -> list[EquivalentService]:
            key = (category, service_type)
            if key not in equivalents_cache:
                raw = await self._comparison_repo.find_equivalents(category, source_provider)
                equivalents_cache[key] = [
                    EquivalentService(
                        provider=eq.provider,
                        service_name=eq.service_name,
                        category=category,
                        service_type=eq.service_type,
                        metadata=target_meta_map.get((eq.provider, eq.service_name)),
                        docs_costs=target_docs_map.get((eq.provider, eq.service_name)),
                    )
                    for eq in raw
                    if eq.service_type == service_type
                ]
            return equivalents_cache[key]

        # ── 4. Match & build enriched summaries ─────────────────
        def _canonical_name(name: str) -> str:
            aliases = PROVIDER_SERVICE_ALIASES.get(source_provider, {})
            return aliases.get(name, name)

        def _match(svc_name: str) -> ServiceComparison | None:
            n = _normalize(_canonical_name(svc_name))
            comp = canonicals.get(n)
            if comp:
                return comp
            if meta_map.get(n):
                return None
            for canon_name, comp in canonicals.items():
                if n in canon_name or canon_name in n:
                    return comp
            for canon_name in meta_map:
                if n in canon_name or canon_name in n:
                    return None
            return None

        # Sort by cost desc first, then build summaries with equivalents
        sorted_services = sorted(
            service_agg.values(),
            key=lambda x: x["total_cost"],
            reverse=True,
        )

        categories: dict[str, dict[str, Any]] = {}
        for raw in sorted_services:
            comp = _match(raw["service_name"])
            meta = meta_map.get(_normalize(raw["service_name"]))
            category_name = comp.category if comp else (meta.category if meta else "unknown")
            service_type = comp.service_type if comp else "unknown"

            equivalents = (
                await _get_equivalents(comp.category, comp.service_type)
                if comp else []
            )

            svc = BillingItemSummary(
                service_name=raw["service_name"],
                provider=raw["provider"],
                total_cost=raw["total_cost"],
                service_type=service_type,
                currency=raw["currency"],
                line_count=raw["line_count"],
                equivalents=equivalents,
            )

            if category_name not in categories:
                categories[category_name] = {
                    "category": category_name,
                    "billing_total": Decimal("0"),
                    "services": [],
                }
            categories[category_name]["services"].append(svc)
            categories[category_name]["billing_total"] += svc.total_cost

        return MigrationContext(
            source_provider=source_provider,
            billing_period=billing_period,
            total_spend=total_spend,
            currency="USD",
            categories=[
                ServiceCategoryGroup(
                    category=c["category"],
                    billing_total=c["billing_total"],
                    services=c["services"],
                )
                for c in categories.values()
            ],
        )
