"""Aggregator so Alembic autogenerate sees every model."""

from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.billing_enriched import BillingEnrichedModel
from app.infrastructure.database.models.billing_items import BillingItemModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.models.cloud_services_comparation import (
    CloudServiceComparisonModel,
)
from app.infrastructure.database.models.cloud_services_metadata import (
    CloudServiceMetadataModel,
)
from app.infrastructure.database.models.cloud_sku_catalog import CloudSkuCatalogModel
from app.infrastructure.database.models.service_ontology import ServiceOntologyModel
from app.infrastructure.database.models.sku_classification_rules import (
    SkuClassificationRuleModel,
)
from app.infrastructure.database.models.unmapped_billing_lines import UnmappedBillingLineModel

__all__ = [
    "AnalysisJobModel",
    "BillingEnrichedModel",
    "BillingItemModel",
    "BillingReportModel",
    "CloudServiceComparisonModel",
    "CloudServiceMetadataModel",
    "CloudSkuCatalogModel",
    "ServiceOntologyModel",
    "SkuClassificationRuleModel",
    "UnmappedBillingLineModel",
]
