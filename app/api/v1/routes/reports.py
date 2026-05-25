import re
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, GenerateBillingDashboardUC
from app.core.logging import get_logger
from app.schemas.reports import BillingDashboardResponse, BillingMetricResponse
from app.use_cases.reports.generate_billing_dashboard import (
    GenerateBillingDashboardInput,
)

router = APIRouter(prefix="/reports")
_log = get_logger(__name__)

_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")
_MAX_TOP_SERVICES = 100


@router.get("/billing-dashboard", response_model=BillingDashboardResponse)
async def billing_dashboard(
    user: CurrentUser,
    uc: GenerateBillingDashboardUC,
    from_period: str | None = Query(None, description="Start period YYYY-MM"),
    to_period: str | None = Query(None, description="End period YYYY-MM"),
    top_services_limit: int = Query(10, ge=1, le=_MAX_TOP_SERVICES),
) -> BillingDashboardResponse:
    user_uuid = _resolve_user_uuid(user.sub)

    if from_period is not None and not _PERIOD_RE.match(from_period):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "from_period must be YYYY-MM",
        )
    if to_period is not None and not _PERIOD_RE.match(to_period):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "to_period must be YYYY-MM",
        )

    result = await uc.execute(
        GenerateBillingDashboardInput(
            user_id=user_uuid,
            from_period=from_period,
            to_period=to_period,
            top_services_limit=top_services_limit,
        )
    )

    return BillingDashboardResponse(
        cost_by_provider=[_to_metric(m) for m in result.cost_by_provider],
        cost_by_service=[_to_metric(m) for m in result.cost_by_service],
        monthly_trend=[_to_metric(m) for m in result.monthly_trend],
        total_cost=result.total_cost,
    )


def _resolve_user_uuid(sub: str) -> UUID:
    try:
        return UUID(sub)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user id in token") from e


def _to_metric(m) -> BillingMetricResponse:
    return BillingMetricResponse(label=m.label, total=m.total)
