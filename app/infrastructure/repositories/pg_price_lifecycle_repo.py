"""Ciclo de vida do preço no Postgres (PLANO_CICLO_DE_VIDA_DO_PRECO)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import cast

from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.repositories.i_price_lifecycle_repo import (
    ChaveDoPreco,
    IPriceLifecycleRepository,
    NovaObservacao,
    PontoDoCiclo,
)
from app.domain.services.ciclo_de_vida_do_preco import (
    Avaliacao,
    Observacao,
    PesoDaFonte,
    RegraDoCiclo,
)
from app.infrastructure.database.models.cloud_price_point import CloudPricePointModel
from app.infrastructure.database.models.price_lifecycle import (
    PriceLifecycleRuleModel,
    PriceObservationModel,
    PriceSourceWeightModel,
)

# Tabela tipada para os UPDATEs em lote (executemany)
_PONTOS = cast(Table, CloudPricePointModel.__table__)


class PgPriceLifecycleRepository(IPriceLifecycleRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def regras(self) -> list[tuple[str, str | None, RegraDoCiclo]]:
        linhas = await self._session.execute(
            select(PriceLifecycleRuleModel).where(PriceLifecycleRuleModel.ativo.is_(True))
        )
        return [
            (
                r.padrao_tipo,
                r.padrao_nome,
                RegraDoCiclo(
                    meia_vida_dias=r.meia_vida_dias,
                    limiar_vivo=r.limiar_vivo,
                    limiar_maduro=r.limiar_maduro,
                    limiar_decomposicao=r.limiar_decomposicao,
                ),
            )
            for r in linhas.scalars()
        ]

    async def pesos(self) -> dict[str, PesoDaFonte]:
        linhas = await self._session.execute(select(PriceSourceWeightModel))
        return {p.fonte: PesoDaFonte(peso=p.peso, confirma=p.confirma) for p in linhas.scalars()}

    async def pontos(self) -> list[PontoDoCiclo]:
        linhas = await self._session.execute(select(CloudPricePointModel))
        return [
            PontoDoCiclo(
                chave=(p.provider, p.sku_key, p.region),
                service_type=p.service_type,
                service_name=p.service_name,
                preco=p.price,
                moeda=p.currency,
                unidade=p.unit,
                confianca=p.confidence,
                capturado_em=p.captured_at,
            )
            for p in linhas.scalars()
        ]

    async def observacoes(self) -> dict[ChaveDoPreco, list[Observacao]]:
        linhas = await self._session.execute(select(PriceObservationModel))
        por_ponto: dict[ChaveDoPreco, list[Observacao]] = defaultdict(list)
        for o in linhas.scalars():
            por_ponto[(o.provider, o.sku_key, o.region)].append(
                Observacao(
                    fonte=o.fonte,
                    preco=o.preco,
                    observado_em=o.observado_em,
                    sob_demanda=o.sob_demanda,
                )
            )
        return dict(por_ponto)

    async def registrar(self, novas: Sequence[NovaObservacao]) -> int:
        if not novas:
            return 0
        self._session.add_all(
            [
                PriceObservationModel(
                    provider=n.chave[0],
                    sku_key=n.chave[1],
                    region=n.chave[2],
                    fonte=n.observacao.fonte,
                    preco=n.observacao.preco,
                    moeda=n.moeda,
                    unidade=n.unidade,
                    sob_demanda=n.observacao.sob_demanda,
                    observado_em=n.observacao.observado_em,
                )
                for n in novas
            ]
        )
        trocas = [n.chave for n in novas if n.troca]
        if trocas:
            tabela = _PONTOS
            await self._session.execute(
                update(tabela)
                .where(
                    tabela.c.provider == bindparam("_provider"),
                    tabela.c.sku_key == bindparam("_sku_key"),
                    tabela.c.region == bindparam("_region"),
                )
                .values(versao=tabela.c.versao + 1),
                [{"_provider": p, "_sku_key": s, "_region": r} for p, s, r in trocas],
            )
        await self._session.flush()
        return len(novas)

    async def atualizar(self, avaliacoes: Sequence[tuple[ChaveDoPreco, Avaliacao]]) -> int:
        if not avaliacoes:
            return 0
        # Core UPDATE com executemany: 18 mil pontos num comando só
        stmt = (
            update(_PONTOS)
            .where(
                _PONTOS.c.provider == bindparam("_provider"),
                _PONTOS.c.sku_key == bindparam("_sku_key"),
                _PONTOS.c.region == bindparam("_region"),
            )
            .values(
                estado=bindparam("_estado"),
                confianca_score=bindparam("_score"),
                confirmado_em=bindparam("_confirmado_em"),
                ultima_fonte=bindparam("_fonte"),
            )
        )
        await self._session.execute(
            stmt,
            [
                {
                    "_provider": chave[0],
                    "_sku_key": chave[1],
                    "_region": chave[2],
                    "_estado": a.estado.value,
                    "_score": a.score,
                    "_confirmado_em": a.confirmado_em,
                    "_fonte": a.ultima_fonte,
                }
                for chave, a in avaliacoes
            ],
        )
        return len(avaliacoes)
