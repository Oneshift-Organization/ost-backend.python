"""Cada tipo de disco tem uma faixa de tamanho, e o `.tf` precisa respeitá-la.

A faixa é regra da API do provedor: `terraform validate` passa com um st1 de
110 GB, e o erro só aparece no `apply`. O caso veio do mock BigQuery depois da
correção do Balanced PD: o HDD que sobrou deu 8 volumes de ~110 GB.
"""

from __future__ import annotations

import re
from uuid import uuid4

import pytest

from app.infrastructure.terraform.renderers import renderizar
from app.infrastructure.terraform.renderers.base import ajustar_tamanho
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint

_REGIAO = {"gcp": "us-central1", "aws": "sa-east-1", "azure": "brazilsouth", "oci": "sa-saopaulo-1"}
_ATRIBUTO = {"gcp": "size", "aws": "size", "azure": "disk_size_gb", "oci": "size_in_gbs"}


def test_dentro_da_faixa_nao_muda_nada() -> None:
    assert ajustar_tamanho(130, minimo=1, maximo=16_384, tipo="gp3") == (130, "")


def test_fora_da_faixa_sobe_ou_desce_e_diz() -> None:
    tamanho, nota = ajustar_tamanho(110, minimo=125, maximo=16_384, tipo="st1")
    assert tamanho == 125
    assert "st1 aceita de 125 a 16,384 GB" in nota
    assert "~110 GB saiu com 125" in nota
    assert ajustar_tamanho(20_000, minimo=1, maximo=16_384, tipo="gp3")[0] == 16_384


def _tamanhos(destino: str, categoria: str, gb: int, volumes: int) -> tuple[list[int], str]:
    payload = {
        "provedor_origem": "aws" if destino == "gcp" else "gcp",
        "moeda": "USD",
        "pools_computacao": [],
        "custos_auxiliares": {categoria: 100.0},
        "inventario_medido": {
            "armazenamento": {categoria: {"gb": gb, "recursos": volumes}},
        },
    }
    entrada = TerraformGenerationIn(
        analysis_job_id=uuid4(),
        project_name="faixa",
        target_provider=destino,
        region=_REGIAO[destino],
    )
    main = renderizar(montar_blueprint(payload, entrada))["main.tf"]
    padrao = rf"^\s*{_ATRIBUTO[destino]}\s*=\s*(\d+)"
    return [int(x) for x in re.findall(padrao, main, re.MULTILINE)], main


@pytest.mark.parametrize(
    ("destino", "categoria", "gb", "volumes", "esperado"),
    [
        # st1 começa em 125 GiB; gp3, em 1.
        ("aws", "armazenamento_hdd", 877, 8, 125),
        ("aws", "armazenamento_ssd", 877, 8, 110),
        # pd-* começa em 10 GB; OCI Block Volume, em 50.
        ("gcp", "armazenamento_ssd", 40, 8, 10),
        ("oci", "armazenamento_hdd", 877, 30, 50),
        ("azure", "armazenamento_hdd", 877, 8, 110),
    ],
)
def test_volume_medido_respeita_o_minimo(
    destino: str, categoria: str, gb: int, volumes: int, esperado: int
) -> None:
    tamanhos, main = _tamanhos(destino, categoria, gb, volumes)
    assert tamanhos == [esperado], main
    assert ("aceita de" in main) is (esperado != -(-gb // volumes))


@pytest.mark.parametrize(
    ("destino", "maximo"), [("aws", 16_384), ("azure", 32_767), ("oci", 32_768)]
)
def test_volume_grande_para_no_maximo_do_destino(destino: str, maximo: int) -> None:
    """O plano limita em 64 TB (o do GCP); os outros aceitam menos."""
    tamanhos, main = _tamanhos(destino, "armazenamento_ssd", 40_000, 1)
    assert tamanhos == [maximo], main
    assert "aceita de" in main
