from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities.billing_item import BillingItem
from app.domain.entities.billing_report import BillingReport, JobStatus
from app.domain.value_objects.cloud_provider import CloudProvider


class BillingFileUploadResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID = Field(..., description="Use this id to poll job status.")
    # Mesmo valor de `job_id`. Este id é o `BillingReport.id`, que
    # `GET /v1/analyses/{report_id}/request` espera como `report_id` — mesmo
    # identificador, dois nomes, por herança do schema.
    #
    # Quem integra lê `job_id`, procura onde passá-lo, e o outro endpoint pede
    # `report_id`. Se tentar o `job_id` de uma *análise* (que existe e é outro
    # id), leva 404 — e nosso 404 é ambíguo de propósito, então o cliente conclui
    # "não existe" em vez de "chamei errado".
    #
    # Renomear seria a correção limpa e quebraria o frontend hoje. Duplicar não
    # quebra ninguém: quem lê `job_id` continua igual, quem não conhece
    # `report_id` ignora. O preço é um campo redundante — mais barato que cada
    # integração nova tropeçar aqui.
    report_id: UUID
    external_id: UUID | None = None
    status: JobStatus
    source_filename: str
    created_at: datetime


class BillingJobStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: UUID
    # Mesmo valor de `job_id`. Este id é o `BillingReport.id`, que
    # `GET /v1/analyses/{report_id}/request` espera como `report_id` — mesmo
    # identificador, dois nomes, por herança do schema.
    #
    # Quem integra lê `job_id`, procura onde passá-lo, e o outro endpoint pede
    # `report_id`. Se tentar o `job_id` de uma *análise* (que existe e é outro
    # id), leva 404 — e nosso 404 é ambíguo de propósito, então o cliente conclui
    # "não existe" em vez de "chamei errado".
    #
    # Renomear seria a correção limpa e quebraria o frontend hoje. Duplicar não
    # quebra ninguém: quem lê `job_id` continua igual, quem não conhece
    # `report_id` ignora. O preço é um campo redundante — mais barato que cada
    # integração nova tropeçar aqui.
    report_id: UUID
    external_id: UUID | None = None
    status: JobStatus
    provider: CloudProvider | None = None
    schema_version: str | None = None
    row_count: int = 0
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class BillingReportSummary(BaseModel):
    """Lightweight view used in list endpoints — no items embedded."""

    model_config = ConfigDict(frozen=True)

    job_id: UUID
    # Mesmo valor de `job_id`. Este id é o `BillingReport.id`, que
    # `GET /v1/analyses/{report_id}/request` espera como `report_id` — mesmo
    # identificador, dois nomes, por herança do schema.
    #
    # Quem integra lê `job_id`, procura onde passá-lo, e o outro endpoint pede
    # `report_id`. Se tentar o `job_id` de uma *análise* (que existe e é outro
    # id), leva 404 — e nosso 404 é ambíguo de propósito, então o cliente conclui
    # "não existe" em vez de "chamei errado".
    #
    # Renomear seria a correção limpa e quebraria o frontend hoje. Duplicar não
    # quebra ninguém: quem lê `job_id` continua igual, quem não conhece
    # `report_id` ignora. O preço é um campo redundante — mais barato que cada
    # integração nova tropeçar aqui.
    report_id: UUID
    external_id: UUID | None = None
    status: JobStatus
    provider: CloudProvider | None = None
    schema_version: str | None = None
    source_filename: str | None = None
    row_count: int = 0
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, r: BillingReport) -> "BillingReportSummary":
        return cls(
            job_id=r.id,
            report_id=r.id,
            external_id=r.external_id,
            status=r.status,
            provider=r.provider,
            schema_version=r.schema_version,
            source_filename=r.source_filename,
            row_count=r.row_count,
            error_message=r.error_message,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )


class BillingItemResponse(BaseModel):
    """One normalized billing line item — what the parser produced."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    report_id: UUID
    provider: CloudProvider
    account_id: str | None = None
    service: str
    resource_id: str | None = None
    region: str | None = None
    cost: Decimal
    cost_discounted: Decimal | None = None
    currency: str
    usage_amount: Decimal | None = None
    usage_unit: str | None = None
    usage_start: datetime | None = None
    usage_end: datetime | None = None
    billing_period: str
    tags: dict[str, str] = Field(default_factory=dict)
    is_correction: bool = False
    is_estimate: bool = False
    line_hash: str

    @classmethod
    def from_entity(cls, i: BillingItem) -> "BillingItemResponse":
        return cls(
            id=i.id,
            report_id=i.report_id,
            provider=i.provider,
            account_id=i.account_id,
            service=i.service,
            resource_id=i.resource_id,
            region=i.region,
            cost=i.cost,
            cost_discounted=i.cost_discounted,
            currency=i.currency,
            usage_amount=i.usage_amount,
            usage_unit=i.usage_unit,
            usage_start=i.usage_start,
            usage_end=i.usage_end,
            billing_period=i.billing_period,
            tags=_filter_tags(i.tags),
            is_correction=i.is_correction,
            is_estimate=i.is_estimate,
            line_hash=i.line_hash,
        )


def _filter_tags(tags: dict) -> dict[str, str]:
    if not isinstance(tags, dict):
        return {}
    return {k: v for k, v in tags.items() if isinstance(v, str) and k != "raw_payload"}


class BillingItemsPage(BaseModel):
    """Paginated envelope for /v1/billing-files/{id}/items."""

    model_config = ConfigDict(frozen=True)

    report_id: UUID
    total: int
    limit: int
    offset: int
    items: list[BillingItemResponse]
