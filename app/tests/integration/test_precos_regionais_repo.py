"""Repositório de preços: regiões disponíveis e substituição da coleta (R3).

A coleta antiga do GCP gerava `e2-standard-64`, que não existe. Upsert sozinho
nunca apaga, então o nome errado continuaria cotando depois da correção. A
substituição remove, dentro do mesmo conjunto (provedor, região, tipo, fonte),
o que a coleta nova não trouxe — e só ali.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.repositories.pg_cloud_price_point_repo import PgCloudPricePointRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

_PROVEDOR = "zzteste"


def _ponto(
    sku: str, regiao: str, *, fonte: str = "api", tipo: str = "virtual_server"
) -> CloudPricePoint:
    return CloudPricePoint(
        provider=_PROVEDOR,
        sku_key=sku,
        service_name="teste",
        service_type=tipo,
        region=regiao,
        price=Decimal("0.1"),
        currency="USD",
        source=fonte,
        captured_at=datetime.now(UTC),
    )


@pytest_asyncio.fixture(loop_scope="session")
async def repo(_db_ready):
    sm = get_sessionmaker()
    async with sm() as s:
        yield PgCloudPricePointRepository(s), s
        await s.execute(
            text("delete from cloud_price_points where provider = :p"), {"p": _PROVEDOR}
        )
        await s.commit()


async def test_regioes_com_preco(repo) -> None:
    r, s = repo
    await r.substituir([_ponto("a", "sa-east-1"), _ponto("b", "us-east-1")])
    await r.substituir([_ponto("x", "eu-west-1", tipo="storage_ssd")])
    await s.flush()
    assert await r.regioes_com_preco(_PROVEDOR, "virtual_server") == ["sa-east-1", "us-east-1"]
    assert await r.regioes_com_preco(_PROVEDOR.upper(), "storage_ssd") == ["eu-west-1"]


async def test_substituir_remove_so_o_que_saiu_do_mesmo_conjunto(repo) -> None:
    r, s = repo
    await r.substituir(
        [
            _ponto("e2-standard-32", "us-central1"),
            _ponto("e2-standard-64", "us-central1"),  # não existe; a coleta nova não traz
            _ponto("e2-standard-64", "southamerica-east1"),  # outra região: fica
            _ponto("ref", "us-central1", fonte="seed"),  # outra fonte: fica
        ]
    )
    gravados, removidos = await r.substituir([_ponto("e2-standard-32", "us-central1")])
    await s.flush()

    assert (gravados, removidos) == (1, 1)
    restantes = {
        (p.sku_key, p.region, p.source)
        for p in await r.find(provider=_PROVEDOR, service_type="virtual_server")
    }
    assert restantes == {
        ("e2-standard-32", "us-central1", "api"),
        ("e2-standard-64", "southamerica-east1", "api"),
        ("ref", "us-central1", "seed"),
    }


async def test_find_filtra_por_regiao(repo) -> None:
    r, s = repo
    await r.substituir([_ponto("a", "sa-east-1"), _ponto("b", "us-east-1")])
    await s.flush()
    pontos = await r.find(provider=_PROVEDOR, service_type="virtual_server", region="sa-east-1")
    assert [p.sku_key for p in pontos] == ["a"]
