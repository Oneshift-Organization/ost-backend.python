"""Balanced PD é SSD, e o SSD de referência do GCP é o pd-balanced.

`*pd capacity*` levava "Balanced PD Capacity" — o disco padrão das VMs do GCP —
para `storage_hdd`. No CSV real versionado isso é 94% do custo de disco. O erro
se propagava em três lugares: o GB estimado (custo dividido pela taxa do
pd-standard saía ~2,5x), a cotação no destino (HDD contra HDD, quando o par é o
gp3) e o Terraform (st1 e Standard_LRS para um disco SSD).

Os nomes de SKU abaixo são os do CSV real e da tabela pública de SKUs do GCP.
"""

from __future__ import annotations

import re
from decimal import Decimal
from uuid import uuid4

import pytest

from app.domain.entities.sku_classification_rule import SkuClassificationRule
from app.domain.services.sku_matcher import SkuMatcher
from app.domain.services.terraform_plan import _TAXA_GB_MES, derivar_plano
from app.infrastructure.database.seed_category_rates import category_rate_points
from app.infrastructure.database.seed_classification_rules import GCP_RULES
from app.infrastructure.terraform.renderers import renderizar
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint


def _matcher() -> SkuMatcher:
    regras = [
        SkuClassificationRule(
            provider="gcp",
            pattern=padrao,
            kind=tipo,
            component_type=componente,
            family=familia,
            confidence=confianca,
            priority=prioridade,
        )
        for (padrao, tipo, componente, familia, confianca, prioridade) in GCP_RULES
    ]
    return SkuMatcher([], regras)


@pytest.mark.parametrize(
    ("sku", "categoria"),
    [
        ("Balanced PD Capacity", "storage_ssd"),
        ("Balanced PD Capacity in Sao Paulo", "storage_ssd"),
        ("Regional Balanced PD Capacity in Sao Paulo", "storage_ssd"),
        ("Extreme PD Capacity in Iowa", "storage_ssd"),
        ("SSD backed PD Capacity in Sao Paulo", "storage_ssd"),
        ("Regional SSD backed PD Capacity", "storage_ssd"),
        # O que é HDD continua HDD.
        ("Storage PD Capacity in Dallas", "storage_hdd"),
        ("Regional Storage PD Capacity", "storage_hdd"),
        ("Storage PD Snapshot in US", "storage_hdd"),
    ],
)
def test_classe_do_disco(sku: str, categoria: str) -> None:
    assert _matcher().match(sku, usage_unit="gibibyte month").component_type == categoria


# ── taxas ───────────────────────────────────────────────────────────────


def _taxas_do_seed() -> dict[tuple[str, str], Decimal]:
    return {(p.provider, p.service_type): p.price for p in category_rate_points()}


def test_ssd_de_referencia_do_gcp_e_o_de_proposito_geral() -> None:
    """pd-balanced (US$ 0,100) fica entre o pd-standard e o pd-ssd (US$ 0,170)."""
    taxas = _taxas_do_seed()
    assert taxas[("gcp", "storage_ssd")] == Decimal("0.100")
    assert taxas[("gcp", "storage_hdd")] < taxas[("gcp", "storage_ssd")] < Decimal("0.170")


def test_terraform_usa_as_mesmas_taxas_do_seed() -> None:
    """O plano volta de dinheiro para GB com uma cópia das taxas (é puro, não lê
    banco). Com as duas divergindo, a cotação e o `.tf` falariam de discos de
    tamanhos diferentes para o mesmo custo."""
    taxas = _taxas_do_seed()
    for provedor, por_categoria in _TAXA_GB_MES.items():
        for categoria, taxa in por_categoria.items():
            assert Decimal(taxa) == taxas[(provedor, categoria)], (provedor, categoria)


# ── Terraform ───────────────────────────────────────────────────────────


def _payload(origem: str) -> dict:
    return {
        "provedor_origem": origem,
        "moeda": "USD",
        "pools_computacao": [],
        "custos_auxiliares": {"armazenamento_ssd": 700.0, "armazenamento_hdd": 40.0},
        "inventario_medido": {
            "armazenamento": {
                "armazenamento_ssd": {"gb": 6000, "recursos": 40},
                "armazenamento_hdd": {"gb": 1000, "recursos": 4},
            }
        },
    }


def test_plano_pede_o_ssd_de_proposito_geral() -> None:
    plano = derivar_plano(
        _payload("aws"), provedor_destino="gcp", regiao="us-central1", alta_disponibilidade=False
    )
    tipos = {
        r.logical_name: r.configuration["disk_type"]
        for r in plano.recursos
        if r.configuration.get("disk_only")
    }
    assert tipos == {"disco_storage_ssd": "pd-balanced", "disco_storage_hdd": "pd-standard"}


@pytest.mark.parametrize(
    ("destino", "regiao", "atributo", "ssd", "hdd"),
    [
        ("gcp", "us-central1", "type", "pd-balanced", "pd-standard"),
        ("aws", "sa-east-1", "type", "gp3", "st1"),
        ("azure", "brazilsouth", "storage_account_type", "Premium_LRS", "Standard_LRS"),
    ],
)
def test_cada_destino_traduz_a_classe(
    destino: str, regiao: str, atributo: str, ssd: str, hdd: str
) -> None:
    entrada = TerraformGenerationIn(
        analysis_job_id=uuid4(), project_name="disco", target_provider=destino, region=regiao
    )
    origem = "aws" if destino == "gcp" else "gcp"
    main = renderizar(montar_blueprint(_payload(origem), entrada))["main.tf"]
    tipos = re.findall(rf'^\s*{atributo}\s*=\s*"([^"]+)"', main, re.MULTILINE)
    assert sorted(tipos) == sorted([ssd, hdd]), main
