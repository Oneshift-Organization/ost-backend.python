"""Pedidos de sync e observação do agente gravados (lote v4, V4.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.services.contestacao import ObservacaoDoAgente, PedidoDeSync
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_acoes_de_preco_repo import PgAcoesDePrecoRepository
from app.infrastructure.repositories.pg_cloud_price_point_repo import PgCloudPricePointRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

_PROVEDOR = "zzv41"


@pytest_asyncio.fixture(loop_scope="session")
async def sessao(_db_ready):
    sm = get_sessionmaker()
    async with sm() as s:
        yield s
        for tabela in ("pedidos_de_sync", "price_observations", "cloud_price_points"):
            await s.execute(
                text(f"delete from {tabela} where provider = :p"),
                {"p": _PROVEDOR},
            )
        await s.commit()


def _pedido(sku: str = "n2-standard-8") -> PedidoDeSync:
    return PedidoDeSync(_PROVEDOR, "us-central1", sku, "virtual_server", "teste")


async def test_pedido_aberto_igual_nao_repete_e_fecha_quando_atendido(sessao) -> None:
    repo = PgAcoesDePrecoRepository(sessao)
    await repo.registrar_contestacoes([_pedido()], [], None)
    await repo.registrar_contestacoes([_pedido(), _pedido("e2-standard-4")], [], None)
    abertos = [(i, p) for i, p in await repo.pedidos_abertos() if p.provedor == _PROVEDOR]
    assert sorted(p.sku for _, p in abertos) == ["e2-standard-4", "n2-standard-8"]
    assert await repo.marcar_atendidos([i for i, _ in abertos]) == 2
    assert [p for _, p in await repo.pedidos_abertos() if p.provedor == _PROVEDOR] == []


async def test_observacao_do_agente_so_com_o_ponto_de_preco(sessao) -> None:
    await PgCloudPricePointRepository(sessao).bulk_upsert(
        [
            CloudPricePoint(
                provider=_PROVEDOR,
                sku_key="n2-standard-8",
                service_name="n2-standard-8",
                service_type="virtual_server",
                region="us-central1",
                price=Decimal("0.39"),
                currency="USD",
                captured_at=datetime.now(UTC),
            )
        ]
    )
    repo = PgAcoesDePrecoRepository(sessao)
    observacoes = [
        ObservacaoDoAgente((_PROVEDOR, "N2-STANDARD-8", "us-central1"), Decimal("0.42"), {}),
        ObservacaoDoAgente((_PROVEDOR, "nao-existe", "us-central1"), Decimal("0.10"), {}),
    ]
    await repo.registrar_contestacoes([], observacoes, None)
    gravadas = (
        await sessao.execute(
            text(
                "select sku_key, fonte, preco, unidade from price_observations where provider = :p"
            ),
            {"p": _PROVEDOR},
        )
    ).all()
    assert [tuple(g) for g in gravadas] == [
        ("n2-standard-8", "agente", Decimal("0.42000000"), "instance_hour")
    ]
