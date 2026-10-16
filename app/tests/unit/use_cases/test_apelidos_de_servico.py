"""Todo apelido de serviço aponta para um nome que existe no DE-PARA.

Apelido para um nome que o CSV não tem é pior que nenhum: o serviço sai "tipo
inferido" e sem equivalente, como o AWSELB saía antes de ter apelido.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.use_cases.analysis.build_migration_context import PROVIDER_SERVICE_ALIASES

_CSV = (
    Path(__file__).parents[3]
    / "infrastructure"
    / "database"
    / "models"
    / "cloud_services_mapping.csv"
)


def _nomes(provedor: str) -> set[str]:
    with _CSV.open(newline="", encoding="utf-8") as f:
        return {r["service_name"] for r in csv.DictReader(f) if r["provider"].lower() == provedor}


@pytest.mark.parametrize(
    ("provedor", "codigo", "nome"),
    [(p, c, n) for p, apelidos in PROVIDER_SERVICE_ALIASES.items() for c, n in apelidos.items()],
)
def test_apelido_aponta_para_nome_do_de_para(provedor: str, codigo: str, nome: str) -> None:
    assert nome in _nomes(provedor), f"{codigo} → {nome} não existe no DE-PARA de {provedor}"


def test_codigo_de_produto_do_balanceador_tem_apelido() -> None:
    assert PROVIDER_SERVICE_ALIASES["aws"]["AWSELB"] == "Elastic Load Balancing"
