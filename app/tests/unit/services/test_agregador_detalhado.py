"""Agregador com fatos medidos (E6 do PLANO_CUR_BIGQUERY).

Linha com ``attributes`` vem de export detalhado: a unidade de capacidade é
declarada, o shape medido prevalece. Linha sem ``attributes`` é o caminho
agregado de sempre — e tem de continuar exatamente como era.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.entities.inventario_medido import InventarioDoPool
from app.domain.entities.workload_pool import PoolCapacity, PoolCosts, PoolKey, WorkloadPool
from app.domain.services.pool_aggregation import aggregate_by_family
from app.services.billing_aggregator import BillingAggregator

_JANELA = Decimal("730")


def _linha(
    *,
    component_type: str = "core",
    cost: str = "10",
    usage: str | None = "744",
    unit: str | None = "hours",
    usage_type: str = "BoxUsage:m7g.2xlarge",
    attrs: dict | None = None,
    resource_id: str | None = None,
    region: str = "us-east-1",
    periodo: str = "2026-03",
) -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider="aws",
        billing_period=periodo,
        service_name="AmazonEC2",
        cost=Decimal(cost),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="fallback",
        pricing_confidence="low",
        usage_type=usage_type,
        usage_amount=None if usage is None else Decimal(usage),
        usage_unit=unit,
        project_id="p1",
        region=region,
        component_type=component_type,
        mapping_confidence="high",
        sku_metadata={"family": "M7G"},
        resource_id=resource_id,
        attributes=attrs,
    )


def _instancia(**attrs) -> dict:
    return {"charge_kind": "usage", "capacity_unit": "instance", **attrs}


async def _agregar(linhas):
    return await BillingAggregator().aggregate(linhas, "aws")


# ── instância ───────────────────────────────────────────────────────────────


async def test_shape_medido_prevalece() -> None:
    # 744 h de uma instância que o provedor diz ter 8 vCPU / 32 GiB.
    res = await _agregar(
        [
            _linha(
                attrs=_instancia(vcpus="8", memory_gib="32", instance_type="m7g.2xlarge"),
                resource_id="i-1",
            )
        ]
    )
    [pool] = res.pools
    assert pool.capacity.vcpus == Decimal("744") * 8 / _JANELA
    assert pool.capacity.ram_gib == Decimal("744") * 32 / _JANELA
    assert res.measured_capacity_cost == Decimal("10")
    assert res.estimated_capacity_cost == Decimal("0")
    inv = pool.inventario
    assert inv is not None
    assert inv.instancias == 1
    assert inv.tipos() == {"m7g.2xlarge": 1}
    assert inv.fonte_capacidade() == "medida"


async def test_sem_medida_estima_pelo_nome_e_declara() -> None:
    res = await _agregar([_linha(attrs=_instancia(instance_type="m7g.2xlarge"), resource_id="i-1")])
    [pool] = res.pools
    assert pool.capacity.vcpus == Decimal("744") * 8 / _JANELA  # m7g.2xlarge pelo nome
    assert res.estimated_capacity_cost == Decimal("10")
    assert pool.inventario.fonte_capacidade() == "estimada"


async def test_so_vcpu_medido_conta_como_estimado() -> None:
    # A Azure informa vCPU e não RAM: completar pela estimativa não é medir.
    res = await _agregar(
        [_linha(attrs=_instancia(vcpus="8", instance_type="m7g.2xlarge"), resource_id="i-1")]
    )
    [pool] = res.pools
    assert pool.capacity.ram_gib == Decimal("744") * 32 / _JANELA
    assert res.estimated_capacity_cost == Decimal("10")


async def test_fracao_de_tempo_ativo_em_horario_comercial() -> None:
    linhas = [
        _linha(
            usage="372",  # 12 h/dia em março
            attrs=_instancia(vcpus="2", memory_gib="4", instance_type="t3a.medium"),
            resource_id=f"i-{n}",
            usage_type="BoxUsage:t3a.medium",
        )
        for n in range(3)
    ]
    res = await _agregar(linhas)
    [pool] = res.pools
    assert res.inventario.horas_periodo == Decimal("744")
    assert pool.inventario.fracao_tempo_ativo(Decimal("744")) == Decimal("0.5")


# ── por unidade (GCP) ───────────────────────────────────────────────────────


async def test_vcpu_hora_nao_e_expandido_pelo_shape() -> None:
    """Linha de core do GCP já é vCPU-hora: expandir pelos cores medidos
    multiplicaria a capacidade pelo tamanho da VM."""
    atributos = {
        "charge_kind": "usage",
        "capacity_unit": "vcpu",
        "vcpus": "4",
        "instance_type": "e2-standard-4",
    }
    res = await _agregar(
        [
            _linha(
                usage="2976",
                usage_type="E2 Instance Core running in Americas",
                attrs=atributos,
                resource_id="vm-1",
            ),
            _linha(
                component_type="ram",
                usage="11904",
                unit="gibibyte hour",
                usage_type="E2 Instance Ram running in Americas",
                attrs={**atributos, "capacity_unit": "gib"},
                resource_id="vm-1",
            ),
        ]
    )
    [pool] = res.pools
    assert pool.capacity.vcpus == Decimal("2976") / _JANELA
    assert pool.capacity.ram_gib == Decimal("11904") / _JANELA
    # Horas da VM = vCPU-hora ÷ cores: 2976 / 4 = 744, o mês inteiro.
    assert pool.inventario.horas_instancia == Decimal("744")
    assert pool.inventario.instancias == 1


async def test_ocpu_usa_vcpus_por_unidade() -> None:
    atributos = {"charge_kind": "usage", "capacity_unit": "vcpu", "vcpus_per_unit": "2"}
    res = await _agregar([_linha(usage="744", usage_type="E4 OCPU", attrs=atributos)])
    [pool] = res.pools
    assert pool.capacity.vcpus == Decimal("1488") / _JANELA


# ── destino do custo sem capacidade ─────────────────────────────────────────


async def test_compromisso_vai_para_o_balde_de_compromisso() -> None:
    res = await _agregar(
        [
            _linha(
                component_type="commitment",
                usage_type="ComputeSP:3yrNoUpfront",
                attrs={"charge_kind": "commitment"},
            )
        ]
    )
    assert res.pools == ()
    assert res.auxiliary.commitment == Decimal("10")


async def test_uso_detalhado_sem_capacidade_e_orfao_nao_compromisso() -> None:
    """CPU credit é custo de uso real; chamá-lo de compromisso esconderia isso."""
    res = await _agregar(
        [_linha(unit="vCPU-Hours", usage_type="CPUCredits:t4g", attrs={"charge_kind": "usage"})]
    )
    assert res.auxiliary.commitment == Decimal("0")
    assert res.auxiliary.orphan == Decimal("10")


async def test_legado_sem_capacidade_continua_compromisso() -> None:
    res = await _agregar([_linha(usage="0", unit=None, usage_type="ComputeSP:3yrNoUpfront")])
    assert res.auxiliary.commitment == Decimal("10")
    assert res.inventario is None  # legado não ganha inventário


async def test_legado_nao_ganha_inventario_no_pool() -> None:
    res = await _agregar([_linha()])
    [pool] = res.pools
    assert pool.inventario is None


# ── armazenamento, banco, sob demanda ───────────────────────────────────────


async def test_armazenamento_medido() -> None:
    atributos = {"charge_kind": "usage"}
    res = await _agregar(
        [
            _linha(
                component_type="storage_ssd",
                usage="500",
                unit="gibibyte month",
                usage_type="EBS:VolumeUsage.gp3",
                attrs=atributos,
                resource_id="vol-1",
            ),
            _linha(
                component_type="storage_ssd",
                usage="300",
                unit="gibibyte month",
                usage_type="EBS:VolumeUsage.gp3",
                attrs=atributos,
                resource_id="vol-2",
            ),
            # IOPS não é tamanho.
            _linha(
                component_type="storage_ssd",
                usage="3000",
                unit="IOPS-Mo",
                usage_type="EBS:VolumeP-IOPS.gp3",
                attrs=atributos,
                resource_id="vol-1",
            ),
        ]
    )
    medido = res.inventario.armazenamento["storage_ssd"]
    assert medido.gb == Decimal("800")
    assert medido.quantidade == 2


async def test_banco_medido_com_classe_motor_e_multi_az() -> None:
    arn = "arn:aws:rds:us-east-1:1:db:pg-1"
    res = await _agregar(
        [
            _linha(
                component_type="database",
                cost="100",
                usage="744",
                usage_type="Multi-AZUsage:db.m6g.xl",
                resource_id=arn,
                attrs=_instancia(
                    instance_type="db.m6g.xlarge",
                    database_engine="PostgreSQL",
                    deployment_option="Multi-AZ",
                ),
            ),
            _linha(
                component_type="database",
                cost="20",
                usage="100",
                unit="gibibyte month",
                usage_type="RDS:Multi-AZ-GP3-Storage",
                resource_id=arn,
                attrs={"charge_kind": "usage"},
            ),
            # Storage sem instância medida não é "um banco".
            _linha(
                component_type="database",
                cost="5",
                usage="10",
                unit="gibibyte month",
                usage_type="RDS:GP2-Storage",
                resource_id="arn:x",
                attrs={"charge_kind": "usage"},
            ),
        ]
    )
    [banco] = res.inventario.bancos
    assert banco.classe == "db.m6g.xlarge"
    assert banco.motor == "PostgreSQL"
    assert banco.multi_az is True
    assert banco.horas == Decimal("744")
    assert banco.custo == Decimal("120")  # instância + storage do mesmo recurso


async def test_sob_demanda_somado_so_nas_linhas_que_o_tem() -> None:
    res = await _agregar(
        [
            _linha(cost="60", attrs=_instancia(vcpus="8", memory_gib="32", on_demand_cost="100")),
            _linha(cost="30", attrs=_instancia(vcpus="8", memory_gib="32")),
        ]
    )
    assert res.custo_sob_demanda == Decimal("100")
    assert res.custo_efetivo_referenciado == Decimal("60")


# ── inventário do pool ──────────────────────────────────────────────────────


def test_inventario_juntar_e_fonte_mista() -> None:
    a = InventarioDoPool(
        recursos=frozenset({"i-1"}),
        por_tipo=(("m7g.large", frozenset({"i-1"})),),
        horas_instancia=Decimal("744"),
        vcpu_horas_medidas=Decimal("1488"),
    )
    b = InventarioDoPool(
        recursos=frozenset({"i-2", "i-3"}),
        por_tipo=(("m7g.large", frozenset({"i-2"})), ("m7g.xlarge", frozenset({"i-3"}))),
        horas_instancia=Decimal("744"),
        vcpu_horas_estimadas=Decimal("100"),
    )
    junto = a.juntar(b)
    assert junto.instancias == 3
    assert junto.tipos() == {"m7g.large": 2, "m7g.xlarge": 1}
    assert junto.fonte_capacidade() == "mista"
    assert junto.fracao_tempo_ativo(Decimal("744")) == Decimal("1488") / Decimal("2232")


def test_agregacao_por_familia_preserva_inventario() -> None:
    def pool(projeto: str, recurso: str) -> WorkloadPool:
        return WorkloadPool(
            pool_key=PoolKey(projeto, "M7G", "us-east-1"),
            capacity=PoolCapacity(Decimal("8"), Decimal("32")),
            costs=PoolCosts(Decimal("100")),
            hours_window=_JANELA,
            inventario=InventarioDoPool(
                recursos=frozenset({recurso}),
                horas_instancia=Decimal("744"),
                vcpu_horas_medidas=Decimal("5952"),
            ),
        )

    [familia] = aggregate_by_family((pool("a", "i-1"), pool("b", "i-2")))
    assert familia.inventario.instancias == 2
    assert familia.source_projects == ("a", "b")
