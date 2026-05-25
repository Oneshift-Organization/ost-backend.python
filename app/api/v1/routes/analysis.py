from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import NotFoundError
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.schemas.analysis import (
    AnalysisRequest,
    EquivalentServiceItem,
    MigrationContextResponse,
    ServiceCategoryGroupItem,
    ServiceSummaryItem,
)
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)

router = APIRouter(prefix="/analyses")


@router.post("/migration-context", status_code=status.HTTP_200_OK)
async def build_migration_context(
    user: CurrentUser,
    session: DbSession,
    body: AnalysisRequest,
) -> MigrationContextResponse:
    uc = BuildMigrationContextUseCase(
        PgBillingReportRepository(session),
        PgBillingItemRepository(session),
        PGServiceMetadataRepository(session),
        PGServiceComparisonRepository(session),
    )
    try:
        ctx = await uc.execute(
            BuildMigrationContextInput(user_id=UUID(user.sub), report_id=body.report_id)
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    return MigrationContextResponse(
        source_provider=ctx.source_provider,
        billing_period=ctx.billing_period,
        total_spend=ctx.total_spend,
        currency=ctx.currency,
        categories=[
            ServiceCategoryGroupItem(
                category=c.category,
                billing_total=c.billing_total,
                services=[
                    ServiceSummaryItem(
                        service_name=s.service_name,
                        service_type=s.service_type,
                        total_cost=s.total_cost,
                        line_count=s.line_count,
                        equivalents=[
                            EquivalentServiceItem(
                                provider=e.provider,
                                service_name=e.service_name,
                                category=e.category,
                                service_type=e.service_type,
                                metadata=e.metadata,
                                docs_costs=e.docs_costs,
                            )
                            for e in s.equivalents
                        ],
                    )
                    for s in c.services
                ],
            )
            for c in ctx.categories
        ],
    )
