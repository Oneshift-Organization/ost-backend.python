from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class BillingMetricResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    total: Decimal


class BillingDashboardResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    cost_by_provider: list[BillingMetricResponse]
    cost_by_service: list[BillingMetricResponse]
    monthly_trend: list[BillingMetricResponse]
    total_cost: Decimal
