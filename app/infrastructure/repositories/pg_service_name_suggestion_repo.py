"""Fila de curadoria de nomes de serviço (decisão 2.11): leitura das faturas e decisões."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.i_service_name_suggestion_repo import ServicoNasFaturas
from app.infrastructure.database.models.billing_enriched import BillingEnrichedModel
from app.infrastructure.database.models.billing_reports import BillingReportModel
from app.infrastructure.database.models.service_name_suggestion import (
    ServiceNameSuggestionModel,
)


class PgServiceNameSuggestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def servicos_das_faturas(self, usuarios_excluidos: list[UUID]) -> list[ServicoNasFaturas]:
        """Nome de serviço por provedor, com quantas faturas e quantos clientes o trazem.

        Fora as faturas de teste: arquivo com "mock" no nome e os usuários excluídos."""
        filtro = [func.coalesce(BillingReportModel.source_filename, "").not_ilike("%mock%")]
        if usuarios_excluidos:
            filtro.append(BillingReportModel.user_id.not_in(usuarios_excluidos))
        stmt = (
            select(
                func.lower(BillingEnrichedModel.provider),
                BillingEnrichedModel.service_name,
                func.count(func.distinct(BillingEnrichedModel.report_id)),
                func.count(func.distinct(BillingReportModel.user_id)),
            )
            .join(BillingReportModel, BillingReportModel.id == BillingEnrichedModel.report_id)
            .where(*filtro, BillingEnrichedModel.service_name.is_not(None))
            .group_by(func.lower(BillingEnrichedModel.provider), BillingEnrichedModel.service_name)
        )
        return [
            ServicoNasFaturas(provider, nome, int(faturas), int(clientes))
            for provider, nome, faturas, clientes in (await self._session.execute(stmt)).all()
        ]

    async def registrar(
        self,
        servico: ServicoNasFaturas,
        chave: str,
        alvo_sugerido: str | None,
        semelhanca: float | None,
    ) -> None:
        """Cria ou atualiza a contagem. A sugestão só muda enquanto está pendente."""
        tabela = ServiceNameSuggestionModel.__table__
        stmt = pg_insert(ServiceNameSuggestionModel).values(
            provider=servico.provider,
            nome_na_fatura=servico.nome,
            chave=chave,
            alvo_sugerido=alvo_sugerido,
            semelhanca=semelhanca,
            faturas=servico.faturas,
            clientes=servico.clientes,
            status="pendente",
        )
        pendente = tabela.c.status == "pendente"
        stmt = stmt.on_conflict_do_update(
            constraint="uq_service_name_suggestions",
            set_={
                "nome_na_fatura": stmt.excluded.nome_na_fatura,
                "faturas": stmt.excluded.faturas,
                "clientes": stmt.excluded.clientes,
                "visto_em": func.now(),
                "alvo_sugerido": case(
                    (pendente, stmt.excluded.alvo_sugerido), else_=tabela.c.alvo_sugerido
                ),
                "semelhanca": case(
                    (pendente, stmt.excluded.semelhanca), else_=tabela.c.semelhanca
                ),
            },
        )
        await self._session.execute(stmt)

    async def listar(
        self, status: str | None = None, provider: str | None = None
    ) -> list[ServiceNameSuggestionModel]:
        stmt = select(ServiceNameSuggestionModel)
        if status:
            stmt = stmt.where(ServiceNameSuggestionModel.status == status)
        if provider:
            stmt = stmt.where(ServiceNameSuggestionModel.provider == provider.lower())
        stmt = stmt.order_by(
            ServiceNameSuggestionModel.clientes.desc(),
            ServiceNameSuggestionModel.faturas.desc(),
            ServiceNameSuggestionModel.nome_na_fatura,
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def obter(self, sugestao_id: UUID) -> ServiceNameSuggestionModel | None:
        return await self._session.get(ServiceNameSuggestionModel, sugestao_id)

    async def decidir(
        self,
        sugestao: ServiceNameSuggestionModel,
        *,
        status: str,
        alvo: str | None,
        por: str,
        observacao: str | None,
    ) -> ServiceNameSuggestionModel:
        sugestao.status = status
        sugestao.alvo = alvo
        sugestao.decidido_por = por
        sugestao.decidido_em = datetime.now(UTC)
        sugestao.observacao = observacao
        await self._session.flush()
        return sugestao
