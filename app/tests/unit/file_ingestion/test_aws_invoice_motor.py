"""Relatório detalhado da AWS: o motor do RDS pela descrição ou pelo código da operação
(PLANO_EQUIVALENTES_E_TARIFAS §10). As linhas são as do Cost Explorer de 03/2026."""

from __future__ import annotations

import csv
from pathlib import Path
from uuid import UUID

import pytest

from app.infrastructure.file_ingestion.parsers import AwsDetailedBillingParser
from app.tests.unit.file_ingestion.test_aws_invoice_spot import _COLUNAS


def _rds(
    uso: str, operacao: str = "", descricao: str = "", produto: str = "AmazonRDS"
) -> dict[str, str]:
    return {
        "ProductCode": produto,
        "UsageType": uso,
        "Operation": operacao,
        "ItemDescription": descricao,
    }


_PG = "CreateDBInstance:0014"
_AURORA_PG = "CreateDBInstance:0021"


@pytest.mark.parametrize(
    ("linha", "motor"),
    [
        (_rds("Multi-AZUsage:db.r6g.large", _PG, "db.r6g.large (PostgreSQL)"), "PostgreSQL"),
        (
            _rds("HeavyUsage:db.r8g.12xl", _AURORA_PG, "db.r8g.12xl (Aurora PostgreSQL)"),
            "Aurora PostgreSQL",
        ),
        # Sem descrição: o código da operação
        (_rds("RDS:Multi-AZ-GP3-Storage", _PG), "PostgreSQL"),
        (_rds("Aurora:StorageIOUsage", _AURORA_PG), "Aurora PostgreSQL"),
        (_rds("Multi-AZUsage:db.m5.large", "CreateDBInstance:0002"), "MySQL"),
        # Oracle (0020) e operação que não é de instância ficam sem motor, como antes
        (_rds("InstanceUsage:db.m5.large", "CreateDBInstance:0020"), None),
        (_rds("USE1-RDS:ProxyUsage", "CreateDBProxy:0014"), None),
        # A fatura de 09/2026 não traz nenhuma das duas colunas
        (_rds("InstanceUsage:db.t3.medium"), None),
        (_rds("BoxUsage:m5.large", "RunInstances:0014", produto="AmazonEC2"), None),
    ],
)
def test_motor_do_rds(
    tmp_path: Path, user_id: UUID, job_id: UUID, linha: dict[str, str], motor: str | None
) -> None:
    arquivo = tmp_path / "fatura.csv"
    with arquivo.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUNAS)
        w.writeheader()
        w.writerow(
            {
                **dict.fromkeys(_COLUNAS, ""),
                "RecordType": "PayerLineItem",
                "BillingPeriodStartDate": "2026/03/01 00:00:00",
                "UsageQuantity": "744",
                "CurrencyCode": "USD",
                "CostBeforeTax": "10",
                "TotalCost": "10",
                **linha,
            }
        )
    df = AwsDetailedBillingParser(user_id=user_id, job_id=job_id).parse(arquivo)
    assert df.iloc[0]["attributes"] == ({"database_engine": motor} if motor else {})
