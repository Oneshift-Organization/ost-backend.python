"""Aggregator so Alembic autogenerate sees every model."""

from app.infrastructure.database.models.billing_items import BillingItemModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.models.cloud_services_comparation import (
    CloudServiceComparisonModel,
)
from app.infrastructure.database.models.cloud_services_metadata import (
    CloudServiceMetadataModel,
)

__all__ = [
    "BillingItemModel",
    "BillingReportModel",
    "CloudServiceComparisonModel",
    "CloudServiceMetadataModel",
]
