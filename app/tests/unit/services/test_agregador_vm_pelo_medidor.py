"""VM da Azure pelo nome do medidor (PROXIMAS_EXECUCOES, item 10).

A exportação da Azure sem o SKU traz a VM só como `Virtual Machines | Edsv5 Series |
E16ds v5`. Antes a hora ficava sem vCPU nem família, e o pool "general" do caso de
US$ 471.870 entrava com 126 vCPU e 0 GB."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.services.azure_shape import tamanho_do_medidor
from app.services.billing_aggregator import BillingAggregator


def _vm(provider: str, medidor: str, horas: str = "730") -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider=provider,
        billing_period="2026-09",
        service_name="Virtual Machines",
        cost=Decimal("100"),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cost_management",
        pricing_confidence="high",
        usage_type=medidor,
        usage_amount=Decimal(horas),
        usage_unit="hours",
        project_id="p1",
        component_type="core",
        mapping_confidence="high",
        region="brazilsouth",
    )


@pytest.mark.parametrize(
    ("medidor", "tamanho"),
    [
        ("Virtual Machines | Edsv5 Series | E16ds v5", "Standard_E16ds_v5"),
        ("Virtual Machines | Dsv5 Series | D16s v5 Spot", "Standard_D16s_v5"),
        ("Virtual Machines | Dsv5 Series | D4s v5 Low Priority", "Standard_D4s_v5"),
        ("Virtual Machines | BS Series | B2ms", "Standard_B2ms"),
        ("Virtual Machines | Esv5 Series Windows | E32s v5", "Standard_E32s_v5"),
        # O que não é tamanho de VM fica de fora
        ("Virtual Machines Licenses | SQL Server Enterprise | SQL Server Enterprise vCPU", None),
        ("Storage | Premium SSD Managed Disks | P10 Disks", None),
        ("Virtual Machines | Edsv5 Series", None),
    ],
)
def test_tamanho_pelo_medidor(medidor: str, tamanho: str | None) -> None:
    assert tamanho_do_medidor(medidor) == tamanho


@pytest.mark.asyncio
async def test_pool_da_azure_pela_familia_e_capacidade_do_medidor() -> None:
    res = await BillingAggregator().aggregate(
        [_vm("azure", "Virtual Machines | Edsv5 Series | E16ds v5", "7300")], "azure"
    )
    [pool] = res.pools
    assert pool.pool_key.machine_family == "EDSV5"
    # 7.300 h de 16 vCPU e 128 GB num mês de 730 h: 10 máquinas
    assert pool.capacity.vcpus == Decimal("160")
    assert pool.capacity.ram_gib == Decimal("1280")


@pytest.mark.asyncio
async def test_so_a_azure_le_o_medidor() -> None:
    res = await BillingAggregator().aggregate([_vm("gcp", "Compute | X | E16ds v5")], "gcp")
    assert res.pools[0].pool_key.machine_family != "EDSV5"


@pytest.mark.asyncio
async def test_licenca_sem_maquina_vai_para_o_pool_da_maquina() -> None:
    """A linha da licença não diz a máquina; sem isto o custo dela sumia (conservação)."""
    licenca = dataclasses.replace(
        _vm("azure", "Virtual Machines Licenses | SQL Server Enterprise | SQL Server vCPU"),
        component_type="os_license",
    )
    vm = _vm("azure", "Virtual Machines | Edsv5 Series | E16ds v5")
    res = await BillingAggregator().aggregate([vm, licenca], "azure")
    [pool] = res.pools
    assert pool.pool_key.machine_family == "EDSV5"
    assert pool.costs.os_licensing == Decimal("100")
