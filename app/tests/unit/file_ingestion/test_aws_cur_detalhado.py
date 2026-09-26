"""Parser CUR contra a semântica documentada pela AWS (E3 do PLANO_CUR_BIGQUERY).

Os casos pequenos reproduzem os exemplos da própria documentação da AWS; o
caso grande é o mock derivado do bill real, cujos totais são conhecidos.
"""

from __future__ import annotations

import csv
import gzip
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.infrastructure.file_ingestion.detector import detect_parser
from app.infrastructure.file_ingestion.loaders import iter_tabular, read_tabular
from app.infrastructure.file_ingestion.parsers import AWSParser

_BASE = {
    "identity_line_item_id": "",
    "identity_time_interval": "2019-10-10T00:00:00Z/2019-10-10T01:00:00Z",
    "bill_billing_period_start_date": "2019-10-01T00:00:00Z",
    "bill_invoice_id": "INV-1",
    "bill_payer_account_id": "999999999999",
    "line_item_usage_account_id": "5555555555555",
    "line_item_line_item_type": "Usage",
    "line_item_product_code": "AmazonEC2",
    "line_item_usage_type": "BoxUsage:t3.nano",
    "line_item_operation": "RunInstances",
    "line_item_resource_id": "i-0abc",
    "line_item_usage_start_date": "2019-10-10T00:00:00Z",
    "line_item_usage_end_date": "2019-10-10T01:00:00Z",
    "line_item_usage_amount": "1",
    "line_item_unblended_cost": "0",
    "line_item_currency_code": "USD",
    "pricing_unit": "Hrs",
    "product_region_code": "us-east-1",
    "product_instance_type": "t3.nano",
    "product": json.dumps({"vcpu": "2", "memory": "0.5 GiB", "operating_system": "Linux"}),
    "resource_tags": json.dumps({"user_env": "prod", "aws_createdBy": "terraform"}),
    "savings_plan_savings_plan_effective_cost": "",
    "savings_plan_total_commitment_to_date": "",
    "savings_plan_used_commitment": "",
    "savings_plan_savings_plan_a_r_n": "",
    "reservation_effective_cost": "",
    "reservation_unused_recurring_fee": "",
    "reservation_unused_amortized_upfront_fee_for_billing_period": "",
    "reservation_reservation_a_r_n": "",
}


def _cur(tmp_path: Path, linhas: list[dict], *, sem: tuple[str, ...] = ()) -> Path:
    colunas = [c for c in _BASE if c not in sem]
    destino = tmp_path / "cur.csv"
    with destino.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=colunas, extrasaction="ignore")
        w.writeheader()
        for i, extra in enumerate(linhas):
            linha = {**_BASE, "identity_line_item_id": f"li-{i}", **extra}
            w.writerow(linha)
    return destino


def _parse(caminho: Path, user_id, job_id) -> pd.DataFrame:
    return AWSParser(user_id=user_id, job_id=job_id).parse(caminho)


def _soma(serie) -> Decimal:
    return sum(serie, Decimal("0"))


# ── exemplo da documentação: Savings Plan (cur-sp.html) ─────────────────────


def _exemplo_sp() -> list[dict]:
    comum = {
        "savings_plan_savings_plan_a_r_n": (
            "arn:aws:savingsplans::5555555555555:savingsplan/bc1d08fd"
        )
    }
    return [
        {
            **comum,
            "line_item_line_item_type": "SavingsPlanCoveredUsage",
            "line_item_usage_amount": "1",
            "line_item_unblended_cost": "0.0052",
            "savings_plan_savings_plan_effective_cost": "0.0026",
        },
        {
            **comum,
            "line_item_line_item_type": "SavingsPlanCoveredUsage",
            "line_item_resource_id": "i-0def",
            "line_item_usage_amount": "0.5",
            "line_item_unblended_cost": "0.0026",
            "savings_plan_savings_plan_effective_cost": "0.0013",
        },
        {
            **comum,
            "line_item_line_item_type": "SavingsPlanNegation",
            "line_item_resource_id": "",
            "line_item_usage_amount": "-1.5",
            "line_item_unblended_cost": "-0.0078",
        },
        {
            **comum,
            "line_item_line_item_type": "SavingsPlanRecurringFee",
            "line_item_product_code": "ComputeSavingsPlans",
            "line_item_usage_type": "ComputeSP:1yrNoUpfront",
            "line_item_resource_id": "",
            "product_instance_type": "",
            "product": "{}",
            "line_item_unblended_cost": "0.01",
            "savings_plan_total_commitment_to_date": "0.01",
            "savings_plan_used_commitment": "0.0039",
        },
    ]


def test_savings_plan_do_exemplo_da_aws(tmp_path, user_id, job_id) -> None:
    df = _parse(_cur(tmp_path, _exemplo_sp()), user_id, job_id)
    por_recurso = {r["resource_id"]: r for r in df.to_dict("records") if r["resource_id"]}

    # Custo efetivo da instância é o do SP, não o sob demanda nem zero.
    assert por_recurso["i-0abc"]["cost"] == Decimal("0.0026")
    assert por_recurso["i-0def"]["cost"] == Decimal("0.0013")
    assert por_recurso["i-0abc"]["cost_billed"] == Decimal("0.0052")
    assert por_recurso["i-0abc"]["charge_kind"] == "usage"

    compromisso = df[df["charge_kind"] == "commitment"]
    # Negação some no amortizado; a taxa fica só com a parte NÃO usada.
    assert _soma(compromisso["cost"]) == Decimal("0.0061")
    # O dinheiro se conserva: amortizado e faturado fecham no mesmo total.
    assert _soma(df["cost"]) == _soma(df["cost_billed"]) == Decimal("0.0100")


def test_sem_colunas_de_sp_a_linha_fica_na_base_faturada(tmp_path, user_id, job_id) -> None:
    sem = (
        "savings_plan_savings_plan_effective_cost",
        "savings_plan_total_commitment_to_date",
        "savings_plan_used_commitment",
    )
    df = _parse(_cur(tmp_path, _exemplo_sp(), sem=sem), user_id, job_id)
    coberta = df[df["resource_id"] == "i-0abc"].iloc[0]
    assert coberta["cost"] == Decimal("0.0052")
    assert coberta["attributes"]["cost_basis"] == "billed"


# ── reserva ─────────────────────────────────────────────────────────────────


def test_reserva_usa_custo_efetivo_e_so_a_parte_ociosa_da_taxa(tmp_path, user_id, job_id) -> None:
    arn = "arn:aws:rds:us-east-1:5555555555555:ri:abc"
    linhas = [
        {
            "line_item_line_item_type": "DiscountedUsage",
            "line_item_product_code": "AmazonRDS",
            "line_item_usage_type": "InstanceUsage:db.t4g.medium",
            "line_item_resource_id": "arn:aws:rds:us-east-1:5555555555555:db:pg-1",
            "product_instance_type": "db.t4g.medium",
            "line_item_usage_amount": "24",
            "line_item_unblended_cost": "0",
            "reservation_effective_cost": "1.20",
            "reservation_reservation_a_r_n": arn,
        },
        {
            "line_item_line_item_type": "RIFee",
            "line_item_product_code": "AmazonRDS",
            "line_item_usage_type": "HeavyUsage:db.t4g.medium",
            "line_item_resource_id": "",
            "line_item_unblended_cost": "36.50",
            "reservation_unused_recurring_fee": "1.50",
            "reservation_unused_amortized_upfront_fee_for_billing_period": "0",
            "reservation_reservation_a_r_n": arn,
        },
        {
            # Taxa inicial de reserva: no amortizado ela já está no uso.
            "line_item_line_item_type": "Fee",
            "line_item_product_code": "AmazonRDS",
            "line_item_usage_type": "HeavyUsage:db.t4g.medium",
            "line_item_resource_id": "",
            "line_item_unblended_cost": "100",
            "reservation_reservation_a_r_n": arn,
        },
    ]
    df = _parse(_cur(tmp_path, linhas), user_id, job_id)
    uso = df[df["charge_kind"] == "usage"].iloc[0]
    assert uso["cost"] == Decimal("1.20")
    assert uso["resource_type"] == "db.t4g.medium"
    compromisso = df[df["charge_kind"] == "commitment"]
    assert _soma(compromisso["cost"]) == Decimal("1.50")


# ── natureza, região, conta, tags, atributos ───────────────────────────────


@pytest.mark.parametrize(
    ("tipo", "natureza"),
    [
        ("Usage", "usage"),
        ("Tax", "tax"),
        ("Credit", "credit"),
        ("Refund", "refund"),
        ("Discount", "credit"),
        ("BundledDiscount", "credit"),
        ("EdpDiscount", "credit"),
        ("FlateRateSubscription", "other"),
        ("TipoNovoQueAAwsInventar", "other"),
    ],
)
def test_natureza_por_tipo_de_linha(tmp_path, user_id, job_id, tipo, natureza) -> None:
    df = _parse(_cur(tmp_path, [{"line_item_line_item_type": tipo}]), user_id, job_id)
    assert df.iloc[0]["charge_kind"] == natureza


def test_regiao_conta_tags_e_atributos(tmp_path, user_id, job_id) -> None:
    df = _parse(_cur(tmp_path, [{"line_item_unblended_cost": "0.0052"}]), user_id, job_id)
    linha = df.iloc[0]
    assert linha["region"] == "us-east-1"
    # Conta que CONSUMIU, não a pagadora.
    assert linha["account_id"] == "5555555555555"
    assert linha["tags"] == {"env": "prod", "aws:createdBy": "terraform"}
    assert linha["usage_unit"] == "hours"
    assert linha["usage_type"] == "BoxUsage:t3.nano"
    atributos = linha["attributes"]
    assert atributos["instance_type"] == "t3.nano"
    assert atributos["vcpus"] == "2"
    assert atributos["memory_gib"] == "0.5"
    assert atributos["project_id"] == "5555555555555"
    assert atributos["operation"] == "RunInstances"


def test_regiao_cai_para_product_region_e_para_o_mapa(tmp_path, user_id, job_id) -> None:
    arquivo = _cur(
        tmp_path, [{"product": json.dumps({"region": "sa-east-1"})}], sem=("product_region_code",)
    )
    assert _parse(arquivo, user_id, job_id).iloc[0]["region"] == "sa-east-1"


def test_sem_conta_de_uso_cai_para_a_pagadora(tmp_path, user_id, job_id) -> None:
    arquivo = _cur(tmp_path, [{}], sem=("line_item_usage_account_id",))
    assert _parse(arquivo, user_id, job_id).iloc[0]["account_id"] == "999999999999"


# ── consolidação e deduplicação ─────────────────────────────────────────────


def test_consolida_o_mesmo_recurso_em_dias_diferentes(tmp_path, user_id, job_id) -> None:
    linhas = [
        {
            "line_item_usage_start_date": f"2019-10-{d:02d}T00:00:00Z",
            "line_item_usage_end_date": f"2019-10-{d + 1:02d}T00:00:00Z",
            "identity_time_interval": f"2019-10-{d:02d}/x",
            "line_item_usage_amount": "24",
            "line_item_unblended_cost": "0.1248",
        }
        for d in (10, 11, 12)
    ]
    df = _parse(_cur(tmp_path, linhas), user_id, job_id)
    assert len(df) == 1
    linha = df.iloc[0]
    assert linha["cost"] == Decimal("0.3744")
    assert linha["usage_amount"] == Decimal("72")
    assert linha["attributes"]["active_days"] == 3
    assert linha["attributes"]["source_lines"] == 3
    assert linha["attributes"]["first_usage"].startswith("2019-10-10")
    assert linha["attributes"]["last_usage"].startswith("2019-10-13")
    assert linha["raw_payload"]["linhas_origem"] == 3


def test_linha_repetida_conta_uma_vez(tmp_path, user_id, job_id) -> None:
    linha = {"identity_line_item_id": "li-x", "line_item_unblended_cost": "1.00"}
    arquivo = tmp_path / "dup.csv"
    with arquivo.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_BASE))
        w.writeheader()
        w.writerow({**_BASE, **linha})
        w.writerow({**_BASE, **linha})
    df = _parse(arquivo, user_id, job_id)
    assert _soma(df["cost"]) == Decimal("1.00")


def test_windows_e_linux_nao_se_misturam(tmp_path, user_id, job_id) -> None:
    linhas = [
        {"line_item_operation": "RunInstances", "line_item_unblended_cost": "1"},
        {"line_item_operation": "RunInstances:0002", "line_item_unblended_cost": "2"},
    ]
    assert len(_parse(_cur(tmp_path, linhas), user_id, job_id)) == 2


# ── CUR legado ──────────────────────────────────────────────────────────────


def test_cur_legado_com_colunas_barra(tmp_path, user_id, job_id) -> None:
    arquivo = tmp_path / "legado.csv"
    colunas = [
        "identity/LineItemId",
        "bill/BillingPeriodStartDate",
        "bill/PayerAccountId",
        "bill/InvoiceId",
        "lineItem/UsageAccountId",
        "lineItem/LineItemType",
        "lineItem/ProductCode",
        "lineItem/UsageType",
        "lineItem/ResourceId",
        "lineItem/UsageStartDate",
        "lineItem/UsageEndDate",
        "lineItem/UsageAmount",
        "lineItem/UnblendedCost",
        "lineItem/CurrencyCode",
        "pricing/unit",
        "product/region",
        "product/instanceType",
        "product/vcpu",
        "product/memory",
        "resourceTags/user:Name",
        "resourceTags/aws:createdBy",
    ]
    with arquivo.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(colunas)
        w.writerow(
            [
                "li-1",
                "2026-03-01T00:00:00Z",
                "111",
                "INV",
                "222",
                "Usage",
                "AmazonEC2",
                "BoxUsage:m7g.2xlarge",
                "i-1",
                "2026-03-01T00:00:00Z",
                "2026-03-02T00:00:00Z",
                "24",
                "7.8336",
                "USD",
                "Hrs",
                "us-east-1",
                "m7g.2xlarge",
                "8",
                "32 GiB",
                "web-1",
                "someone",
            ]
        )
    parser = AWSParser(user_id=user_id, job_id=job_id)
    df = parser.parse(arquivo)
    assert parser.get_schema_version() == "AWS_CUR_LEGACY"
    linha = df.iloc[0]
    assert linha["region"] == "us-east-1"
    assert linha["account_id"] == "222"
    assert linha["tags"] == {"Name": "web-1", "aws:createdBy": "someone"}
    assert linha["attributes"]["vcpus"] == "8"
    assert linha["attributes"]["memory_gib"] == "32"


# ── formatos: blocos e Parquet ──────────────────────────────────────────────


def test_leitura_em_blocos_da_o_mesmo_que_inteira(tmp_path) -> None:
    arquivo = _cur(tmp_path, [{"line_item_unblended_cost": str(i)} for i in range(23)])
    blocos = list(iter_tabular(arquivo, tamanho=5))
    assert len(blocos) == 5
    juntos = pd.concat(blocos, ignore_index=True)
    inteiro = read_tabular(arquivo)
    assert (
        juntos["line_item_unblended_cost"].tolist() == inteiro["line_item_unblended_cost"].tolist()
    )


def test_parquet_da_o_mesmo_que_csv(tmp_path, user_id, job_id) -> None:
    csv_path = _cur(tmp_path, _exemplo_sp())
    tabela = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    # Como o CUR entrega: custo como double, mapa como map<string,string>.
    tabela["line_item_unblended_cost"] = tabela["line_item_unblended_cost"].astype(float)
    parquet_path = tmp_path / "cur.parquet"
    colunas = {
        c: pa.array(tabela[c].tolist())
        for c in tabela.columns
        if c not in ("product", "resource_tags")
    }
    colunas["product"] = pa.array(
        [list(json.loads(p).items()) for p in tabela["product"]],
        type=pa.map_(pa.string(), pa.string()),
    )
    colunas["resource_tags"] = pa.array(
        [list(json.loads(p).items()) for p in tabela["resource_tags"]],
        type=pa.map_(pa.string(), pa.string()),
    )
    pq.write_table(pa.table(colunas), parquet_path)

    assert isinstance(detect_parser(parquet_path, user_id, job_id), AWSParser)
    via_csv = _parse(csv_path, user_id, job_id).sort_values("line_hash").reset_index(drop=True)
    via_parquet = (
        _parse(parquet_path, user_id, job_id).sort_values("line_hash").reset_index(drop=True)
    )
    assert via_parquet["cost"].tolist() == via_csv["cost"].tolist()
    assert via_parquet["tags"].tolist() == via_csv["tags"].tolist()
    assert via_parquet["attributes"].tolist() == via_csv["attributes"].tolist()


# ── o mock inteiro ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mock_parseado(mocks_billing):
    parser = AWSParser(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        job_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    return parser.parse(mocks_billing.cur)


def test_mock_total_amortizado_bate_com_o_bill_real(mock_parseado) -> None:
    # US$ 104.077,14 do bill real, dos quais 11.973,66 são imposto.
    total = _soma(mock_parseado["cost"])
    assert abs(total - Decimal("104077.1434133795")) < Decimal("0.000001")
    sem_imposto = _soma(mock_parseado.loc[mock_parseado["charge_kind"] != "tax", "cost"])
    assert abs(sem_imposto - Decimal("92103.4834133795")) < Decimal("0.000001")
    assert _soma(mock_parseado["cost_billed"]) == total


def test_mock_consolidado_e_rastreavel(mock_parseado, mocks_billing) -> None:
    with gzip.open(mocks_billing.cur, "rt") as fh:
        origem = sum(1 for _ in fh) - 1
    assert len(mock_parseado) < origem  # consolidou
    assert sum(r["linhas_origem"] for r in mock_parseado["raw_payload"]) == origem


def test_mock_nenhuma_instancia_com_custo_zero(mock_parseado) -> None:
    """No faturado, 5 tipos de EC2 somam zero (cobertos pelo SP). Amortizado, nenhum."""
    ec2 = mock_parseado[
        (mock_parseado["service"] == "AmazonEC2")
        & mock_parseado["usage_type"].fillna("").str.contains("BoxUsage")
    ]
    por_tipo = ec2.groupby("resource_type")["cost"].apply(_soma)
    assert (por_tipo > 0).all()
    faturado = ec2.groupby("resource_type")["cost_billed"].apply(_soma)
    # A cobertura aparece no faturado como sob demanda + negação à parte; por
    # recurso, o faturado de um tipo 100% coberto é o próprio sob demanda.
    assert (faturado >= 0).all()


def test_mock_instancias_tem_shape_medido_e_regiao(mock_parseado) -> None:
    # Só `usage`: a SavingsPlanNegation tem o tipo de uso da instância, mas não
    # é recurso nem traz atributos de produto.
    ec2 = mock_parseado[
        (mock_parseado["charge_kind"] == "usage")
        & (mock_parseado["service"] == "AmazonEC2")
        & mock_parseado["usage_type"].fillna("").str.contains("BoxUsage")
    ]
    assert ec2["resource_id"].nunique() == 321
    assert all("vcpus" in a and "memory_gib" in a for a in ec2["attributes"])
    assert set(ec2["region"]) == {"us-east-1", "sa-east-1"}
    assert (mock_parseado["usage_unit"] != "Hrs").all()
