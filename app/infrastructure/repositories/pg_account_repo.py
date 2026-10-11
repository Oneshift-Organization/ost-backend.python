from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.i_account_repo import IAccountRepository
from app.infrastructure.database.models.analysis_job import AnalysisJobModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.models.inventory import InventoryModel
from app.infrastructure.database.models.terraform_generation_job import (
    TerraformGenerationJobModel,
)

# Só as tabelas-raiz. Itens, enriquecimento e linhas não mapeadas caem com
# `billing_reports`; recursos e relações caem com `inventories` (FK ON DELETE
# CASCADE). Tabela nova com `user_id` e sem pai precisa entrar aqui.
_ROOT_MODELS = (
    TerraformGenerationJobModel,
    AnalysisJobModel,
    InventoryModel,
    BillingReportModel,
)


class PgAccountRepository(IAccountRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def delete_user_data(self, user_id: UUID) -> None:
        for model in _ROOT_MODELS:
            await self._s.execute(delete(model).where(model.user_id == user_id))
