"""Heavy pipeline: detect → parse → validate → persist items.

Runs in a FastAPI BackgroundTask (no Celery in MVP). The route returns 202 +
job_id; the frontend polls status.
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.domain.repositories.i_billing_item_repo import IBillingItemRepository
from app.domain.repositories.i_billing_report_repo import IBillingReportRepository
from app.domain.value_objects.cloud_provider import CloudProvider
from app.infrastructure.file_ingestion.detector import detect_and_parse
from app.infrastructure.file_ingestion.normalizer import (
    dataframe_to_items,
    deduplicate,
)
from app.infrastructure.file_ingestion.validator import validate_normalized_df
from app.use_cases.analysis.build_billing_enriched import (
    BuildBillingEnrichedInput,
    BuildBillingEnrichedUseCase,
)

_log = get_logger(__name__)


@dataclass(frozen=True)
class IngestBillingFileInput:
    user_id: UUID
    report_id: UUID
    file_path: Path


class IngestBillingFileUseCase:
    def __init__(
        self,
        report_repo: IBillingReportRepository,
        item_repo: IBillingItemRepository,
        enriched_uc: "BuildBillingEnrichedUseCase",
    ) -> None:
        self._reports = report_repo
        self._items = item_repo
        self._enriched_uc = enriched_uc

    async def execute(self, payload: IngestBillingFileInput) -> None:
        report = await self._reports.get_by_id(payload.report_id, payload.user_id)
        if report is None:
            raise IngestionError(f"report {payload.report_id} not found for user")

        report.mark_processing()
        await self._reports.update(report)

        try:
            parser, df = detect_and_parse(payload.file_path, payload.user_id, payload.report_id)
            df = deduplicate(df)
            validate_normalized_df(df)

            items = dataframe_to_items(df, report.id)

            await self._items.delete_by_report(report.id, payload.user_id)
            inserted = await self._items.bulk_insert(items)

            await self._enriched_uc.execute(
                BuildBillingEnrichedInput(user_id=payload.user_id, report_id=report.id)
            )

            report.mark_done(
                row_count=inserted,
                provider=CloudProvider(df.iloc[0]["provider"]),
                schema_version=parser.get_schema_version(),
            )
            await self._reports.update(report)
            _log.info(
                "billing_ingest_done",
                report_id=str(report.id),
                rows=inserted,
                provider=report.provider.value if report.provider else None,
            )
        except Exception as e:
            _log.exception("billing_ingest_failed", report_id=str(report.id))
            report.mark_failed(str(e))
            await self._reports.update(report)
            raise
