"""Banco por partes: instância pela vCPU-hora e pelo motor, o resto pela parte
(PLANO_PRECISAO_OCI P1.6, B1 a B4)."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.billing_enriched import BillingEnriched
from app.domain.services.category_calculator import CategoryRate
from app.services.billing_aggregator import BillingAggregator
from app.use_cases.analysis.build_migration_context import (
    _cotar_banco,
    _misturar_motores,
    _resto_do_banco,
)

D = Decimal


def _t(prov: str, taxa: str, unidade: str = "vcpu_hour") -> CategoryRate:
    return CategoryRate(provider=prov, rate=D(taxa), unit=unidade, confidence="reference")


_STORAGE = {
    None: {"aws": _t("aws", "0.115", "gb_month"), "oci": _t("oci", "0.072", "gb_month")},
    "mysql": {"oci": _t("oci", "0.04", "gb_month")},
    "postgresql": {"oci": _t("oci", "0.072", "gb_month")},
}
_BACKUP = {
    None: {"oci": _t("oci", "0.04", "gb_month")},
    "postgresql": {"oci": _t("oci", "0.0255", "gb_month")},
}
_CACHE = {
    "ate_4gb": {"oci": _t("oci", "0.0194", "gb_hour")},
    "acima_10gb": {"oci": _t("oci", "0.0136", "gb_hour")},
}


def _resto(
    partes: dict[str, dict[str, Decimal]],
) -> tuple[dict[str, Decimal], dict[str, dict[str, Decimal]]]:
    return _resto_do_banco(
        partes,
        custos={k: v["custo"] for k, v in partes.items()},
        storage=_STORAGE,
        backup=_BACKUP,
        cache=_CACHE,
        provedores=["oci"],
    )


def test_regua_da_fatura_da_tela() -> None:
    """db.t3.medium PostgreSQL, 535,747 h (US$ 38,57) + 48,228 GB gp3 (US$ 5,55). Na
    OCI, pela calculadora oficial: 2 vCPU x 535,747 h x 0,049 + 48,228 GB x 0,072."""
    taxas = _misturar_motores(
        {"aws": _t("aws", "0.089"), "oci": _t("oci", "0.049")},
        {"postgresql": {"aws": _t("aws", "0.089"), "oci": _t("oci", "0.049")}},
        {"postgresql": D("1071.494")},
    )
    fator, detalhe = _resto(
        {"armazenamento:postgresql:": {"quantidade": D("48.228"), "custo": D("5.55")}}
    )
    [oci] = _cotar_banco(
        source_provider="aws",
        custo=D("44.12"),
        medido=D("38.57"),
        quantidade=D("1071.494"),
        preco_efetivo=D("38.57") / D("1071.494"),
        taxas=taxas,
        fator_do_resto=fator,
    )
    assert round(oci.estimated_monthly_cost, 2) == D("55.98")
    assert round(detalhe["oci"]["armazenamento"], 3) == D("3.472")


def test_motor_mistura_a_taxa_pela_vcpu_hora() -> None:
    base = {"oci": _t("oci", "0.049")}
    por_motor = {"mysql": {"oci": _t("oci", "0.0366")}, "postgresql": {"oci": _t("oci", "0.049")}}
    taxas = _misturar_motores(base, por_motor, {"mysql": D(300), "postgresql": D(100)})
    # 3/4 MySQL + 1/4 PostgreSQL
    assert taxas["oci"].rate == D("0.039700")
    # Sem motor conhecido vale a maior taxa entre os motores
    assert _misturar_motores(base, por_motor, {"": D(10)})["oci"].rate == D("0.049")
    assert _misturar_motores(base, por_motor, {}) is base


def test_alta_disponibilidade_usa_as_replicas_do_destino() -> None:
    """A vCPU-hora em HA da origem conta 2 instâncias; o OCI MySQL HeatWave tem 3."""
    base = {"oci": _t("oci", "0.049"), "aws": _t("aws", "0.089")}
    por_motor = {"mysql": {"oci": _t("oci", "0.0366"), "aws": _t("aws", "0.0855")}}
    taxas = _misturar_motores(base, por_motor, {"mysql:ha": D(100)})
    assert taxas["oci"].rate == D("0.05490")  # 0,0366 x 3/2
    assert taxas["aws"].rate == D("0.0855")
    # Sem motor e em HA, a maior entre os motores: MySQL em HA (0,0549) > base (0,049)
    assert _misturar_motores(base, por_motor, {":ha": D(1)})["oci"].rate == D("0.05490")


def test_resto_por_parte() -> None:
    fator, detalhe = _resto(
        {
            # Cache pela memória do nó, na faixa dele
            "cache:ate_4gb": {"quantidade": D(1000), "custo": D(30)},
            # Storage do MySQL em HA: 3 cópias na OCI
            "armazenamento:mysql:ha": {"quantidade": D(100), "custo": D(23)},
            "backup:postgresql": {"quantidade": D(200), "custo": D(19)},
            # Só a origem cobra: zero no destino
            "sem_equivalente": {"quantidade": D(0), "custo": D(40)},
            "outros": {"quantidade": D(0), "custo": D(8)},
        }
    )
    oci = detalhe["oci"]
    assert oci["cache"] == D("19.4000")
    assert oci["armazenamento"] == D("12.00")  # 100 GB x 0,04 x 3
    assert oci["backup"] == D("5.1000")
    assert oci["sem_equivalente"] == D(0)
    assert oci["outros"] == D(8)
    assert fator["oci"] == sum(oci.values(), D(0)) / D(120)


def test_parte_sem_quantidade_ou_sem_taxa_fica_ao_custo_da_origem() -> None:
    _, detalhe = _resto(
        {
            "cache:ate_4gb": {"quantidade": D(0), "custo": D(10)},
            "cache:de_4_a_10gb": {"quantidade": D(500), "custo": D(12)},
        }
    )
    assert detalhe["oci"]["cache"] == D(22)


def test_sem_vcpu_hora_a_instancia_vai_pela_razao() -> None:
    out = _cotar_banco(
        source_provider="aws",
        custo=D("100"),
        medido=D("80"),
        quantidade=D(0),
        preco_efetivo=None,
        taxas={"aws": _t("aws", "0.089"), "oci": _t("oci", "0.049")},
        fator_do_resto={"oci": D("0.5")},
    )
    [oci] = out
    # 80 x 0,049 / 0,089 + 20 x 0,5
    assert round(oci.estimated_monthly_cost, 2) == D("54.04")


def test_banco_todo_instancia_nao_rebaixa_a_confianca() -> None:
    """D3 do E2E de 17/10: sem a razão do resto no destino, a confiança só cai quando há
    resto, que fica ao custo da origem."""

    def confianca(custo: str) -> str:
        [oci] = _cotar_banco(
            source_provider="aws",
            custo=D(custo),
            medido=D("80"),
            quantidade=D("1000"),
            preco_efetivo=D("0.08"),
            taxas={"aws": _t("aws", "0.089"), "oci": _t("oci", "0.049")},
            fator_do_resto={},
        )
        return oci.pricing_confidence

    assert confianca("80") == _t("oci", "0.049").confidence
    assert confianca("100") == "low"


def _linha(
    uso: str,
    custo: str,
    horas: str | None,
    motor: str | None,
    *,
    cobranca: str | None = None,
    unidade: str = "hours",
    servico: str = "AmazonRDS",
) -> BillingEnriched:
    return BillingEnriched(
        user_id=uuid4(),
        report_id=uuid4(),
        provider="aws",
        billing_period="2026-09",
        service_name=servico,
        cost=D(custo),
        currency="USD",
        source_line_hash=uuid4().hex,
        pricing_source="cur",
        pricing_confidence="high",
        usage_type=uso,
        usage_amount=D(horas) if horas else None,
        usage_unit=unidade if horas else None,
        project_id="p1",
        component_type="database",
        mapping_confidence="high",
        region="us-east-1",
        attributes={k: v for k, v in (("database_engine", motor), ("charge_kind", cobranca)) if v}
        or None,
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
    assert aux.banco_por_motor == {"postgresql": D("1071.494"), "mysql:ha": D("2920")}
    # O storage sem quantidade fica nas partes, ao custo da origem
    assert aux.banco_partes == {"outros": {"quantidade": D(0), "custo": D("5.55")}}


@pytest.mark.asyncio
async def test_motor_do_relatorio_detalhado_nao_vira_export_detalhado() -> None:
    """PLANO_EQUIVALENTES_E_TARIFAS §10: a linha do relatório legado só traz o motor (e o
    tipo que o enriquecimento junta). O motor entra na vCPU-hora; a fatura não ganha
    inventário medido."""
    linha = _linha("Multi-AZUsage:db.r6g.large", "100", "744", "PostgreSQL")
    linha.attributes = {"database_engine": "PostgreSQL", "instance_type": linha.usage_type}
    resultado = await BillingAggregator().aggregate([linha], "aws")
    assert resultado.auxiliary.banco_por_motor == {"postgresql:ha": D("2976")}
    assert resultado.inventario is None


@pytest.mark.asyncio
async def test_taxa_de_reserva_nao_soma_horas_e_entra_no_custo_das_instancias() -> None:
    """Base amortizada: o uso coberto pela reserva vem na linha de uso, com as horas; a
    taxa da reserva traz a parte não usada com as horas da reserva inteira. Contar as
    horas dela dobraria a vCPU-hora. Base sem amortização: o uso coberto vem a custo
    zero e a taxa paga a reserva."""
    amortizada = [
        _linha("InstanceUsage:db.r8g.4xl", "1257.06", "744", "PostgreSQL", cobranca="usage"),
        _linha("HeavyUsage:db.r8g.4xl", "100", "744", None, cobranca="commitment"),
    ]
    aux = (await BillingAggregator().aggregate(amortizada, "aws")).auxiliary
    assert aux.quantidade_por_regiao["database"] == {"us-east-1": D("11904")}  # 744 x 16
    assert aux.medido_por_regiao["database"] == {"us-east-1": D("1357.06")}

    sem_amortizar = [
        _linha("InstanceUsage:db.r8g.4xl", "0", "744", "PostgreSQL", cobranca="usage"),
        _linha("HeavyUsage:db.r8g.4xl", "1357.06", "744", None, cobranca="commitment"),
    ]
    aux = (await BillingAggregator().aggregate(sem_amortizar, "aws")).auxiliary
    assert aux.quantidade_por_regiao["database"] == {"us-east-1": D("11904")}
    assert aux.medido_por_regiao["database"] == {"us-east-1": D("1357.06")}
    assert aux.banco_por_motor == {"postgresql": D("11904")}


@pytest.mark.asyncio
async def test_agregador_separa_as_partes_do_banco() -> None:
    linhas = [
        _linha("NodeUsage:cache.t4g.medium", "47.45", "730", None, servico="AmazonElastiCache"),
        _linha("RDS:Multi-AZ-GP3-Storage", "23", "100", "MySQL", unidade="GB-Mo"),
        _linha("RDS:ChargedBackupUsage", "19", "200", "PostgreSQL", unidade="GB-Mo"),
        _linha("RDS:Multi-AZ-GP3-PIOPS", "40", "3000", "MySQL", unidade="IOPS-Mo"),
        # IO rotulado como GB-mês: US$ 0,0000002 por "GB" não é armazenamento
        _linha("Aurora:IO-OptimizedStorageUsage", "0.24", "1191126", None, unidade="GB-Mo"),
    ]
    aux = (await BillingAggregator().aggregate(linhas, "aws")).auxiliary
    assert aux.banco_partes == {
        "cache:ate_4gb": {"quantidade": D("2255.70"), "custo": D("47.45")},
        "armazenamento:mysql:ha": {"quantidade": D(100), "custo": D(23)},
        "backup:postgresql": {"quantidade": D(200), "custo": D(19)},
        "sem_equivalente": {"quantidade": D(0), "custo": D(40)},
        "outros": {"quantidade": D(0), "custo": D("0.24")},
    }
    assert "database" not in aux.quantidade_por_regiao


@pytest.mark.asyncio
async def test_cost_explorer_horas_do_uso_coberto_e_reserva_sem_horas() -> None:
    """Cost Explorer (sem atributos): a taxa da reserva vem sem horas e o uso coberto, a
    custo zero, traz as horas. Contar só as linhas pagas perdia as horas cobertas; o
    storage vem sem unidade e é medido pelo nome, sob a trava de preço plausível."""
    sem_atributos = [
        _linha("HeavyUsage:db.r8g.4xl", "1357.06", None, None),
        _linha("Multi-AZUsage:db.r8g.4xl", "0", "744", None, unidade=""),
        _linha("InstanceUsage:db.t3.micro", "13.39", "744", None, unidade=""),
        _linha("RDS:Multi-AZ-GP3-Storage", "23", "100", None, unidade=""),
    ]
    aux = (await BillingAggregator().aggregate(sem_atributos, "aws")).auxiliary
    # 744 h x 16 vCPU x 2 réplicas + 744 h x 2 vCPU
    assert aux.quantidade_por_regiao["database"] == {"us-east-1": D("25296")}
    assert aux.medido_por_regiao["database"] == {"us-east-1": D("1370.45")}
    assert aux.banco_partes == {
        "armazenamento::ha": {"quantidade": D(100), "custo": D(23)},
    }
