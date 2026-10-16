"""Pool coberto por compromisso estimado pelo preço sob demanda da origem (lote v4, V4.6).

No AWS Cost Explorer real, o C7G custa 0,004 a 0,018 por vCPU-hora contra 0,036 a
0,054 sob demanda: o Savings Plan paga o resto na linha dele. Pool a pool, o destino
aparecia a 7-8x. O total do cenário já estava certo; a marca tira a diferença pool a
pool da tabela e diz o motivo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domain.entities.cloud_price_point import CloudPricePoint
from app.domain.entities.workload_pool import (
    AuxiliaryCosts,
    FamilyPool,
    FamilyPoolKey,
    PoolCapacity,
    PoolCosts,
)
from app.domain.services.cobertura_por_compromisso import cobertura_estimada, familia_do_ponto
from app.domain.services.currency import identity_rate
from app.use_cases.analysis.build_migration_context import BuildMigrationContextUseCase
from app.use_cases.analysis.equivalence_projection import project_equivalences


def _ponto(sku: str, preco: str, vcpus: int) -> CloudPricePoint:
    return CloudPricePoint(
        provider="aws",
        sku_key=sku,
        service_name=sku,
        service_type="virtual_server",
        region="us-east-1",
        price=Decimal(preco),
        currency="USD",
        vcpus=Decimal(vcpus),
        ram_gib=Decimal(vcpus * 2),
        captured_at=datetime.now(UTC),
    )


def _pool(familia: str, vcpus: str, custo: str) -> FamilyPool:
    return FamilyPool(
        pool_key=FamilyPoolKey(machine_family=familia, region="us-east-1"),
        capacity=PoolCapacity(vcpus=Decimal(vcpus), ram_gib=Decimal(vcpus) * 2),
        costs=PoolCosts(compute_base=Decimal(custo)),
        hours_window=Decimal(730),
    )


def _uc(pontos: list[CloudPricePoint]) -> BuildMigrationContextUseCase:
    repo = AsyncMock()
    repo.find.return_value = pontos
    return BuildMigrationContextUseCase(
        AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), price_repo=repo
    )


def test_cobertura_e_a_fracao_do_sob_demanda_que_o_pool_nao_pagou() -> None:
    assert cobertura_estimada(Decimal("0.009"), Decimal("0.036")) == Decimal("0.75")
    assert cobertura_estimada(Decimal("0.05"), Decimal("0.036")) == Decimal("0")
    assert cobertura_estimada(Decimal("0.01"), Decimal("0")) is None


@pytest.mark.parametrize(
    ("provedor", "sku", "familia"),
    [
        ("aws", "c7g.2xlarge", "C7G"),
        ("gcp", "e2-standard-4", "E2"),
        ("oci", "VM.Standard.E4.Flex", "E4"),
        ("azure", "Standard_D4s_v5", "DSV5"),
    ],
)
def test_familia_do_ponto_como_o_pool_nomeia(provedor: str, sku: str, familia: str) -> None:
    assert familia_do_ponto(provedor, sku) == familia


@pytest.mark.asyncio
async def test_pool_que_paga_bem_abaixo_do_sob_demanda_e_marcado() -> None:
    # c7g a 0,0725/h em 2 vCPU = 0,03625 por vCPU-hora sob demanda
    pontos = [_ponto("c7g.large", "0.0725", 2), _ponto("c7g.xlarge", "0.145", 4)]
    # 16 vCPU médios x 730 h a 0,009 = 105,12
    coberto = _pool("C7G", "16", "105.12")
    cheio = _pool("C7G", "16", "420")
    aux = AuxiliaryCosts(commitment=Decimal(100), commitment_compute=Decimal(100))
    uc = _uc(pontos)
    resultado = await uc._cobertura_estimada("aws", [coberto], aux, identity_rate("USD"))
    assert resultado == {"C7G/us-east-1": Decimal("0.7517")}
    assert await uc._cobertura_estimada("aws", [cheio], aux, identity_rate("USD")) == {}


@pytest.mark.asyncio
async def test_sem_compromisso_de_compute_nao_marca() -> None:
    uc = _uc([_ponto("c7g.large", "0.0725", 2)])
    aux = AuxiliaryCosts(commitment=Decimal(0), commitment_compute=Decimal(0))
    assert (
        await uc._cobertura_estimada(
            "aws", [_pool("C7G", "16", "105.12")], aux, identity_rate("USD")
        )
        == {}
    )


@pytest.mark.asyncio
async def test_sem_preco_da_familia_nao_marca() -> None:
    uc = _uc([_ponto("m7g.large", "0.0816", 2)])
    aux = AuxiliaryCosts(commitment_compute=Decimal(100))
    assert (
        await uc._cobertura_estimada(
            "aws", [_pool("C7G", "16", "105.12")], aux, identity_rate("USD")
        )
        == {}
    )


def _payload(marca: dict[str, Any]) -> dict[str, Any]:
    return {
        "provedor_origem": "aws",
        "pools_computacao": [
            {
                "familia": "C7G",
                "regiao": "us-east-1",
                "custo_computacao": 105.12,
                **marca,
                "equivalentes": [
                    {
                        "provedor": "gcp",
                        "nome_servico": "Compute Engine",
                        "sku": "t2a-standard-16",
                        "financeiro": {"custo_mensal_estimado": 790.0, "delta_custo": 684.88},
                    }
                ],
            }
        ],
    }


def test_pool_marcado_sai_da_diferenca_pool_a_pool_com_o_motivo() -> None:
    linha = project_equivalences(_payload({"coberto_por_compromisso_estimado": 0.7517}))[0]
    assert linha.estimated_monthly_cost == 790.0
    assert linha.cost_delta is None
    assert "compromisso" in linha.pricing_reason


def test_pool_sem_marca_segue_com_a_diferenca() -> None:
    linha = project_equivalences(_payload({}))[0]
    assert linha.cost_delta == 684.88
