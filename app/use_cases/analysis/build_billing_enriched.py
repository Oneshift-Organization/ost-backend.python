from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.exceptions import NotFoundError
from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.unmapped_billing_line import UnmappedBillingLine
from app.domain.repositories.i_billing_enriched_repo import IBillingEnrichedRepository
from app.domain.repositories.i_billing_item_repo import IBillingItemRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.domain.repositories.i_sku_classification_rule_repo import (
    ISkuClassificationRuleRepository,
)
from app.domain.repositories.i_unmapped_billing_repo import IUnmappedBillingRepository
from app.domain.services.sku_matcher import SkuMatch, SkuMatcher
from app.domain.services.sku_normalization import normalize_provider

# component_type -> (category, service_type) for the migration context.
_CATEGORY_BY_COMPONENT: dict[str, tuple[str | None, str | None]] = {
    "core": ("compute", "virtual_server"),
    "ram": ("compute", "virtual_server"),
    "os_license": ("compute", "os_license"),
    "gke_control_plane": ("containers", "managed_kubernetes"),
    "network_egress": ("networking", "network"),
    "storage_ssd": ("storage", "block_storage"),
    "storage_hdd": ("storage", "block_storage"),
    "database": ("database", "relational_database"),
    "unclassified": (None, None),
}


@dataclass(frozen=True)
class BuildBillingEnrichedInput:
    user_id: UUID
    report_id: UUID


class BuildBillingEnrichedUseCase:
    """Enrich billing items via the Matching Ladder (SkuMatcher).

    Replaces the previous exact-string ontology lookup. Each line is classified
    into a ``component_type`` with a traceable ``match_layer`` /
    ``mapping_confidence``, and the structured SKU id (when matched) is stored
    in ``sku_id`` (no longer the raw descriptor). See
    ``docs/sku-matching.md`` §5.
    """

    def __init__(
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingItemRepository,
        enriched_repo: IBillingEnrichedRepository,
        sku_repo: ICloudSkuRepository,
        rule_repo: ISkuClassificationRuleRepository,
        unmapped_repo: IUnmappedBillingRepository,
    ) -> None:
        self._reports = report_repo
        self._items = item_repo
        self._enriched = enriched_repo
        self._sku = sku_repo
        self._rules = rule_repo
        self._unmapped = unmapped_repo

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

        provider = normalize_provider(items[0].provider.value)
        catalog = await self._sku.list_by_provider(provider)
        rules = await self._rules.list_active_by_provider(provider)
        matcher = SkuMatcher(catalog, rules)

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
            if (
                record_type in ("StatementTotal", "InvoiceTotal")
                or raw_payload.get("ProductCode") == "Tax"
                or cost_type in ("tax", "total", "rounding error")
            ):
                continue

            # The discriminating descriptor is the per-SKU text in raw_payload
            # (GCP "SKU description" / AWS "UsageType"), not the coarse service
            # name in service_raw/service.
            descriptor = (
                raw_payload.get("SKU description")
                or raw_payload.get("UsageType")
                or raw_payload.get("sku_description")
                or raw_payload.get("usage_type")
                or getattr(item, "service_raw", None)
                or item.service
            )
            match = matcher.match(descriptor, usage_unit=item.usage_unit)

            category, service_type = self._resolve_taxonomy(match)
            scope = "global_zero" if raw_payload.get("scope") == "global_zero" else "line_item"
            project_id = (
                raw_payload.get("Project ID")
                or raw_payload.get("project", {}).get("id")
                or raw_payload.get("project_id")
            )

            if match.match_layer == "L5":
                unmapped_items.append(
                    UnmappedBillingLine(
                        user_id=item.user_id,
                        report_id=item.report_id,
                        provider=item.provider.value,
                        billing_period=item.billing_period,
                        usage_type=descriptor,
                        service_name=item.service,
                        region=item.region,
                        reason="no_match_ladder",
                        raw_payload=raw_payload,
                        source_line_hash=item.line_hash,
                    )
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
                    usage_type=descriptor,
                    region=item.region,
                    project_id=project_id,
                    usage_amount=item.usage_amount,
                    usage_unit=item.usage_unit,
                    cost=item.cost,
                    currency=item.currency,
                    sku_id=match.sku_id,
                    sku_metadata=_build_metadata(match),
                    component_type=match.component_type,
                    mapping_confidence=match.mapping_confidence,
                    match_layer=match.match_layer,
                    pricing_source=_pricing_source(match),
                    pricing_confidence="low",
                    scope=scope,
                    source_line_hash=item.line_hash,
                )
            )

        await self._enriched.delete_by_report(payload.report_id, payload.user_id)
        inserted = await self._enriched.bulk_insert(enriched_items)
        await self._unmapped.bulk_insert(unmapped_items)
        return inserted

    @staticmethod
    def _resolve_taxonomy(match: SkuMatch) -> tuple[str | None, str | None]:
        if match.category is not None or match.service_type is not None:
            return match.category, match.service_type
        return _CATEGORY_BY_COMPONENT.get(match.component_type, (None, None))


def _build_metadata(match: SkuMatch) -> dict[str, object] | None:
    metadata: dict[str, object] = {"component_type": match.component_type}
    if match.family is not None:
        metadata["family"] = match.family
    if match.vcpus is not None:
        metadata["vcpus"] = str(match.vcpus)
    if match.ram_gb is not None:
        metadata["ram_gb"] = str(match.ram_gb)
    return metadata


def _pricing_source(match: SkuMatch) -> str:
    if match.sku_id is not None:
        return "sku_catalog"
    if match.component_type != "unclassified":
        return "fallback"
    return "missing"
