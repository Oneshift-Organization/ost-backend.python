"""O preço de cada serviço no destino, lido da tabela de equivalentes gravada."""

from __future__ import annotations

from decimal import Decimal

from app.use_cases.analysis.consultar_acoplamento import precos_por_servico


def test_so_a_linha_que_entra_no_total_da_o_preco() -> None:
    """As alternativas do destino dividem o orçamento: somar as três triplicava o preço."""
    linhas = [
        {"workload": "AmazonRDS", "target_provider": "OCI", "target_service": nome,
         "estimated_monthly_cost": 55.98, "included_in_estimate": nome == "MySQL"}
        for nome in ("Oracle", "MySQL", "PostgreSQL")
    ] + [
        {"workload": "AmazonS3", "target_provider": "GCP", "estimated_monthly_cost": None},
        # Tabela antiga, sem a marca: a primeira com preço
        {"workload": "AmazonS3", "target_provider": "GCP", "estimated_monthly_cost": 3.5},
        {"workload": "AmazonS3", "target_provider": "GCP", "estimated_monthly_cost": 9.0},
    ]
    assert precos_por_servico(linhas) == {
        ("AmazonRDS", "oci"): Decimal("55.98"),
        ("AmazonS3", "gcp"): Decimal("3.5"),
    }
