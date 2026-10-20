"""Região de cada destino para o diálogo do Terraform (PROXIMAS_EXECUCOES, item 27)."""

from __future__ import annotations

from app.use_cases.analysis.escopo_de_migracao import regioes_de_destino


def _eq(provedor: str, regiao: str | None, custo: float | None) -> dict[str, object]:
    return {
        "provedor": provedor,
        "financeiro": {"preco_regiao": regiao, "custo_mensal_estimado": custo},
    }


def test_vale_a_regiao_com_mais_custo_cotado_em_cada_destino() -> None:
    payload = {
        "pools_computacao": [
            {"equivalentes": [_eq("OCI", "us-ashburn-1", 900), _eq("AWS", "us-east-1", 1000)]},
            {"equivalentes": [_eq("OCI", "sa-saopaulo-1", 300), _eq("AWS", "sa-east-1", 400)]},
            {"equivalentes": [_eq("OCI", "sa-saopaulo-1", 200)]},
        ]
    }
    assert regioes_de_destino(payload) == {"oci": "us-ashburn-1", "aws": "us-east-1"}


def test_destino_sem_regiao_ou_sem_custo_fica_de_fora() -> None:
    sem_regiao, sem_custo = _eq("GCP", None, 10), _eq("AZURE", "eastus", None)
    payload = {"pools_computacao": [{"equivalentes": [sem_regiao, sem_custo]}]}
    assert regioes_de_destino(payload) == {}
    assert regioes_de_destino({}) == {}
