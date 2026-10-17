"""Banco pela vCPU-hora e pelo motor, storage à parte (PLANO_PRECISAO_OCI P1.6)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.services.category_calculator import CategoryRate
from app.services.billing_aggregator import BillingAggregator
from app.use_cases.analysis.build_migration_context import _cotar_banco, _misturar_motores

D = Decimal


def _t(prov: str, taxa: str, unidade: str = "vcpu_hour") -> CategoryRate:
    return CategoryRate(provider=prov, rate=D(taxa), unit=unidade, confidence="reference")


def test_regua_da_fatura_da_tela() -> None:
    """db.t3.medium PostgreSQL, 535,747 h (US$ 38,57) + 48,228 GB gp3 (US$ 5,55). Na OCI:
    2 vCPU x 535,747 h x 0,055 + 5,55 x (0,072 / 0,115)."""
    taxas = _misturar_motores(
        {"aws": _t("aws", "0.089"), "oci": _t("oci", "0.055")},
        {"postgresql": {"aws": _t("aws", "0.089"), "oci": _t("oci", "0.055")}},
        {"postgresql": D("1071.494")},
    )
    out = _cotar_banco(
        source_provider="aws",
        custo=D("44.12"),
        medido=D("38.57"),
        quantidade=D("1071.494"),
        preco_efetivo=D("38.57") / D("1071.494"),
        taxas=taxas,
        taxas_storage={
            "aws": _t("aws", "0.115", "gb_month"),
            "oci": _t("oci", "0.072", "gb_month"),
        },
    )
    [oci] = out
    assert round(oci.estimated_monthly_cost, 2) == D("62.41")
    # Antes, pela razão de referência (30 / 62), a OCI saía a US$ 21,35
    assert oci.estimated_monthly_cost > D("44.12")


def test_motor_mistura_a_taxa_pela_vcpu_hora() -> None:
    base = {"oci": _t("oci", "0.055")}
    por_motor = {"mysql": {"oci": _t("oci", "0.0366")}, "postgresql": {"oci": _t("oci", "0.055")}}
    taxas = _misturar_motores(base, por_motor, {"mysql": D(300), "postgresql": D(100)})
    # 3/4 MySQL + 1/4 PostgreSQL
    assert taxas["oci"].rate == D("0.04120")
    # Sem motor conhecido vale a base (a maior)
    assert _misturar_motores(base, por_motor, {"": D(10)})["oci"].rate == D("0.055")
    assert _misturar_motores(base, por_motor, {}) is base


def test_sem_taxa_de_storage_da_origem_o_resto_segue_o_preco_efetivo() -> None:
    out = _cotar_banco(
        source_provider="aws",
        custo=D("100"),
        medido=D("80"),
        quantidade=D("1000"),
        preco_efetivo=D("0.08"),
        taxas={"oci": _t("oci", "0.04")},
        taxas_storage={"oci": _t("oci", "0.072", "gb_month")},
    )
    # 1.250 vCPU-hora (1.000 medidas + 20 / 0,08) x 0,04
    assert out[0].estimated_monthly_cost == D("50.00")


def _linha(uso: str, custo: str, horas: str | None, motor: str | None) -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider="aws",
        billing_period="2026-09",
        service_name="AmazonRDS",
        cost=D(custo),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type=uso,
        usage_amount=D(horas) if horas else None,
        usage_unit="hours" if horas else None,
        project_id="p1",
        component_type="database",
        mapping_confidence="high",
        region="us-east-1",
        attributes={"database_engine": motor} if motor else None,
    )


@pytest.mark.asyncio
async def test_agregador_mede_a_vcpu_hora_do_banco_por_motor() -> None:
    linhas = [
        _linha("InstanceUsage:db.t3.medium", "38.57", "535.747", "PostgreSQL"),
        _linha("Multi-AZUsage:db.m6i.large", "250", "730", "MySQL"),
        _linha("RDS:GP3-Storage", "5.55", None, None),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "aws")).auxiliary
    assert aux.database == D("294.12")
    assert aux.quantidade_por_regiao["database"] == {"us-east-1": D("3991.494")}
    assert aux.medido_por_regiao["database"] == {"us-east-1": D("288.57")}
    assert aux.banco_por_motor == {"postgresql": D("1071.494"), "mysql": D("2920")}
