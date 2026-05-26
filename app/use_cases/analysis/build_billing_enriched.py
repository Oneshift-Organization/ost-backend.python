from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.unmapped_billing_line import UnmappedBillingLine
from app.domain.interfaces.i_service_repositories import IServiceComparisonRepository
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_item_repo import IBillingItemRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_service_ontology_repo import IServiceOntologyRepository
from app.domain.repositories.i_unmapped_billing_repo import IUnmappedBillingRepository


@dataclass(frozen=True)
class BuildBillingEnrichedInput:
    user_id: UUID
    report_id: UUID


class BuildBillingEnrichedUseCase:
    def __init__(
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingItemRepository,
        enriched_repo: IBillingEnrichedRepository,
        ontology_repo: IServiceOntologyRepository,
        unmapped_repo: IUnmappedBillingRepository,
        comparison_repo: IServiceComparisonRepository,
    ) -> None:
        self._reports = report_repo
        self._items = item_repo
        self._enriched = enriched_repo
        self._ontology = ontology_repo
        self._unmapped = unmapped_repo
        self._comparison = comparison_repo

    async def execute(self, payload: BuildBillingEnrichedInput) -> int:
        report = await self._reports.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise NotFoundError(f"report {payload.report_id} not found")
        if report.status not in {"done", "processing"}:
            raise ValueError(
                f"report {payload.report_id} status is {report.status}, expected done or processing"
            )
        items = await self._items.list_by_report(
            payload.report_id, payload.user_id, limit=100000, offset=0
        )
        if not items:
            raise ValueError(f"report {payload.report_id} has no billing items")

        enriched_items: list[BillingEnriched] = []
        unmapped_items: list[UnmappedBillingLine] = []

        for item in items:
            raw_payload = (
                item.tags.get("raw_payload", {}) if isinstance(item.tags, dict) else {}
            )
            if not isinstance(raw_payload, dict):
                raw_payload = {}

            # ── Skip financial metadata / summary lines ──────────
            record_type = raw_payload.get("RecordType", "")
            cost_type = raw_payload.get("Cost type", "").lower()
            if record_type in ("StatementTotal", "InvoiceTotal") or cost_type in ("tax", "total", "rounding error"):
                continue

            usage_type = getattr(item, "service_raw", None) or item.service
            provider = item.provider.value

            ontology = await self._ontology.find_by_provider_and_usage_type(
                provider, usage_type
            )

            if ontology is None:
                ontology = await self._ontology.find_by_provider_and_usage_type(
                    provider, item.service
                )

            category = ontology.category if ontology else None
            service_type = ontology.service_type if ontology else None

            if ontology is None:
                fallback_name = _canonical_service_name(provider, item.service)
                fallback = await self._comparison.find_by_provider_and_service(
                    provider, fallback_name
                )
                if fallback is not None:
                    category = fallback.category
                    service_type = fallback.service_type

                unmapped_items.append(
                    UnmappedBillingLine(
                        user_id=item.user_id,
                        report_id=item.report_id,
                        provider=provider,
                        billing_period=item.billing_period,
                        usage_type=usage_type,
                        service_name=item.service,
                        region=item.region,
                        reason="ontology_not_found",
                        raw_payload=raw_payload,
                        source_line_hash=item.line_hash,
                    )
                )

            scope = "line_item"
            if raw_payload.get("scope") == "global_zero":
                scope = "global_zero"

            sku_id = (
                raw_payload.get("UsageType")
                or raw_payload.get("sku_id")
                or raw_payload.get("SKU ID")
                or raw_payload.get("usage_type")
                or item.service_raw
            )

            project_id = (
                raw_payload.get("Project ID")
                or raw_payload.get("project", {}).get("id")
                or raw_payload.get("project_id")
            )

            enriched_items.append(
                BillingEnriched(
                    user_id=item.user_id,
                    report_id=item.report_id,
                    provider=item.provider.value,
                    billing_period=item.billing_period,
                    service_name=item.service,
                    service_type=service_type,
                    category=category,
                    usage_type=usage_type,
                    region=item.region,
                    project_id=project_id,
                    usage_amount=item.usage_amount,
                    usage_unit=item.usage_unit,
                    cost=item.cost,
                    currency=item.currency,
                    sku_id=sku_id,
                    sku_metadata=None,
                    pricing_source="missing",
                    pricing_confidence="low",
                    scope=scope,
                    source_line_hash=item.line_hash,
                )
            )

        await self._enriched.delete_by_report(payload.report_id, payload.user_id)
        inserted = await self._enriched.bulk_insert(enriched_items)
        await self._unmapped.bulk_insert(unmapped_items)
        return inserted


def _canonical_service_name(provider: str, name: str) -> str:
    aliases = {
        "aws": {
            "EC2-Instances": "Amazon EC2",
            "EC2-Other": "Amazon EC2",
            "Elastic Container Service for Kubernetes": "Amazon EKS",
            "Elastic Container Service": "Amazon ECS",
            "Relational Database Service": "Amazon RDS",
            "Elastic File System": "Amazon EFS",
            "Key Management Service": "AWS KMS",
            "AmazonEC2": "Amazon EC2",
            "AmazonRDS": "Amazon RDS",
            "AmazonS3": "Amazon S3",
            "AmazonVPC": "Amazon VPC",
            "AmazonCloudWatch": "Amazon CloudWatch",
            "AmazonEFS": "Amazon EFS",
            "AmazonElastiCache": "Amazon ElastiCache",
            "AmazonECR": "Amazon ECR",
        },
        "gcp": {
            "Compute Engine": "Compute Engine",
        },
    }
    provider_aliases = aliases.get(provider, {})
    return provider_aliases.get(name, name)
