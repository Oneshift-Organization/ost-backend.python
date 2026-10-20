"""Parser BigQuery contra a semântica documentada pelo Google (E4 do PLANO_CUR_BIGQUERY)."""

from __future__ import annotations

import gzip
import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from app.infrastructure.file_ingestion.detector import detect_parser
from app.infrastructure.file_ingestion.parsers import GCPParser

_USER = UUID("11111111-1111-1111-1111-111111111111")
_JOB = UUID("22222222-2222-2222-2222-222222222222")


def _soma(serie) -> Decimal:
    return sum(serie, Decimal("0"))


def _parse(caminho: Path):
    parser = GCPParser(user_id=_USER, job_id=_JOB)
    return parser, parser.parse(caminho)


def _linha(**extra) -> dict:
    base = {
        "billing_account_id": "0X0X0X",
        "service": {"id": "6F81", "description": "Compute Engine"},
        "sku": {"id": "abc", "description": "N2 Instance Core running in Americas"},
        "usage_start_time": "2026-05-01T00:00:00Z",
        "usage_end_time": "2026-05-01T01:00:00Z",
        "project": {"id": "proj-a", "name": "Projeto A"},
        "location": {"region": "us-central1"},
        "resource": {
            "name": "vm-1",
            "global_name": (
                "//compute.googleapis.com/projects/proj-a/zones/us-central1-a/instances/1"
            ),
        },
        "cost": "10",
        "currency": "USD",
        "usage": {
            "amount": "3600",
            "unit": "seconds",
            "amount_in_pricing_units": "1",
            "pricing_unit": "hour",
        },
        "credits": [],
        "invoice": {"month": "202605"},
        "cost_type": "regular",
    }
    base.update(extra)
    return base


def _ndjson(tmp_path: Path, linhas: list[dict], nome: str = "bq.json") -> Path:
    caminho = tmp_path / nome
    caminho.write_text("".join(json.dumps(x) + "\n" for x in linhas), encoding="utf-8")
    return caminho


# ── o mock inteiro ──────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mock(mocks_billing):
    return _parse(mocks_billing.bigquery)


def test_mock_detectado_e_marcado_como_detalhado(mock, mocks_billing) -> None:
    parser, _ = mock
    assert isinstance(detect_parser(mocks_billing.bigquery, _USER, _JOB), GCPParser)
    assert parser.get_schema_version() == "GCP_BILLING_DETAILED"


def test_mock_conserva_o_cost_table(mock) -> None:
    _, df = mock
    # Sem crédito promocional no bill: efetivo = faturado = total do Cost Table.
    assert abs(_soma(df["cost"]) - Decimal("27825.01")) < Decimal("0.000001")
    assert abs(_soma(df["cost_billed"]) - Decimal("27825.01")) < Decimal("0.000001")


def test_mock_quantidade_na_unidade_de_preco(mock) -> None:
    _, df = mock
    core = df[df["usage_type"].fillna("").str.startswith("E2 Instance Core")]
    ram = df[df["usage_type"].fillna("").str.startswith("E2 Instance Ram")]
    disco = df[df["usage_type"].fillna("").str.contains("PD Capacity")]
    assert set(core["usage_unit"]) == {"hours"}
    assert set(ram["usage_unit"]) == {"gibibyte hour"}
    assert set(disco["usage_unit"]) == {"gibibyte month"}
    # Horas de vCPU, não segundos: um mês de 1 vCPU são 744 h, não 2,6 milhões.
    assert max(core["usage_amount"]) < Decimal("10000")


def test_mock_vm_com_shape_medido(mock) -> None:
    _, df = mock
    # Com recurso: linha de crédito sem uso correspondente (acontece no Cost
    # Table de origem) chega sem VM e sem system_labels — e isso é correto.
    core = df[
        df["attributes"].apply(lambda a: a.get("capacity_unit") == "vcpu")
        & df["resource_id"].notna()
    ]
    assert core["resource_id"].nunique() == 26
    for atributos in core["attributes"]:
        assert atributos["instance_type"]
        assert Decimal(atributos["vcpus"]) > 0
        assert Decimal(atributos["memory_gib"]) > 0
        assert atributos["project_id"].startswith("proj-id-")
    assert (core["resource_type"] == core["attributes"].apply(lambda a: a["instance_type"])).all()


def test_mock_imposto_e_arredondamento(mock) -> None:
    _, df = mock
    naturezas = set(df["charge_kind"])
    assert {"usage", "tax", "adjustment"} <= naturezas


def test_csv_achatado_bate_com_o_ndjson(tmp_path, mocks_billing) -> None:
    _, via_csv = _parse(mocks_billing.bigquery_csv)
    with gzip.open(mocks_billing.bigquery, "rt", encoding="utf-8") as fh:
        primeiras = [next(fh) for _ in range(400)]
    via_json = GCPParser(user_id=_USER, job_id=_JOB).parse(
        _ndjson(tmp_path, [json.loads(x) for x in primeiras])
    )
    assert _soma(via_csv["cost"]) == _soma(via_json["cost"])
    assert sorted(via_csv["line_hash"]) == sorted(via_json["line_hash"])


# ── regras de crédito e natureza ────────────────────────────────────────────


def test_promocao_nao_reduz_a_base_mas_esta_no_faturado(tmp_path) -> None:
    creditos = [
        {"name": "SUD", "amount": "-2", "type": "SUSTAINED_USAGE_DISCOUNT"},
        {"name": "Trial", "amount": "-5", "type": "PROMOTION"},
    ]
    _, df = _parse(_ndjson(tmp_path, [_linha(credits=creditos)]))
    linha = df.iloc[0]
    assert linha["cost"] == Decimal("8")  # 10 - 2 (SUD); promoção fora
    assert linha["cost_billed"] == Decimal("3")  # 10 - 2 - 5: o que a fatura mostra
    assert linha["attributes"]["promotion_credit"] == "-5"


@pytest.mark.parametrize(
    ("cost_type", "ajuste", "natureza"),
    [
        ("regular", None, "usage"),
        ("tax", None, "tax"),
        ("rounding error", None, "adjustment"),
        ("rounding_error", None, "adjustment"),
        ("adjustment", "USAGE_CORRECTION", "usage"),
        ("adjustment", "PRICE_CORRECTION", "usage"),
        ("adjustment", "GOODWILL", "credit"),
        ("adjustment", "SLA_VIOLATION", "credit"),
        ("adjustment", None, "adjustment"),
    ],
)
def test_natureza(tmp_path, cost_type, ajuste, natureza) -> None:
    extra = {"cost_type": cost_type}
    if ajuste:
        extra["adjustment_info"] = {"type": ajuste}
    _, df = _parse(_ndjson(tmp_path, [_linha(**extra)]))
    assert df.iloc[0]["charge_kind"] == natureza


def test_sku_de_compromisso_e_commitment(tmp_path) -> None:
    linha = _linha(
        sku={"id": "c1", "description": "Commitment v1: N2 Cpu in Americas for 1 Year"},
        resource={"name": None, "global_name": None},
    )
    _, df = _parse(_ndjson(tmp_path, [linha]))
    assert df.iloc[0]["charge_kind"] == "commitment"


# ── rateio do CUD ───────────────────────────────────────────────────────────


def test_taxa_de_cud_rateada_pelo_credito(tmp_path) -> None:
    def coberta(n: int, custo: str, cud: str) -> dict:
        return _linha(
            resource={"name": f"vm-{n}", "global_name": f"//x/instances/{n}"},
            cost=custo,
            credits=[{"name": "CUD", "amount": cud, "type": "COMMITTED_USAGE_DISCOUNT"}],
        )

    linhas = [
        coberta(1, "60", "-60"),
        coberta(2, "20", "-20"),
        _linha(
            sku={"id": "c1", "description": "Commitment v1: N2 Cpu in Americas for 1 Year"},
            resource={"name": None, "global_name": None},
            cost="100",
        ),
    ]
    _, df = _parse(_ndjson(tmp_path, linhas))
    por_recurso = {r["resource_id"]: r for r in df.to_dict("records")}
    assert por_recurso["//x/instances/1"]["cost"] == Decimal("75")
    assert por_recurso["//x/instances/2"]["cost"] == Decimal("25")
    taxa = df[df["charge_kind"] == "commitment"].iloc[0]
    assert taxa["cost"] == Decimal("0")
    assert taxa["attributes"]["allocated_to_usage"] == "100"
    # Conservação: a soma não muda com o rateio.
    assert _soma(df["cost"]) == Decimal("100")
    assert (
        por_recurso["//x/instances/1"]["attributes"]["commitment_allocation"]
        == "proporcional_ao_credito_cud"
    )


def test_taxa_sem_uso_coberto_fica_no_compromisso(tmp_path) -> None:
    linha = _linha(
        sku={"id": "c1", "description": "Commitment v1: N2 Cpu in Americas for 1 Year"},
        resource={"name": None, "global_name": None},
        cost="100",
    )
    _, df = _parse(_ndjson(tmp_path, [linha, _linha()]))
    taxa = df[df["charge_kind"] == "commitment"].iloc[0]
    assert taxa["cost"] == Decimal("100")


# ── formatos ────────────────────────────────────────────────────────────────


def test_unidade_base_sem_unidade_de_preco_e_convertida(tmp_path) -> None:
    linha = _linha(usage={"amount": "7200", "unit": "seconds"})
    _, df = _parse(_ndjson(tmp_path, [linha]))
    assert df.iloc[0]["usage_unit"] == "hours"
    assert df.iloc[0]["usage_amount"] == Decimal("2")


def test_jsonl_array_e_gzip(tmp_path) -> None:
    linhas = [_linha(), _linha(cost="5", resource={"name": "vm-2", "global_name": "//x/2"})]
    jsonl = _ndjson(tmp_path, linhas, "bq.jsonl")
    array = tmp_path / "bq_array.json"
    array.write_text(json.dumps(linhas), encoding="utf-8")
    comprimido = tmp_path / "bq.json.gz"
    with gzip.open(comprimido, "wt", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(x) + "\n" for x in linhas))
    for caminho in (jsonl, array, comprimido):
        _, df = _parse(caminho)
        assert _soma(df["cost"]) == Decimal("15"), caminho.name


def test_linha_repetida_conta_uma_vez(tmp_path) -> None:
    _, df = _parse(_ndjson(tmp_path, [_linha(), _linha()]))
    assert _soma(df["cost"]) == Decimal("10")


def test_custo_nunca_passa_por_float(tmp_path) -> None:
    # 0.1 + 0.2 em float daria 0.30000000000000004.
    linhas = [
        _linha(cost=0.1, resource={"name": "a", "global_name": "//a"}),
        _linha(cost=0.2, resource={"name": "b", "global_name": "//b"}),
    ]
    caminho = tmp_path / "bq.json"
    caminho.write_text("".join(json.dumps(x) + "\n" for x in linhas), encoding="utf-8")
    _, df = _parse(caminho)
    assert _soma(df["cost"]) == Decimal("0.3")
