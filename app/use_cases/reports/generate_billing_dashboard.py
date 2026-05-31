from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.repositories.i_billing_dashboard_repo import IBillingDashboardRepository


@dataclass(frozen=True)
class GenerateBillingDashboardInput:
    user_id: UUID
    from_period: str | None = None
    to_period: str | None = None
    top_services_limit: int = 10


@dataclass(frozen=True)
class BillingMetric:
    label: str
    total: Decimal


@dataclass(frozen=True)
class BillingDashboardOutput:
    cost_by_provider: list[BillingMetric]
    cost_by_service: list[BillingMetric]
    monthly_trend: list[BillingMetric]
    total_cost: Decimal


class GenerateBillingDashboardUseCase:
    """Aggregate normalized billing items for dashboard consumption."""

    def __init__(self, repo: IBillingDashboardRepository) -> None:
        self._repo = repo

    async def execute(self, payload: GenerateBillingDashboardInput) -> BillingDashboardOutput:
        by_provider = await self._repo.sum_by_provider(
            payload.user_id, payload.from_period, payload.to_period
        )
        by_service = await self._repo.sum_by_service(
            payload.user_id,
            payload.from_period,
            payload.to_period,
            limit=payload.top_services_limit,
        )
        by_period = await self._repo.sum_by_period(
            payload.user_id, payload.from_period, payload.to_period
        )

        cost_by_provider = [_metric(label, total) for label, total in by_provider]
        cost_by_service = [_metric(label, total) for label, total in by_service]
        monthly_trend = [_metric(label, total) for label, total in by_period]

        total_cost = sum((m.total for m in cost_by_provider), Decimal("0"))

        return BillingDashboardOutput(
            cost_by_provider=cost_by_provider,
            cost_by_service=cost_by_service,
            monthly_trend=monthly_trend,
            total_cost=total_cost,
        )


def _metric(label: str, total: Decimal) -> BillingMetric:
    return BillingMetric(label=label, total=total)
