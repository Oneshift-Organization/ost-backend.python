"""Fatura AWS legada: Spot e instância com prefixo de região têm unidade de hora.

Sem a unidade, o agregador não soma a capacidade e manda o custo para
"compromisso" — no bill real, ~150 vCPU sumiam e US$ 1,7 mil de Spot viravam
compromisso (achado na E7 do PLANO_CUR_BIGQUERY, comparando com o CUR).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.infrastructure.file_ingestion.parsers import AwsDetailedBillingParser

_COLUNAS = [
    "InvoiceID",
    "PayerAccountId",
    "LinkedAccountId",
    "RecordType",
    "RecordID",
    "BillingPeriodStartDate",
    "BillingPeriodEndDate",
    "InvoiceDate",
    "PayerAccountName",
    "LinkedAccountName",
    "TaxationAddress",
    "PayerPONumber",
    "ProductCode",
    "ProductName",
    "SellerOfRecord",
    "UsageType",
    "Operation",
    "RateId",
    "ItemDescription",
    "UsageStartDate",
    "UsageEndDate",
    "UsageQuantity",
    "BlendedRate",
    "CurrencyCode",
    "CostBeforeTax",
    "Credits",
    "TaxAmount",
    "TaxType",
    "TotalCost",
]


@pytest.mark.parametrize(
    ("usage_type", "unidade"),
    [
        ("BoxUsage:m7g.2xlarge", "hours"),
        ("SpotUsage:c7g.2xlarge", "hours"),
        ("DedicatedUsage:m5.large", "hours"),
        ("SAE1-BoxUsage:t3a.medium", "hours"),
        ("EBS:VolumeUsage.gp3", None),
        ("DataTransfer-Out-Bytes", None),
    ],
)
def test_unidade_de_hora_de_instancia(tmp_path: Path, user_id, job_id, usage_type, unidade):
    arquivo = tmp_path / "fatura.csv"
    with arquivo.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLUNAS)
        w.writeheader()
        w.writerow(
            {
                **dict.fromkeys(_COLUNAS, ""),
                "RecordType": "PayerLineItem",
                "BillingPeriodStartDate": "2026/03/01 00:00:00",
                "ProductCode": "AmazonEC2",
                "UsageType": usage_type,
                "UsageQuantity": "744",
                "CurrencyCode": "USD",
                "CostBeforeTax": "10",
                "TotalCost": "10",
            }
        )
    df = AwsDetailedBillingParser(user_id=user_id, job_id=job_id).parse(arquivo)
    assert df.iloc[0]["usage_unit"] == unidade
