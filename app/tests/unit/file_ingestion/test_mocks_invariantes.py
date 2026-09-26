"""Invariantes dos mocks de export detalhado (PLANO_CUR_BIGQUERY.md §6, etapa E2).

Os mocks substituem dado real que ainda não temos, então precisam ser
verificáveis contra os bills reais de onde saíram. Estes testes leem os arquivos
com pandas/json **direto**, sem o parser do projeto: um erro no parser não pode
esconder um erro no mock, nem o contrário.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal

import pandas as pd
import pytest

from app.tests.conftest import FIXTURES_INTEGRACAO, gerar_mocks

_FIXTURES = FIXTURES_INTEGRACAO
_TOL = Decimal("0.000001")


def _d(valor: str) -> Decimal:
    return Decimal(valor) if valor else Decimal("0")


# ── AWS ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def cur(mocks_billing) -> pd.DataFrame:
    df = pd.read_csv(mocks_billing.cur, dtype=str, keep_default_na=False)
    df["unb"] = df["line_item_unblended_cost"].map(_d)
    df["amort"] = [_amortizado(linha) for linha in df.to_dict(orient="records")]
    return df


@pytest.fixture(scope="module")
def bill_aws() -> pd.DataFrame:
    df = pd.read_csv(_FIXTURES / "aws_cost_explorer_full.csv", dtype=str, keep_default_na=False)
    df["custo"] = df["TotalCost"].map(_d)
    return df


def _amortizado(linha: dict) -> Decimal:
    """Fórmula da AWS (CUDOS), reescrita aqui de propósito — sem o parser."""
    tipo = linha["line_item_line_item_type"]
    if tipo == "SavingsPlanCoveredUsage":
        return _d(linha["savings_plan_savings_plan_effective_cost"])
    if tipo in ("SavingsPlanNegation", "SavingsPlanUpfrontFee"):
        return Decimal("0")
    if tipo == "SavingsPlanRecurringFee":
        return _d(linha["savings_plan_total_commitment_to_date"]) - _d(
            linha["savings_plan_used_commitment"]
        )
    if tipo == "DiscountedUsage":
        return _d(linha["reservation_effective_cost"])
    if tipo == "RIFee":
        return _d(linha["reservation_unused_amortized_upfront_fee_for_billing_period"]) + _d(
            linha["reservation_unused_recurring_fee"]
        )
    return _d(linha["line_item_unblended_cost"])


def test_cur_conserva_o_total_do_bill_real(cur, bill_aws) -> None:
    total_real = bill_aws.loc[bill_aws["RecordType"] == "StatementTotal", "custo"].iloc[0]
    assert abs(sum(cur["unb"]) - total_real) < _TOL


def test_cur_amortizado_fecha_com_o_faturado(cur) -> None:
    # Savings Plan e reservas do bill são No Upfront: a soma amortizada tem de
    # ser igual à faturada — só a DISTRIBUIÇÃO entre linhas muda.
    assert abs(sum(cur["amort"]) - sum(cur["unb"])) < _TOL


def test_cobertura_e_negacao_se_anulam(cur) -> None:
    cobertura = cur["line_item_line_item_type"].isin(
        ["SavingsPlanCoveredUsage", "SavingsPlanNegation"]
    )
    assert abs(sum(cur.loc[cobertura, "unb"])) < _TOL


def test_imposto_preservado(cur, bill_aws) -> None:
    imposto_real = sum(bill_aws.loc[bill_aws["ProductCode"] == "Tax", "custo"])
    assert sum(cur.loc[cur["line_item_line_item_type"] == "Tax", "unb"]) == imposto_real


def test_custo_por_tipo_de_uso_bate_com_o_bill(cur, bill_aws) -> None:
    """Cada linha do bill real reaparece com o mesmo custo faturado.

    A chave é (produto, tipo de uso, operação): `DataTransfer-Out-Bytes` existe
    em EC2, S3, RDS e Lambda com custos diferentes.
    """
    linhas = bill_aws[
        (bill_aws["RecordType"] == "PayerLineItem")
        & ~bill_aws["ProductCode"].isin(["Tax", "Savings Plans for AWS Compute usage"])
        & ~bill_aws["UsageType"].str.contains("HeavyUsage:")
    ]
    mock = cur[~cur["line_item_line_item_type"].isin(["Tax", "RIFee", "SavingsPlanRecurringFee"])]
    por_chave = mock.groupby(
        ["line_item_product_code", "line_item_usage_type", "line_item_operation"]
    )["unb"].apply(lambda s: sum(s, Decimal("0")))
    # O próprio bill repete a chave em linhas distintas (tarifas diferentes do
    # mesmo tipo de uso): compara soma com soma.
    reais = linhas.groupby(["ProductCode", "UsageType", "Operation"])["custo"].apply(
        lambda s: sum(s, Decimal("0"))
    )
    divergentes = [
        (chave, real, por_chave.get(chave))
        for chave, real in reais.items()
        if abs(por_chave.get(chave, Decimal("0")) - real) > _TOL
    ]
    assert divergentes == []


def test_nenhuma_instancia_passa_de_24h_por_dia(cur) -> None:
    inst = cur[
        (cur["product_instance_type"] != "")
        & cur["line_item_line_item_type"].isin(
            ["Usage", "SavingsPlanCoveredUsage", "DiscountedUsage"]
        )
        & cur["line_item_usage_type"].str.contains("Usage:")
        & ~cur["line_item_usage_type"].str.contains("ExtendedSupport|EBSOptimized|CPUCredits")
    ]
    horas = inst.groupby(["line_item_resource_id", "line_item_usage_start_date"])[
        "line_item_usage_amount"
    ].apply(lambda s: sum((_d(x) for x in s), Decimal("0")))
    assert max(horas) <= Decimal("24")


def test_nenhum_tipo_de_instancia_com_custo_amortizado_zero(cur) -> None:
    """O defeito que o mock existe para exercitar: no FATURADO há tipos com
    custo zero (cobertos por SP/RI); no AMORTIZADO, nenhum."""
    inst = cur[cur["product_instance_type"] != ""]
    faturado = inst.groupby("product_instance_type")["unb"].apply(lambda s: sum(s, Decimal("0")))
    amortizado = inst.groupby("product_instance_type")["amort"].apply(
        lambda s: sum(s, Decimal("0"))
    )
    assert (faturado <= 0).any(), "o mock precisa reproduzir o custo zero do faturado"
    assert (amortizado > 0).all()


def test_desconto_de_compromisso_plausivel(cur) -> None:
    """Custo efetivo nunca acima do sob demanda; desconto do SP entre 20% e 72%."""
    cobertas = cur[
        cur["line_item_line_item_type"].isin(["SavingsPlanCoveredUsage", "DiscountedUsage"])
    ]
    for linha in cobertas.itertuples():
        efetivo = _d(linha.savings_plan_savings_plan_effective_cost) or _d(
            linha.reservation_effective_cost
        )
        assert efetivo <= _d(linha.pricing_public_on_demand_cost) + _TOL
    sp = cur[cur["line_item_line_item_type"] == "SavingsPlanCoveredUsage"]
    for _arn, grupo in sp.groupby("savings_plan_savings_plan_a_r_n"):
        efetivo = sum(
            (_d(x) for x in grupo["savings_plan_savings_plan_effective_cost"]), Decimal("0")
        )
        sob_demanda = sum((_d(x) for x in grupo["pricing_public_on_demand_cost"]), Decimal("0"))
        assert Decimal("0.20") <= 1 - efetivo / sob_demanda <= Decimal("0.72")


def test_regiao_vem_do_prefixo_do_tipo_de_uso(cur) -> None:
    sae1 = cur[cur["line_item_usage_type"].str.startswith("SAE1-")]
    assert not sae1.empty
    assert set(sae1["product_region_code"]) == {"sa-east-1"}
    assert (cur["product_region_code"] != "").all()


def test_mapas_sao_json_e_instancia_traz_shape_medido(cur) -> None:
    for coluna in ("product", "resource_tags"):
        assert all(isinstance(json.loads(v), dict) for v in cur[coluna].unique())
    inst = cur[
        (cur["product_instance_type"] != "") & (cur["line_item_product_code"] == "AmazonEC2")
    ]
    produtos = [json.loads(p) for p in inst["product"].unique()]
    assert all("vcpu" in p and p["memory"].endswith(" GiB") for p in produtos)


# ── GCP ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def bq(mocks_billing) -> list[dict]:
    with gzip.open(mocks_billing.bigquery, "rt", encoding="utf-8") as fh:
        return [json.loads(linha) for linha in fh]


@pytest.fixture(scope="module")
def cost_table() -> list[dict[str, str]]:
    with (_FIXTURES / "gcp_cost_table_full.csv").open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh, delimiter=";") if r["Cost type"] != "Total"]


def _liquido(linha: dict) -> Decimal:
    return _d(linha["cost"]) + sum((_d(c["amount"]) for c in linha["credits"]), Decimal("0"))


def _custo_ct(r: dict[str, str]) -> Decimal:
    return _d(r["Unrounded Cost (R$)"].replace(",", ""))


def test_bigquery_conserva_o_cost_table(bq, cost_table) -> None:
    assert abs(sum(map(_liquido, bq)) - sum(map(_custo_ct, cost_table))) < _TOL


def test_bigquery_bate_por_projeto_e_sku(bq, cost_table) -> None:
    mock: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    real: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for linha in bq:
        mock[(linha["project"]["id"], linha["sku"]["description"])] += _liquido(linha)
    for r in cost_table:
        real[(r["Project ID"], r["SKU description"])] += _custo_ct(r)
    divergentes = [k for k in set(mock) | set(real) if abs(mock[k] - real[k]) > _TOL]
    assert divergentes == []


def test_bigquery_vm_nao_passa_de_24h_por_dia(bq) -> None:
    horas: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    cores: dict[tuple[str, str], int] = {}
    for linha in bq:
        rotulos = {x["key"]: x["value"] for x in linha["system_labels"]}
        if "compute.googleapis.com/cores" in rotulos and "Core" in linha["sku"]["description"]:
            chave = (linha["resource"]["name"], linha["usage_start_time"])
            horas[chave] += _d(linha["usage"]["amount_in_pricing_units"])
            cores[chave] = int(rotulos["compute.googleapis.com/cores"])
    assert horas, "o mock precisa ter VMs com system_labels"
    assert all(horas[k] <= cores[k] * 24 for k in horas)


def test_bigquery_unidade_base_difere_da_de_preco(bq) -> None:
    """A armadilha do export real: `usage.amount` em segundos, preço em horas."""
    core = next(x for x in bq if x["sku"]["description"].startswith("E2 Instance Core"))
    assert core["usage"]["unit"] == "seconds"
    assert core["usage"]["pricing_unit"] == "hour"
    assert _d(core["usage"]["amount"]) == _d(core["usage"]["amount_in_pricing_units"]) * 3600


def test_bigquery_traz_imposto_e_credito(bq) -> None:
    assert any(x["cost_type"] == "tax" for x in bq)
    tipos = {c["type"] for x in bq for c in x["credits"]}
    assert {"DISCOUNT", "SUSTAINED_USAGE_DISCOUNT"} <= tipos


def test_csv_achatado_espelha_o_ndjson(bq, mocks_billing) -> None:
    with mocks_billing.bigquery_csv.open(encoding="utf-8") as fh:
        linhas = list(csv.DictReader(fh))
    assert "service.description" in linhas[0]
    for plano, aninhado in zip(linhas, bq, strict=False):
        assert plano["sku.description"] == aninhado["sku"]["description"]
        assert _d(plano["cost"]) == _d(aninhado["cost"])


# ── reprodutibilidade ───────────────────────────────────────────────────────


def test_gerador_e_deterministico(tmp_path, mocks_billing) -> None:
    """Os mocks não são versionados; o que garante reprodutibilidade é gerar
    duas vezes e obter os mesmos bytes."""
    de_novo = gerar_mocks(tmp_path)
    for a, b in (
        (mocks_billing.cur, de_novo.cur),
        (mocks_billing.bigquery, de_novo.bigquery),
        (mocks_billing.bigquery_csv, de_novo.bigquery_csv),
    ):
        assert hashlib.sha256(a.read_bytes()).digest() == hashlib.sha256(b.read_bytes()).digest()
