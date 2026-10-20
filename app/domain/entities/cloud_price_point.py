"""Ponto de preço real de nuvem (Precision Engine v3 — camada de verdade).

Diferente do ``cloud_sku_catalog`` (seed, specs + preço aproximado), um
``CloudPricePoint`` é um preço **oficial** buscado da API do provedor, com
proveniência (``source``, ``captured_at``) e ``confidence``. Carrega os próprios
specs (vcpus/ram) porque vêm da mesma resposta da API — assim serve como fonte
de candidatos do matcher sem precisar casar chave com o catálogo seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CloudPricePoint:
    provider: str  # 'azure', 'oci', … (minúsculo, normalizado)
    sku_key: str  # chave nativa do provedor (armSkuName, partNumber…)
    service_name: str
    service_type: str  # 'virtual_server', 'relational_database', …
    region: str
    price: Decimal  # preço na unidade abaixo
    currency: str
    unit: str = "instance_hour"
    vcpus: Decimal | None = None
    ram_gib: Decimal | None = None
    source: str = "manual"
    confidence: str = "official"
    captured_at: datetime | None = None
    # Ciclo de vida do preço (PLANO_CICLO_DE_VIDA_DO_PRECO): só rótulo, não muda número
    estado: str | None = None
    confianca_score: Decimal | None = None
    confirmado_em: datetime | None = None
