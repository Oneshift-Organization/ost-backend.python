from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.analysis_v2 import AnalysisV2Response
from app.schemas.diagnostico_de_entrada import DiagnosticoDeEntradaOut


class TechnicalMatchItem(BaseModel):
    vcpus: Decimal | None = None
    ram_gb: Decimal | None = None


class FinancialEstimateItem(BaseModel):
    unit_price: Decimal | None = None
    estimated_monthly_cost: Decimal | None = None
    cost_delta: Decimal | None = None
    pricing_confidence: str = "low"


class EquivalentServiceItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    service_name: str
    category: str
    service_type: str
    metadata: dict[str, Any] | None = None
    docs_costs: str | None = None
    technical_match: TechnicalMatchItem | None = None
    financials: FinancialEstimateItem | None = None


class ServiceSummaryItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    service_name: str
    service_type: str = "unknown"
    total_cost: Decimal
    line_count: int = 0
    equivalents: list[EquivalentServiceItem] = Field(default_factory=list)


class ServiceCategoryGroupItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    category: str
    billing_total: Decimal
    services: list[ServiceSummaryItem] = Field(default_factory=list)


class SourceUsageItem(BaseModel):
    sku_id: str
    usage_quantity: Decimal
    billed_cost: Decimal
    currency: str = "USD"


class WorkloadSummaryItem(BaseModel):
    canonical_service: str
    source_usage: SourceUsageItem
    equivalents: list[EquivalentServiceItem] = Field(default_factory=list)


class MigrationContextResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_provider: str
    billing_period: str
    total_spend: Decimal
    currency: str = "USD"
    categories: list[ServiceCategoryGroupItem] = Field(default_factory=list)
    workloads: list[WorkloadSummaryItem] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    report_id: UUID


class AnalysisJobResponse(BaseModel):
    job_id: UUID
    report_id: UUID
    external_id: UUID | None = None
    status: str
    llm_output: str | None = None
    error: str | None = None
    result: AnalysisV2Response | None = None
    llm_input_payload: dict | None = None
    created_at: datetime
    # O que os arquivos enviados permitem; null quando não há problema a dizer
    diagnostico_de_entrada: DiagnosticoDeEntradaOut | None = None


class DispStatusIn(BaseModel):
    """Corpo do `PUT /v1/analyses/{job_id}/disp-status`.

    Um campo só, de propósito: esta rota muda visibilidade e nada mais. Aceitar
    outros campos abriria caminho para o front editar resultado de análise.
    """

    model_config = ConfigDict(extra="forbid")

    disp_status: Literal[0, 1] = Field(
        description="1 = ativa (visível na listagem); 0 = depreciada (some da lista)."
    )


class DispStatusOut(BaseModel):
    job_id: UUID
    disp_status: int
    disp_status_updated_at: datetime | None = None


class AnalysisJobSummary(BaseModel):
    job_id: UUID
    report_id: UUID
    external_id: UUID | None = None
    status: str
    provider: str | None = None
    source_filename: str | None = None
    # Nome do billing dado pelo usuário, e o que a tela mostra (alias ou arquivo)
    alias: str | None = None
    nome_exibicao: str | None = None
    row_count: int = 0
    recommended_provider: str | None = None
    recommendation: str | None = None
    viability_score: int | None = None
    analysis_confidence: str | None = None
    decision_score_status: str | None = None
    decision_score_lower_bound: float | None = None
    decision_score_upper_bound: float | None = None
    decision_score_value: float | None = None
    decision_score_method: str | None = None
    # Só informativo na listagem — a tela de lixeira precisa distinguir. O
    # `GET /v1/analyses` já esconde as depreciadas por default.
    disp_status: int = 1
    error: str | None = None
    created_at: datetime
    # O que os arquivos enviados permitem: `nivel` dá a cor do aviso na lista
    diagnostico_de_entrada: DiagnosticoDeEntradaOut | None = None
    # Uma linha por billing: refazer a análise (anexar inventário, reanalisar) cria
    # uma revisão, e a lista mostra a vigente. Se uma revisão mais nova ainda não
    # concluiu, o estado e o erro dela vêm aqui; a vigente segue valendo.
    reanalise_job_id: UUID | None = None
    reanalise_status: str | None = None
    reanalise_erro: str | None = None
