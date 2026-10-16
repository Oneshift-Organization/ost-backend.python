"""Pedidos de sync e observações do agente no Postgres (lote v4, V4.1)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.i_acoes_de_preco_repo import IAcoesDePrecoRepository
from app.domain.services.ciclo_de_vida_do_preco import Fonte
from app.domain.services.contestacao import ObservacaoDoAgente, PedidoDeSync
from app.infrastructure.database.models.cloud_price_point import CloudPricePointModel
from app.infrastructure.database.models.price_lifecycle import (
    PedidoDeSyncModel,
    PriceObservationModel,
)


class PgAcoesDePrecoRepository(IAcoesDePrecoRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def registrar_contestacoes(
        self,
        pedidos: Sequence[PedidoDeSync],
        observacoes: Sequence[ObservacaoDoAgente],
        snapshot_id: UUID | None,
    ) -> None:
        for pedido in pedidos:
            if not await self._ha_aberto(pedido):
                self._session.add(
                    PedidoDeSyncModel(
                        provider=pedido.provedor,
                        region=pedido.regiao,
                        service_type=pedido.tipo,
                        sku_key=pedido.sku,
                        motivo=pedido.motivo,
                        snapshot_id=snapshot_id,
                    )
                )
        agora = datetime.now(UTC)
        for obs in observacoes:
            ponto = await self._ponto(*obs.chave_do_preco)
            if ponto is None:
                continue  # sem o ponto de preço, a observação não tem a que se referir
            self._session.add(
                PriceObservationModel(
                    provider=ponto.provider,
                    sku_key=ponto.sku_key,
                    region=ponto.region,
                    fonte=Fonte.AGENTE,
                    preco=obs.preco,
                    moeda=ponto.currency,
                    unidade=ponto.unit,
                    sob_demanda=True,
                    observado_em=agora,
                    detalhe={
                        **obs.detalhe,
                        **({"snapshot_id": str(snapshot_id)} if snapshot_id else {}),
                    },
                )
            )
        await self._session.flush()

    async def _ha_aberto(self, pedido: PedidoDeSync) -> bool:
        stmt = select(func.count()).where(
            PedidoDeSyncModel.provider == pedido.provedor,
            PedidoDeSyncModel.region.is_not_distinct_from(pedido.regiao),
            PedidoDeSyncModel.service_type == pedido.tipo,
            func.lower(PedidoDeSyncModel.sku_key) == pedido.sku.lower(),
            PedidoDeSyncModel.atendido_em.is_(None),
        )
        return bool((await self._session.execute(stmt)).scalar_one())

    async def _ponto(self, provedor: str, sku: str, regiao: str) -> CloudPricePointModel | None:
        stmt = select(CloudPricePointModel).where(
            func.lower(CloudPricePointModel.provider) == provedor.lower(),
            func.lower(CloudPricePointModel.sku_key) == sku.lower(),
            CloudPricePointModel.region == regiao,
        )
        return (await self._session.execute(stmt)).scalars().first()

    async def pedidos_abertos(self) -> list[tuple[UUID, PedidoDeSync]]:
        linhas = await self._session.execute(
            select(PedidoDeSyncModel).where(PedidoDeSyncModel.atendido_em.is_(None))
        )
        return [
            (
                p.id,
                PedidoDeSync(
                    provedor=p.provider,
                    regiao=p.region,
                    sku=p.sku_key,
                    tipo=p.service_type,
                    motivo=p.motivo,
                ),
            )
            for p in linhas.scalars()
        ]

    async def marcar_atendidos(self, ids: Sequence[UUID]) -> int:
        if not ids:
            return 0
        resultado = await self._session.execute(
            update(PedidoDeSyncModel)
            .where(PedidoDeSyncModel.id.in_(list(ids)))
            .values(atendido_em=datetime.now(UTC))
        )
        return int(getattr(resultado, "rowcount", 0) or 0)
