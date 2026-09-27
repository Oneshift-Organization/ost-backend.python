"""Payloads fixos de análise para os testes de Terraform.

Capturados do pipeline em 27/09/2026 a partir dos mocks de billing e de
inventário (e do CSV real do GCP, o caminho legado), com só as chaves que o
gerador lê. Congelados de propósito: os testes são sobre o gerador, não sobre o
pipeline, e recalcular faria o resultado depender de preço e câmbio do banco.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.infrastructure.terraform.renderers import renderizar
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint

FIXTURES = Path(__file__).parent / "fixtures_terraform"
REGIOES = {
    "gcp": "us-central1",
    "aws": "sa-east-1",
    "azure": "brazilsouth",
    "oci": "sa-saopaulo-1",
}


def carregar(nome: str) -> dict[str, Any]:
    with gzip.open(FIXTURES / f"{nome}.json.gz", "rt", encoding="utf-8") as fh:
        dados: dict[str, Any] = json.load(fh)
    return dados


def com_topologia(provedor: str) -> dict[str, Any]:
    """Payload da análise com a topologia do inventário do mesmo mock."""
    return {**carregar(f"payload_{provedor}"), "topologia": carregar(f"topologia_{provedor}")}


def entrada(destino: str, **extra: Any) -> TerraformGenerationIn:
    return TerraformGenerationIn(
        analysis_job_id=UUID(int=0),
        project_name="caracterizacao",
        environment="prod",
        target_provider=destino,
        region=REGIOES[destino],
        network=extra.pop("network", {"mode": "create", "cidr": "10.50.0.0/16"}),
        availability={"high_availability": True},
        **extra,
    )


def pacote(payload: dict[str, Any], destino: str, **extra: Any) -> dict[str, str]:
    return renderizar(
        montar_blueprint(payload, entrada(destino, **extra)),
        procedencia=payload.get("premissas") or {},
        topologia=payload.get("topologia"),
        moeda=payload.get("moeda"),
    )
