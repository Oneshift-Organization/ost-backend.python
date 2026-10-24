"""Parsers de Azure e OCI contra o formato documentado (E9 do PLANO_CUR_BIGQUERY)."""

from __future__ import annotations

import csv
import gzip
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pandas as pd
import pytest

from app.domain.services.categoria_por_uso import quantidade_por_uso, tipo_por_uso
from app.domain.services.reclassificacao_por_uso import reclassificar
from app.infrastructure.file_ingestion.parsers import AzureParser, OCIParser

_USER = UUID("11111111-1111-1111-1111-111111111111")
_JOB = UUID("22222222-2222-2222-2222-222222222222")


def _soma(serie) -> Decimal:
    return sum(serie, Decimal("0"))


# ── mocks inteiros ──────────────────────────────────────────────────────────


def test_mock_azure_conserva_e_mede(mocks_billing) -> None:
    df = AzureParser(user_id=_USER, job_id=_JOB).parse(mocks_billing.azure)
    esperado = mocks_billing.resumo_azure
    assert abs(_soma(df["cost"]) - esperado.total) < Decimal("0.000001")
    assert "None" not in set(df["service"])  # ServiceName ausente no EA
    vms = df[df["attributes"].apply(lambda a: a.get("capacity_unit") == "instance")]
    vcpu_horas = _soma(
        linha.usage_amount * Decimal(linha.attributes["vcpus"]) for linha in vms.itertuples()
    )
    assert vcpu_horas == esperado.vcpu_horas
    discos = df[df["usage_unit"] == "gibibyte month"]
    assert _soma(discos["usage_amount"]) > 0
    assert set(df["charge_kind"]) >= {"usage", "commitment", "adjustment"}
    assert all(tags for tags in vms["tags"])  # formato sem chaves externas


def test_mock_oci_conserva_com_a_correcao(mocks_billing) -> None:
    df = OCIParser(user_id=_USER, job_id=_JOB).parse(mocks_billing.oci)
    esperado = mocks_billing.resumo_oci
    # A correção SUBSTITUI a original: somar as duas daria a original a mais.
    assert abs(_soma(df["cost"]) - esperado.total) < Decimal("0.000001")
    assert df["is_correction"].any()
    ocpu = df[df["attributes"].apply(lambda a: a.get("capacity_unit") == "vcpu")]
    vcpu_horas = _soma(
        linha.usage_amount * Decimal(linha.attributes["vcpus_per_unit"])
        for linha in ocpu.itertuples()
    )
    assert vcpu_horas == esperado.vcpu_horas
    assert set(df["account_id"]) == {"ocid1.tenancy.oc1..aaaaaaaaoneshiftdemo"}


# ── Azure: casos pontuais ───────────────────────────────────────────────────

_AZ_COLUNAS = [
    "SubscriptionId",
    "SubscriptionName",
    "Date",
    "MeterCategory",
    "MeterSubCategory",
    "MeterName",
    "Quantity",
    "CostInBillingCurrency",
    "ResourceLocation",
    "ResourceId",
    "Tags",
    "AdditionalInfo",
    "UnitOfMeasure",
    "ChargeType",
    "PricingModel",
    "BillingCurrency",
    "PayGPrice",
]


def _azure(tmp_path: Path, linhas: list[dict], extra_colunas: tuple[str, ...] = ()) -> Path:
    caminho = tmp_path / "azure.csv"
    colunas = _AZ_COLUNAS + list(extra_colunas)
    with caminho.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=colunas)
        w.writeheader()
        for extra in linhas:
            w.writerow(
                {
                    **dict.fromkeys(colunas, ""),
                    "SubscriptionId": "sub-1",
                    "Date": "03/15/2026",
                    "MeterCategory": "Virtual Machines",
                    "MeterSubCategory": "Dsv5 Series",
                    "MeterName": "D4s v5",
                    "Quantity": "24",
                    "CostInBillingCurrency": "4.608",
                    "ResourceLocation": "East US",
                    "ResourceId": "/subscriptions/sub-1/VM-A",
                    "UnitOfMeasure": "1 Hour",
                    "ChargeType": "Usage",
                    "PricingModel": "OnDemand",
                    "BillingCurrency": "USD",
                    **extra,
                }
            )
    return caminho


def _parse_azure(caminho: Path) -> pd.DataFrame:
    return AzureParser(user_id=_USER, job_id=_JOB).parse(caminho)


def test_azure_tags_sem_chaves_externas(tmp_path) -> None:
    df = _parse_azure(_azure(tmp_path, [{"Tags": '"env": "prod","app": "checkout"'}]))
    assert df.iloc[0]["tags"] == {"env": "prod", "app": "checkout"}


def test_azure_servico_cai_na_categoria_sem_service_name(tmp_path) -> None:
    df = _parse_azure(_azure(tmp_path, [{}]))
    assert df.iloc[0]["service"] == "Virtual Machines"


def test_azure_regiao_normalizada_e_recurso_minusculo(tmp_path) -> None:
    linha = _parse_azure(_azure(tmp_path, [{}])).iloc[0]
    assert linha["region"] == "eastus"
    assert linha["resource_id"] == "/subscriptions/sub-1/vm-a"


def test_azure_bloco_de_preco_nao_multiplica_a_quantidade(tmp_path) -> None:
    # Export real (23/10): um servidor ligado o dia todo vem com 24 em "1000 Hours",
    # e custo ÷ quantidade dá o preço por hora. São 24 horas, não 24.000.
    linha = {
        "UnitOfMeasure": "1000 Hours",
        "Quantity": "24",
        "CostInBillingCurrency": "0.96",
        "PayGPrice": "0.04",
    }
    df = _parse_azure(_azure(tmp_path, [linha]))
    assert df.iloc[0]["usage_unit"] == "hours"
    assert df.iloc[0]["usage_amount"] == Decimal("24")
    assert Decimal(df.iloc[0]["attributes"]["on_demand_cost"]) == Decimal("0.96")


@pytest.mark.parametrize(
    ("local", "regiao"),
    [
        ("East US", "eastus"),
        ("brazilsouth", "brazilsouth"),
        ("BR South", "brazilsouth"),
        ("BRSouth", "brazilsouth"),
        ("US East", "eastus"),
        ("US West 2", "westus2"),
        ("EU West", "westeurope"),
        ("AP Southeast", "southeastasia"),
        ("Unassigned", None),
        ("Unknown", None),
        ("Intercontinental", None),
        ("Zone 1", None),
    ],
)
def test_azure_regiao_no_nome_antigo_vira_a_do_arm(tmp_path, local, regiao) -> None:
    valor = _parse_azure(_azure(tmp_path, [{"ResourceLocation": local}])).iloc[0]["region"]
    assert (None if pd.isna(valor) else valor) == regiao


def test_azure_vm_com_tamanho_e_vcpu_medido(tmp_path) -> None:
    info = '{"ServiceType": "Standard_D4s_v5", "VCPUs": 4, "UsageType": "ComputeHR"}'
    atributos = _parse_azure(_azure(tmp_path, [{"AdditionalInfo": info}])).iloc[0]["attributes"]
    assert atributos["instance_type"] == "Standard_D4s_v5"
    assert atributos["instance_family"] == "DSV5"
    assert atributos["vcpus"] == "4"
    assert atributos["capacity_unit"] == "instance"


@pytest.mark.parametrize(
    ("charge", "modelo", "natureza"),
    [
        ("Usage", "OnDemand", "usage"),
        ("Usage", "Reservation", "usage"),
        ("Purchase", "Reservation", "commitment"),
        ("Purchase", "SavingsPlan", "commitment"),
        ("UnusedReservation", "Reservation", "commitment"),
        ("UnusedSavingsPlan", "SavingsPlan", "commitment"),
        ("Purchase", "OnDemand", "other"),
        ("Refund", "Reservation", "refund"),
        ("RoundingAdjustment", "", "adjustment"),
    ],
)
def test_azure_natureza(tmp_path, charge, modelo, natureza) -> None:
    df = _parse_azure(_azure(tmp_path, [{"ChargeType": charge, "PricingModel": modelo}]))
    assert df.iloc[0]["charge_kind"] == natureza


def test_azure_disco_por_tier_vira_gb_mes_e_sob_demanda_nao_infla(tmp_path) -> None:
    linha = {
        "MeterCategory": "Storage",
        "MeterSubCategory": "Premium SSD Managed Disks",
        "MeterName": "P30 LRS Disk",
        "Quantity": "1",
        "UnitOfMeasure": "1/Month",
        "CostInBillingCurrency": "135.17",
        "PayGPrice": "135.17",
    }
    df = _parse_azure(_azure(tmp_path, [linha]))
    assert df.iloc[0]["usage_unit"] == "gibibyte month"
    assert df.iloc[0]["usage_amount"] == Decimal("1024")
    # Preço de lista é por disco-mês: sob demanda = 135,17, não 135,17 x 1024.
    assert Decimal(df.iloc[0]["attributes"]["on_demand_cost"]) == Decimal("135.17")


def test_azure_versao_do_schema(tmp_path) -> None:
    parser = AzureParser(user_id=_USER, job_id=_JOB)
    parser.parse(_azure(tmp_path, [{}], extra_colunas=("costInUsd",)))
    assert parser.get_schema_version() == "AZURE_MCA"
    parser.parse(_azure(tmp_path, [{}]))
    assert parser.get_schema_version() == "AZURE_EA"


# ── OCI: casos pontuais ─────────────────────────────────────────────────────

_OCI_COLUNAS = [
    "lineItem/referenceNo",
    "lineItem/tenantId",
    "lineItem/intervalUsageStart",
    "lineItem/intervalUsageEnd",
    "product/service",
    "product/compartmentId",
    "product/compartmentName",
    "product/region",
    "product/resourceId",
    "product/Description",
    "usage/billedQuantity",
    "cost/myCost",
    "cost/currencyCode",
    "cost/billingUnitReadable",
    "cost/productSku",
    "lineItem/isCorrection",
    "lineItem/backReferenceNo",
]


def _oci(tmp_path: Path, linhas: list[dict]) -> Path:
    caminho = tmp_path / "oci.csv.gz"
    with gzip.open(caminho, "wt", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_OCI_COLUNAS)
        w.writeheader()
        for i, extra in enumerate(linhas):
            w.writerow(
                {
                    **dict.fromkeys(_OCI_COLUNAS, ""),
                    "lineItem/referenceNo": f"ref-{i}",
                    "lineItem/tenantId": "ocid1.tenancy.x",
                    "lineItem/intervalUsageStart": "2026-03-01T00:00Z",
                    "lineItem/intervalUsageEnd": "2026-03-02T00:00Z",
                    "product/service": "COMPUTE",
                    "product/compartmentId": "ocid1.compartment.prod",
                    "product/compartmentName": "producao",
                    "product/region": "sa-saopaulo-1",
                    "product/resourceId": "ocid1.instance.a",
                    "product/Description": "Compute - Standard - E4 - OCPU",
                    "usage/billedQuantity": "96",
                    "cost/myCost": "2.4",
                    "cost/currencyCode": "USD",
                    "cost/billingUnitReadable": "OCPU Hours",
                    "lineItem/isCorrection": "false",
                    **extra,
                }
            )
    return caminho


def _parse_oci(caminho: Path) -> pd.DataFrame:
    return OCIParser(user_id=_USER, job_id=_JOB).parse(caminho)


def test_oci_colunas_reais_de_conta_e_compartimento(tmp_path) -> None:
    linha = _parse_oci(_oci(tmp_path, [{}])).iloc[0]
    assert linha["account_id"] == "ocid1.tenancy.x"
    assert linha["account_name"] == "producao"
    assert linha["attributes"]["project_id"] == "ocid1.compartment.prod"


@pytest.mark.parametrize(
    ("descricao", "unidade", "capacidade", "por_unidade"),
    [
        ("Compute - Standard - E4 - OCPU", "OCPU Hours", "vcpu", "2"),
        ("Compute - Ampere A1 - OCPU", "OCPU Hours", "vcpu", "1"),
        ("Compute - Standard - E4 - Memory", "GB Hours", "gib", None),
    ],
)
def test_oci_ocpu_e_memoria(tmp_path, descricao, unidade, capacidade, por_unidade) -> None:
    linha = _parse_oci(
        _oci(tmp_path, [{"product/Description": descricao, "cost/billingUnitReadable": unidade}])
    ).iloc[0]
    assert linha["attributes"]["capacity_unit"] == capacidade
    assert linha["attributes"].get("vcpus_per_unit") == por_unidade
    assert linha["usage_unit"] == ("hours" if capacidade == "vcpu" else "gibibyte hour")


@pytest.mark.parametrize("correcao_primeiro", [True, False])
def test_oci_correcao_substitui_em_qualquer_ordem(tmp_path, correcao_primeiro) -> None:
    original = {"lineItem/referenceNo": "orig", "cost/myCost": "10"}
    correcao = {
        "lineItem/referenceNo": "corr",
        "cost/myCost": "4",
        "lineItem/isCorrection": "true",
        "lineItem/backReferenceNo": "orig",
    }
    linhas = [correcao, original] if correcao_primeiro else [original, correcao]
    df = _parse_oci(_oci(tmp_path, linhas))
    assert _soma(df["cost"]) == Decimal("4")


def test_azure_plano_do_app_service_vira_capacidade(tmp_path) -> None:
    """PLANO_CONFERENCIA_DE_SERVICOS §7: o plano tem tamanho publicado; o resto não."""
    base = {"MeterCategory": "Azure App Service", "UnitOfMeasure": "1 Hour", "Quantity": "730"}
    df = _parse_azure(
        _azure(
            tmp_path,
            [
                {**base, "MeterSubCategory": "Premium v3 Plan", "MeterName": "P1 v3 App"},
                {**base, "MeterSubCategory": "IP SSL", "MeterName": "IP SSL Connection"},
            ],
        )
    )
    plano, ssl = (df.iloc[i]["attributes"] for i in range(2))
    assert plano["instance_type"] == "AppService_P1v3"
    assert plano["instance_family"] == "APPSERVICE_PV3"
    assert plano["capacity_unit"] == "instance"
    assert "instance_type" not in ssl and "capacity_unit" not in ssl


@pytest.mark.parametrize(
    ("linha", "categoria", "esperado"),
    [
        # O relatório dá a quantidade na unidade da lista de preços e o parser multiplica
        # pelo número dela. A regra não pode multiplicar de novo: a chamada saía 1 milhão de
        # vezes maior (PLANO_CONFERENCIA_DE_SERVICOS §8)
        (
            (
                "API_GATEWAY",
                "API Gateway  - 1,000,000 API Calls",
                "5",
                "1,000,000 API Calls Per Month",
            ),
            "api_requests",
            "500",
        ),
        (
            ("FUNCTIONS", "Oracle Functions - Execution Time", "36", "10,000 GB Memory-Seconds"),
            "function_compute",
            "100",
        ),
        # "1MIL" não é separado pelo parser: a quantidade continua em milhões
        (
            ("FUNCTIONS", "Oracle Functions - Invocations", "3", "1MIL Function Invocations"),
            "function_requests",
            "300",
        ),
        (("NETWORK", "OCI - FastConnect 1 Gbps", "744", "Port Hour"), "dedicated_link_1g", "744"),
    ],
)
def test_oci_quantidade_do_parser_chega_na_unidade_da_categoria(
    tmp_path, linha, categoria, esperado
) -> None:
    servico, descricao, quantidade, unidade = linha
    lida = _parse_oci(
        _oci(
            tmp_path,
            [
                {
                    "product/service": servico,
                    "product/Description": descricao,
                    "usage/billedQuantity": quantidade,
                    "cost/billingUnitReadable": unidade,
                }
            ],
        )
    ).iloc[0]
    balde = reclassificar("oci", lida["service"], lida["usage_type"], "unclassified")
    tipo = tipo_por_uso(balde, "oci", lida["service"], lida["usage_type"])
    assert tipo.categoria == categoria
    medida = quantidade_por_uso("oci", tipo, Decimal(str(lida["usage_amount"])), lida["usage_unit"])
    assert medida == Decimal(esperado)
