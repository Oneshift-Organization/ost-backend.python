"""Parser do GCP Cost Table — as linhas que NÃO são consumo.

O arquivo do Cost Table termina com uma linha `Cost type = Total`, que repete a
soma de todas as outras. Ingeri-la como item **dobra** o gasto.

O enriquecimento já a descartava, então a análise sempre esteve certa. Quem
mostrava o dobro era o dashboard e o drill-down, que somam `billing_items`
direto. Foi encontrado num E2E conferindo `billing_items` contra
`billing_enriched`: R$ 55.650 contra R$ 27.825, exatamente 2x.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from app.infrastructure.file_ingestion.parsers.gcp_cost_table_parser import (
    GcpCostTableParser,
)

_COLUNAS = [
    "Billing account name", "Billing account ID", "Project name", "Project ID",
    "Service description", "SKU description", "Cost type", "Usage start date",
    "Usage end date", "Usage amount", "Usage unit", "Location", "Cost (R$)",
]


def _linha(**kw) -> dict:
    base = dict.fromkeys(_COLUNAS, "")
    base.update(
        {
            "Billing account name": "Exemplo",
            "Service description": "Compute Engine",
            "SKU description": "N2 Instance Core running in Americas",
            "Usage start date": "2025-12-01",
            "Usage end date": "2025-12-02",
            "Cost (R$)": "100.00",
        }
    )
    base.update(kw)
    return base


def _parse(linhas: list[dict], tmp_path: Path) -> pd.DataFrame:
    caminho = tmp_path / "cost_table.csv"
    pd.DataFrame(linhas).to_csv(caminho, sep=";", index=False)
    return GcpCostTableParser(user_id=uuid4(), job_id=uuid4()).parse(caminho)


def test_linha_de_total_nao_vira_item(tmp_path: Path) -> None:
    """É o bug: a soma entraria como item e o gasto dobraria."""
    df = _parse(
        [
            _linha(**{"Cost (R$)": "100.00"}),
            _linha(**{"Cost (R$)": "50.00"}),
            _linha(**{"Cost type": "Total", "Cost (R$)": "150.00", "Service description": ""}),
        ],
        tmp_path,
    )
    assert len(df) == 2
    assert Decimal(str(df["cost"].sum())) == Decimal("150.00")


def test_imposto_continua_fora(tmp_path: Path) -> None:
    """Imposto é custo real, mas não se migra imposto."""
    df = _parse(
        [_linha(), _linha(**{"Cost type": "Tax", "Cost (R$)": "20.00"})], tmp_path
    )
    assert len(df) == 1


def test_erro_de_arredondamento_continua_dentro(tmp_path: Path) -> None:
    """Fica de fora da exclusão de propósito: é ajuste contábil de verdade, e
    descartá-lo criaria diferença inexplicável contra a fatura do cliente."""
    df = _parse(
        [
            _linha(),
            _linha(**{"Cost type": "Rounding error", "Cost (R$)": "-0.01"}),
        ],
        tmp_path,
    )
    assert len(df) == 2


@pytest.mark.parametrize("rotulo", ["Total", "total", "TOTAL", " Total "])
def test_a_exclusao_nao_depende_do_casing(tmp_path: Path, rotulo: str) -> None:
    df = _parse([_linha(), _linha(**{"Cost type": rotulo, "Cost (R$)": "999.00"})], tmp_path)
    assert len(df) == 1, f"{rotulo!r} passou"
