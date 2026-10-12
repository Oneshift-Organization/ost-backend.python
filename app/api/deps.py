from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.logging import get_logger
from app.core.security import verify_jwt
from app.domain.analysis import MigrationContext
from app.infrastructure.database.session import get_db_session
from app.infrastructure.repositories.pg_agent_snapshot_repo import (
    PgAgentSnapshotRepository,
)
from app.infrastructure.repositories.pg_billing_dashboard_repo import (
    PgBillingDashboardRepository,
)
from app.infrastructure.repositories.pg_billing_enriched_repo import (
    PgBillingEnrichedRepository,
)
from app.infrastructure.repositories.pg_billing_item_repo import PgBillingItemRepository
from app.infrastructure.repositories.pg_billing_report_repo import (
    PgBillingReportRepository,
)
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.infrastructure.repositories.pg_cloud_sku_repo import PgCloudSkuRepository
from app.infrastructure.repositories.pg_exchange_rate_repo import (
    PgExchangeRateRepository,
)
from app.infrastructure.repositories.pg_inventory_repo import PgInventoryRepository
from app.infrastructure.repositories.pg_sku_classification_rule_repo import (
    PgSkuClassificationRuleRepository,
)
from app.infrastructure.repositories.pg_unmapped_billing_repo import (
    PgUnmappedBillingRepository,
)
from app.infrastructure.repositories.service_repositories import (
    PGServiceComparisonRepository,
    PGServiceMetadataRepository,
)
from app.schemas.auth import UserClaims
from app.use_cases.agent.preparar_snapshot import PrepararSnapshotUseCase
from app.use_cases.analysis.build_billing_enriched import BuildBillingEnrichedUseCase
from app.use_cases.analysis.build_migration_context import (
    BuildMigrationContextInput,
    BuildMigrationContextUseCase,
)
from app.use_cases.analysis.payload_do_agente import (
    ConstrutorDeContexto,
    MontarPayloadDoAgenteUseCase,
)
from app.use_cases.analysis.topologia_no_payload import MontarTopologiaUseCase
from app.use_cases.billing.get_billing_job_status import GetBillingJobStatusUseCase
from app.use_cases.billing.ingest_billing_file import IngestBillingFileUseCase
from app.use_cases.billing.list_billing_items import ListBillingItemsUseCase
from app.use_cases.billing.list_billing_reports import ListBillingReportsUseCase
from app.use_cases.billing.upload_billing_file import UploadBillingFileUseCase
from app.use_cases.reports.generate_billing_dashboard import (
    GenerateBillingDashboardUseCase,
)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]

_log = get_logger(__name__)

# Fake user used when AUTH_ENABLED=false (dev only). Stable UUID so repeated
# uploads can be correlated to the same "user" in the DB.
_DEV_USER = UserClaims(
    sub="00000000-0000-0000-0000-000000000001",
    email="dev@local",
    role="authenticated",
)


# Declara o esquema para o OpenAPI — é o que faz o botão **Authorize** aparecer
# no Swagger. Sem isto, `get_current_user` lia o header direto de `Request` e o
# FastAPI não tinha como documentar nada: a tela não oferecia onde colar o token
# e toda chamada voltava 401, sem indicação do que fazer.
#
# `auto_error=False` de propósito: quem decide o que é 401 continua sendo a
# função abaixo, que também trata o bypass de dev. Com `True`, o HTTPBearer
# levantaria antes e o bypass pararia de funcionar.
_bearer = HTTPBearer(
    auto_error=False,
    description="JWT do Supabase. Cole apenas o token, sem o prefixo 'Bearer '.",
)


def get_current_user(
    request: Request,
    _credencial: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> UserClaims:
    """Reads `Authorization: Bearer <jwt>` and verifies via Supabase JWKS.

    Dev bypass: when `settings.auth_enabled` is False (only allowed outside
    production), returns a fixed `_DEV_USER` regardless of the Authorization
    header. Logged on every request so the bypass is never silent.

    O header continua sendo lido de `request` e não de `_credencial`: o parsing
    já existia, está coberto por teste, e trocá-lo agora mudaria o contrato de
    erro por causa de uma melhoria de documentação.
    """
    settings = get_settings()
    if not settings.auth_enabled:
        if settings.is_production:
            # Defensive: refuse to disable auth in prod even if env says so.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="auth cannot be disabled in production",
            )
        _log.warning("auth_bypassed", env=settings.app_env, dev_user=_DEV_USER.sub)
        return _DEV_USER

    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header[len("Bearer ") :].strip()
    try:
        return verify_jwt(token)
    except UnauthorizedError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        ) from e


CurrentUser = Annotated[UserClaims, Depends(get_current_user)]


def _upload_billing_file_uc(session: DbSession) -> UploadBillingFileUseCase:
    return UploadBillingFileUseCase(PgBillingReportRepository(session))


def _ingest_billing_file_uc(session: DbSession) -> IngestBillingFileUseCase:
    return IngestBillingFileUseCase(
        PgBillingReportRepository(session),
        PgBillingItemRepository(session),
        BuildBillingEnrichedUseCase(
            PgBillingReportRepository(session),
            PgBillingItemRepository(session),
            PgBillingEnrichedRepository(session),
            PgCloudSkuRepository(session),
            PgSkuClassificationRuleRepository(session),
            PgUnmappedBillingRepository(session),
        ),
    )


def _get_billing_job_status_uc(session: DbSession) -> GetBillingJobStatusUseCase:
    return GetBillingJobStatusUseCase(PgBillingReportRepository(session))


def _list_billing_reports_uc(session: DbSession) -> ListBillingReportsUseCase:
    return ListBillingReportsUseCase(PgBillingReportRepository(session))


def _list_billing_items_uc(session: DbSession) -> ListBillingItemsUseCase:
    return ListBillingItemsUseCase(
        PgBillingReportRepository(session),
        PgBillingItemRepository(session),
    )


def _generate_billing_dashboard_uc(session: DbSession) -> GenerateBillingDashboardUseCase:
    return GenerateBillingDashboardUseCase(PgBillingDashboardRepository(session))


UploadBillingFileUC = Annotated[UploadBillingFileUseCase, Depends(_upload_billing_file_uc)]
IngestBillingFileUC = Annotated[IngestBillingFileUseCase, Depends(_ingest_billing_file_uc)]
GetBillingJobStatusUC = Annotated[GetBillingJobStatusUseCase, Depends(_get_billing_job_status_uc)]
ListBillingReportsUC = Annotated[ListBillingReportsUseCase, Depends(_list_billing_reports_uc)]
ListBillingItemsUC = Annotated[ListBillingItemsUseCase, Depends(_list_billing_items_uc)]
GenerateBillingDashboardUC = Annotated[
    GenerateBillingDashboardUseCase,
    Depends(_generate_billing_dashboard_uc),
]


def construtor_de_contexto(session: AsyncSession) -> ConstrutorDeContexto:
    """O contexto de migração de um relatório, com os repositórios desta sessão."""
    uc = BuildMigrationContextUseCase(
        PgBillingReportRepository(session),
        PgBillingEnrichedRepository(session),
        PGServiceMetadataRepository(session),
        PGServiceComparisonRepository(session),
        sku_repo=PgCloudSkuRepository(session),
        fx_repo=PgExchangeRateRepository(session),
        price_repo=PgCloudPricePointRepository(session),
    )

    async def construir(user_id: UUID, report_id: UUID) -> MigrationContext:
        return await uc.execute(BuildMigrationContextInput(user_id=user_id, report_id=report_id))

    return construir


def payload_do_agente_uc(session: AsyncSession) -> MontarPayloadDoAgenteUseCase:
    """O mesmo montador para o `/request` e para a preparação de snapshot."""
    return MontarPayloadDoAgenteUseCase(
        relatorios=PgBillingReportRepository(session),
        inventarios=PgInventoryRepository(session),
        contexto=construtor_de_contexto(session),
        topologia=MontarTopologiaUseCase(
            PgInventoryRepository(session), PgBillingItemRepository(session)
        ),
        inventario_habilitado=get_settings().inventory_enabled,
    )


def preparar_snapshot_uc(session: AsyncSession) -> PrepararSnapshotUseCase:
    return PrepararSnapshotUseCase(
        montador=payload_do_agente_uc(session),
        snapshots=PgAgentSnapshotRepository(session),
        inventarios=PgInventoryRepository(session),
        validade=timedelta(hours=get_settings().agent_snapshot_ttl_hours),
    )
