from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_sku import CloudSku
from app.domain.repositories.i_cloud_sku_repo import ICloudSkuRepository
from app.infrastructure.cloud_clients.gcp_catalog_client import (
    GcpCatalogClient,
    GcpSkuEntry,
)

logger = logging.getLogger(__name__)

# ── Filter rules ───────────────────────────────────────────────
_SKIP_DESC_PATTERNS = re.compile(
    r"(Commitment|Discount|Premium OS|Sustained Usage|Spot|Preemptible|"
    r"Licensing Fee|Sole Tenancy|Premium|Trial|Support)",
    re.IGNORECASE,
)

# ── Parsing helpers ────────────────────────────────────────────
# Category resource groups that indicate compute capacity
_COMPUTE_CORE_RG = re.compile(r"(?i)(Core|VCPU|CPU|N\d+|C\d+|E2|C2|N2|T2|Z3)")
_COMPUTE_RAM_RG = re.compile(r"(?i)(Ram|Memory|RAM)")


def _should_skip(entry: GcpSkuEntry) -> bool:
    """Return True if this SKU should be excluded from the catalog."""
    desc = entry.description
    rg = entry.category_resource_group
    if _SKIP_DESC_PATTERNS.search(desc):
        return True
    if not entry.service_regions:
        return True
    return False


def _parse_vcpus_ram(entry: GcpSkuEntry) -> tuple[Decimal | None, Decimal | None]:
    """Extract vcpus and ram_gb from a GCP SKU entry.

    GCP bills compute as separate components:
      - Core/CPU SKUs → 1 vCPU, 0 RAM
      - RAM SKUs → 0 vCPUs, unit RAM from pricing
      - Cloud SQL bundled SKUs → regex extract from description
    """
    desc = entry.description
    rg = entry.category_resource_group or ""
    unit = (entry.pricing_unit or "").lower()

    if entry.service_name == "Cloud SQL":
        return _parse_cloud_sql(desc)

    # Core SKUs
    if _COMPUTE_CORE_RG.search(rg) or "core" in desc.lower() or "vcpu" in desc.lower():
        vcpus = _extract_count(desc) or Decimal("1")
        return (vcpus, Decimal("0"))

    # RAM SKUs
    if _COMPUTE_RAM_RG.search(rg) or "ram" in desc.lower() or "memory" in desc.lower():
        ram = _extract_ram_gb(desc, unit)
        return (Decimal("0"), ram)

    return (None, None)


def _extract_count(text: str) -> Decimal | None:
    """Extract a leading number from text like '8 cores' or '4 vCPU'."""
    m = re.search(r"(\d+)\s*(?:core|cpu|vcpu|pack)", text, re.IGNORECASE)
    if m:
        return Decimal(m.group(1))
    return None


def _extract_ram_gb(text: str, unit: str) -> Decimal:
    """Extract RAM amount. Defaults to 1 unit (pricing unit)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:gb|gib|ram)", text, re.IGNORECASE)
    if m:
        return Decimal(m.group(1))
    # Fallback: pricing unit like "gibibyte" → 1 GiB
    if "gib" in unit or "gbyte" in unit:
        return Decimal("1")
    return Decimal("1")


def _parse_cloud_sql(desc: str) -> tuple[Decimal | None, Decimal | None]:
    """Parse Cloud SQL bundled SKUs which have vCPU+RAM in description.

    Examples:
      "Micro instance with 1 VCPU, 0.6 GB RAM"
      "Small instance with 1 VCPU, 3.75 GB RAM"
      "Cloud SQL for PostgreSQL: Standard 2 vCPUs, 7.5 GB RAM"
    """
    m_vcpu = re.search(r"(\d+)\s*V?CPU", desc, re.IGNORECASE)
    m_ram = re.search(r"(\d+(?:\.\d+)?)\s*GB\s*RAM", desc, re.IGNORECASE)
    vcpus = Decimal(m_vcpu.group(1)) if m_vcpu else Decimal("1")
    ram = Decimal(m_ram.group(1)) if m_ram else Decimal("3.75")
    return (vcpus, ram)


# ── Service type mapping ──────────────────────────────────────
_CATEGORY_MAP: dict[str, str] = {
    "Compute Engine": "compute",
    "Cloud SQL": "database",
}


def _infer_service_type(entry: GcpSkuEntry, vcpus: Decimal | None, ram_gb: Decimal | None) -> str:
    if entry.service_name == "Cloud SQL":
        return "relational_database"
    if (vcpus is not None and vcpus > 0) or (ram_gb is not None and ram_gb > 0):
        return "virtual_server"
    return "infrastructure_component"


def _infer_category(entry: GcpSkuEntry) -> str:
    return _CATEGORY_MAP.get(entry.service_name, "unknown")


# ── Use Case ──────────────────────────────────────────────────


@dataclass
class SyncResult:
    total_raw: int = 0
    skipped_filter: int = 0
    inserted: int = 0
    updated: int = 0
    with_specs: int = 0
    without_specs: int = 0
    errors: list[str] = field(default_factory=list)


class SyncGcpCatalogUseCase:
    """Fetch GCP SKUs from Cloud Billing Catalog API and sync into local catalog.

    Strategy:
      1. Fetch all Compute Engine + Cloud SQL SKUs via API.
      2. Filter out commitments, discounts, premium OS, spot, etc.
      3. Parse Core → vcpus=1/RAM, Ram → ram_gb=1/vCPUs, Cloud SQL → regex.
      4. Bulk upsert into cloud_sku_catalog.
    """

    def __init__(
        self,
        sku_repo: ICloudSkuRepository,
        catalog_client: GcpCatalogClient,
    ) -> None:
        self._repo = sku_repo
        self._client = catalog_client

    async def execute(self) -> SyncResult:
        result = SyncResult()
        now = datetime.now(timezone.utc)

        # 1. Fetch
        raw_skus = self._client.list_compute_and_sql_skus()
        result.total_raw = len(raw_skus)
        logger.info("fetched %d raw SKUs from GCP Catalog API", result.total_raw)

        # 2. Filter + 3. Parse
        domain_skus: list[CloudSku] = []
        for entry in raw_skus:
            if _should_skip(entry):
                result.skipped_filter += 1
                continue

            vcpus, ram_gb = _parse_vcpus_ram(entry)
            service_type = _infer_service_type(entry, vcpus, ram_gb)
            category = _infer_category(entry)

            if vcpus is not None or ram_gb is not None:
                result.with_specs += 1
            else:
                result.without_specs += 1

            domain_skus.append(
                CloudSku(
                    sku_id=entry.sku_id,
                    provider="GCP",
                    service_name=entry.service_name,
                    service_type=service_type,
                    category=category,
                    region=",".join(entry.service_regions) if entry.service_regions else "global",
                    usage_unit=entry.pricing_unit or "unit",
                    price_per_unit=entry.tier_price,
                    currency=entry.currency,
                    vcpus=vcpus,
                    ram_gb=ram_gb,
                    attributes={"description": entry.description, "raw": entry.raw},
                    effective_date=now,
                    collected_at=now,
                )
            )

        if not domain_skus:
            logger.warning("no SKUs to upsert after filtering")
            return result

        # 4. Persist
        try:
            inserted, updated = await self._repo.bulk_upsert(domain_skus)
            result.inserted = inserted
            result.updated = updated
        except Exception as exc:
            msg = f"bulk_upsert failed: {exc}"
            logger.exception(msg)
            result.errors.append(msg)

        logger.info(
            "sync done: raw=%d filtered=%d inserted=%d updated=%d with_specs=%d no_specs=%d",
            result.total_raw,
            result.skipped_filter,
            result.inserted,
            result.updated,
            result.with_specs,
            result.without_specs,
        )
        return result
