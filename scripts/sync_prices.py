#!/usr/bin/env python3
"""Popula cloud_price_points com preços oficiais, por região.

Roda offline (script/cron), nunca no caminho crítico da análise.

Cada provedor é coletado na sua região de cada zona pedida (São Paulo cota
`sa-east-1`, `southamerica-east1`, `brazilsouth` e `sa-saopaulo-1`). Zonas em
`app/domain/services/regioes.py`; PLANO_PRECO_REGIONAL D1.

Uso:
    python scripts/sync_prices.py --providers aws,gcp,azure,oci,categories-live
    python scripts/sync_prices.py --providers azure --zonas sao-paulo
    python scripts/sync_prices.py --providers categories        # referência, sem rede
    python scripts/sync_prices.py --zonas todas                 # as 11 zonas
    python scripts/sync_prices.py --pedidos     # só o que a contestação do agente pediu

O GCP exige `GCP_BILLING_API_KEY`; sem ela, o GCP sai vazio e os outros seguem.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Sequence
from uuid import UUID

from app.core.config import get_settings
from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.services.contestacao import PedidoDeSync
from app.domain.services.regioes import ZONAS, regiao_canonica
from app.infrastructure.database.seed_category_rates import category_rate_points
from app.infrastructure.database.session import get_sessionmaker
from app.infrastructure.pricing.aws_price_client import fetch_aws_ec2_prices
from app.infrastructure.pricing.azure_price_client import fetch_azure_vm_prices
from app.infrastructure.pricing.category_rate_client import (
    fetch_aws_category_rates,
    fetch_azure_category_rates,
    fetch_azure_network_global,
    fetch_gcp_category_rates,
    fetch_oci_category_rates,
)
from app.infrastructure.pricing.ciclo_de_vida import recalcular_ciclo_de_vida
from app.infrastructure.pricing.gcp_price_client import fetch_gcp_compute_prices
from app.infrastructure.pricing.oci_price_client import fetch_oci_compute_prices
from app.infrastructure.repositories.pg_acoes_de_preco_repo import PgAcoesDePrecoRepository
from app.infrastructure.repositories.pg_cloud_price_point_repo import (
    PgCloudPricePointRepository,
)
from app.use_cases.catalog.sync_cloud_prices import SyncCloudPricesUseCase

# As zonas que os bills de teste e os mocks usam. `--zonas todas` pega as 11.
ZONAS_PADRAO = ("virginia", "meio-oeste", "sao-paulo", "texas", "california")
_ORDEM = ("azure", "oci", "aws", "gcp", "categories", "categories-live")


def regioes_por_provedor(zonas: Sequence[str]) -> dict[str, list[str]]:
    """Região canônica de cada provedor em cada zona, sem repetir."""
    out: dict[str, list[str]] = {}
    for provedor in ("aws", "gcp", "azure", "oci"):
        regioes = [r for z in zonas if (r := regiao_canonica(z, provedor)) is not None]
        out[provedor] = list(dict.fromkeys(regioes))
    return out


def _fetchers(
    regioes: dict[str, list[str]], max_pages: int
) -> dict[str, Callable[[], Sequence[CloudPricePoint]]]:
    chave_gcp = get_settings().gcp_billing_api_key

    def azure() -> list[CloudPricePoint]:
        return [p for r in regioes["azure"] for p in fetch_azure_vm_prices(r, max_pages=max_pages)]

    def aws() -> list[CloudPricePoint]:
        return [p for r in regioes["aws"] for p in fetch_aws_ec2_prices(region=r)]

    def categorias_oficiais() -> list[CloudPricePoint]:
        return [
            *fetch_aws_category_rates(regioes["aws"]),
            *fetch_gcp_category_rates(api_key=chave_gcp, regioes=regioes["gcp"]),
            *(
                p
                for rede_azure in [fetch_azure_network_global()]
                for r in regioes["azure"]
                for p in fetch_azure_category_rates(r, rede_global=rede_azure)
            ),
            *fetch_oci_category_rates(regioes["oci"]),
        ]

    return {
        "azure": azure,
        "oci": lambda: fetch_oci_compute_prices(regioes=regioes["oci"]),
        "aws": aws,
        "gcp": lambda: fetch_gcp_compute_prices(api_key=chave_gcp, regioes=regioes["gcp"]),
        "categories": category_rate_points,  # taxas de referência (seed)
        "categories-live": categorias_oficiais,  # taxas oficiais (fetch)
    }


def _regioes_dos_pedidos(
    pedidos: Sequence[PedidoDeSync],
) -> tuple[dict[str, list[str]], set[str]]:
    """Regiões e coletas que os pedidos abertos pedem: compute do provedor e, para
    taxa de categoria, as categorias oficiais."""
    regioes: dict[str, list[str]] = {p: [] for p in ("aws", "gcp", "azure", "oci")}
    wanted: set[str] = set()
    for p in pedidos:
        if p.regiao and p.provedor in regioes and p.regiao not in regioes[p.provedor]:
            regioes[p.provedor].append(p.regiao)
        wanted.add("categories-live" if p.tipo != "virtual_server" else p.provedor)
    return regioes, wanted


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--providers", default="azure,oci", help="csv: " + ",".join(_ORDEM))
    parser.add_argument(
        "--zonas",
        default=",".join(ZONAS_PADRAO),
        help="csv de zonas, ou 'todas': " + ",".join(z.id for z in ZONAS),
    )
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument(
        "--pedidos",
        action="store_true",
        help="atende os pedidos de sync abertos pela contestação do agente (ignora as zonas)",
    )
    args = parser.parse_args()

    wanted = {p.strip() for p in args.providers.split(",") if p.strip()}
    zonas = (
        [z.id for z in ZONAS]
        if args.zonas == "todas"
        else [z.strip() for z in args.zonas.split(",") if z.strip()]
    )
    regioes = regioes_por_provedor(zonas)
    sm = get_sessionmaker()
    pedidos: list[tuple[UUID, PedidoDeSync]] = []
    if args.pedidos:
        async with sm() as session:
            pedidos = await PgAcoesDePrecoRepository(session).pedidos_abertos()
        regioes, wanted = _regioes_dos_pedidos([p for _, p in pedidos])
        print(f"pedidos abertos: {len(pedidos)}")  # noqa: T201
    fetchers = _fetchers(regioes, args.max_pages)

    falhas: list[str] = []
    for provider in _ORDEM:
        if provider not in wanted:
            continue
        # Uma sessão e um commit por provedor: a API de um fora do ar não pode
        # descartar o que os outros já coletaram.
        async with sm() as session:
            uc = SyncCloudPricesUseCase(PgCloudPricePointRepository(session))
            try:
                res = await uc.execute(provider, fetchers[provider])
            except Exception as exc:  # relatar e seguir para o próximo
                await session.rollback()
                falhas.append(provider)
                print(f"{provider}: FALHOU ({type(exc).__name__}: {exc})")  # noqa: T201
                continue
            await session.commit()
        linha = f"fetched={res.fetched} upserted={res.upserted} ausentes={res.removed}"
        print(f"{provider}: {linha}")  # noqa: T201
    if pedidos:
        atendidos = [
            i
            for i, p in pedidos
            if ("categories-live" if p.tipo != "virtual_server" else p.provedor) not in falhas
        ]
        async with sm() as session:
            fechados = await PgAcoesDePrecoRepository(session).marcar_atendidos(atendidos)
            await session.commit()
        print(f"pedidos atendidos: {fechados}")  # noqa: T201
    # Estado e score dos preços depois da coleta (PLANO_CICLO_DE_VIDA_DO_PRECO)
    print(await recalcular_ciclo_de_vida())  # noqa: T201
    if falhas:
        raise SystemExit(f"falharam: {', '.join(falhas)}")


if __name__ == "__main__":
    asyncio.run(main())
