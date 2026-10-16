"""Nome ambíguo da fatura resolvido pelo uso das linhas, só quando um alvo domina."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.services.servico_pelo_uso import REGRAS, servico_pelo_uso

_CSV = (
    Path(__file__).parents[4]
    / "infrastructure"
    / "database"
    / "models"
    / "cloud_services_mapping.csv"
)


def _d(v: str) -> Decimal:
    return Decimal(v)


def test_storage_da_azure_com_disco_dominante_e_disco_gerenciado() -> None:
    usos = [
        ("Storage | Premium SSD Managed Disks | P10 LRS Disk", _d("80")),
        ("Storage | Tiered Block Blob | Hot LRS Data Stored", _d("20")),
    ]
    assert servico_pelo_uso("azure", "Storage", usos) == ("Azure Managed Disks", _d("0.8"))


def test_mistura_sem_dominante_continua_sem_tipo() -> None:
    usos = [
        ("Storage | Premium SSD Managed Disks | P10 LRS Disk", _d("50")),
        ("Storage | Tiered Block Blob | Hot LRS Data Stored", _d("50")),
    ]
    assert servico_pelo_uso("azure", "Storage", usos) is None


def test_networking_do_gcp_pelo_sku() -> None:
    usos = [
        ("Network Load Balancing: Forwarding Rule Minimum Service Charge in Americas", _d("90")),
        ("Static Ip Charge", _d("10")),
    ]
    assert servico_pelo_uso("gcp", "Networking", usos) == ("Cloud Load Balancing", _d("0.9"))


def test_database_da_oci_mysql() -> None:
    assert servico_pelo_uso("oci", "DATABASE", [("MySQL Database - ECPU", _d("10"))]) == (
        "MySQL Database Service",
        _d(1),
    )


def test_servico_sem_regra_nao_e_tocado() -> None:
    assert servico_pelo_uso("aws", "AmazonEC2", [("BoxUsage:m5.large", _d("10"))]) is None


@pytest.mark.parametrize(
    ("provedor", "alvo"),
    sorted({(p, alvo) for (p, _), regras in REGRAS.items() for _, alvo in regras}),
)
def test_todo_alvo_existe_no_de_para(provedor: str, alvo: str) -> None:
    with _CSV.open(newline="", encoding="utf-8") as f:
        nomes = {r["service_name"] for r in csv.DictReader(f) if r["provider"].lower() == provedor}
    assert alvo in nomes
