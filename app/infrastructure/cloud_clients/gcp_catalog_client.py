from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from google.cloud import billing_v1
from google.oauth2.service_account import Credentials

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Hardcoded GCP service IDs (stable across accounts)
COMPUTE_ENGINE_SERVICE_ID = "6F81-5844-456A"
CLOUD_SQL_SERVICE_ID = "9662-B51E-5089"


@dataclass(frozen=True)
class GcpSkuEntry:
    sku_id: str
    service_name: str
    description: str
    category_resource_group: str
    service_regions: list[str]
    pricing_unit: str | None
    tier_price: Decimal | None
    currency: str
    raw: dict[str, Any]


class GcpCatalogClient:
    """Wrapper around google-cloud-billing CloudCatalogClient.

    Uses GOOGLE_APPLICATION_CREDENTIALS from settings to authenticate.
    Fetches SKUs only for Compute Engine and Cloud SQL.
    """

    def __init__(self, service_account_path: str | None = None) -> None:
        settings = get_settings()
        path = service_account_path or settings.gcp_service_account_path
        if path:
            credentials = Credentials.from_service_account_file(path)
            self._client = billing_v1.CloudCatalogClient(credentials=credentials)
        else:
            logger.warning("gcp_service_account_path not set; falling back to ADC")
            self._client = billing_v1.CloudCatalogClient()

    def list_skus(self, service_name: str) -> list[GcpSkuEntry]:
        """List all SKUs for a given GCP service name.

        Args:
            service_name: e.g. "services/6F81-5844-456A" for Compute Engine.
        """
        entries: list[GcpSkuEntry] = []
        request = billing_v1.ListSkusRequest(parent=service_name)
        page = self._client.list_skus(request=request)

        for sku in page:
            raw = billing_v1.types.Sku.to_dict(sku)
            pricing_info = sku.pricing_info or []
            tier_price: Decimal | None = None
            pricing_unit: str | None = None
            currency = "USD"

            if pricing_info:
                pi = pricing_info[0]
                pricing_unit = str(pi.pricing_expression.display_quantity) if pi.pricing_expression.display_quantity is not None else None
                if pi.pricing_expression.tiered_rates:
                    rate = pi.pricing_expression.tiered_rates[0]
                    if rate.unit_price:
                        currency = rate.unit_price.currency_code or "USD"
                        tier_price = Decimal(str(rate.unit_price.units)) + Decimal(
                            str(rate.unit_price.nanos)
                        ) / Decimal("1_000_000_000")
                    elif rate.list_price:
                        currency = rate.list_price.currency_code or "USD"
                        tier_price = Decimal(str(rate.list_price.units)) + Decimal(
                            str(rate.list_price.nanos)
                        ) / Decimal("1_000_000_000")

            category = sku.category
            resource_group = category.resource_group if category else ""
            service_name = category.service_display_name if category else ""

            entries.append(
                GcpSkuEntry(
                    sku_id=sku.sku_id,
                    service_name=service_name,
                    description=sku.description or "",
                    category_resource_group=resource_group,
                    service_regions=list(sku.service_regions or []),
                    pricing_unit=pricing_unit,
                    tier_price=tier_price,
                    currency=currency,
                    raw=raw,
                )
            )

        return entries

    def list_compute_and_sql_skus(self) -> list[GcpSkuEntry]:
        """Convenience: fetch SKUs for Compute Engine + Cloud SQL."""
        all_skus: list[GcpSkuEntry] = []
        for sid in (COMPUTE_ENGINE_SERVICE_ID, CLOUD_SQL_SERVICE_ID):
            parent = f"services/{sid}"
            logger.info("fetching SKUs from %s", parent)
            skus = self.list_skus(parent)
            logger.info("  got %d SKUs from %s", len(skus), parent)
            all_skus.extend(skus)
        return all_skus
