from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.value_objects.cloud_provider import CloudProvider
from app.infrastructure.database.models.billing_reports import BillingReportModel


class PgBillingReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, report: BillingReport) -> BillingReport:
        model = BillingReportModel(
            id=report.id,
            user_id=report.user_id,
            provider=report.provider.value if report.provider else None,
            schema_version=report.schema_version,
            source_filename=report.source_filename,
            status=report.status.value,
            row_count=report.row_count,
            error_message=report.error_message,
            external_id=report.external_id,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_id(self, report_id: UUID, user_id: UUID) -> BillingReport | None:
        stmt = select(BillingReportModel).where(
            BillingReportModel.id == report_id,
            BillingReportModel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def get_by_external_id(self, external_id: UUID, user_id: UUID) -> BillingReport | None:
        stmt = select(BillingReportModel).where(
            BillingReportModel.external_id == external_id,
            BillingReportModel.user_id == user_id,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        return self._to_entity(model) if model else None

    async def update(self, report: BillingReport) -> BillingReport:
        stmt = select(BillingReportModel).where(BillingReportModel.id == report.id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise ValueError(f"BillingReport {report.id} not found")
        model.status = report.status.value
        model.row_count = report.row_count
        model.error_message = report.error_message
        if report.provider is not None:
            model.provider = report.provider.value
        if report.schema_version is not None:
            model.schema_version = report.schema_version
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def rename(self, report_id: UUID, user_id: UUID, alias: str | None) -> None:
        await self._session.execute(
            update(BillingReportModel)
            .where(BillingReportModel.id == report_id, BillingReportModel.user_id == user_id)
            .values(alias=alias)
        )
        await self._session.flush()

    async def list_by_user(self, user_id: UUID, limit: int = 50) -> list[BillingReport]:
        stmt = (
            select(BillingReportModel)
            .where(BillingReportModel.user_id == user_id)
            .order_by(BillingReportModel.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [self._to_entity(m) for m in result.scalars().all()]

    @staticmethod
    def _to_entity(model: BillingReportModel) -> BillingReport:
        return BillingReport(
            id=model.id,
            user_id=model.user_id,
            provider=CloudProvider(model.provider) if model.provider else None,
            schema_version=model.schema_version,
            source_filename=model.source_filename,
            alias=model.alias,
            status=JobStatus(model.status),
            row_count=model.row_count,
            error_message=model.error_message,
            external_id=model.external_id,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
