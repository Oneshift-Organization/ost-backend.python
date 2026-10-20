"""Cliente da Cloud Billing Catalog API do GCP — Compute Engine.

## Esta fonte exige credencial, diferente das outras

Azure, OCI e AWS publicam preço sem autenticação. O GCP não: a Catalog API
responde **403** a chamador anônimo ("Method doesn't allow unregistered
callers"), e o `pricelist.json` estático que a calculadora antiga usava foi
desativado (404). Verificado, não suposto.

Então este cliente precisa de `GCP_BILLING_API_KEY` — uma API key simples de
projeto, com a Cloud Billing API habilitada. Não é service account, não precisa
de escopo além de leitura do catálogo público.

**Sem a key, devolve lista vazia** em vez de estourar. É o mesmo contrato do
`category_rate_client`: um sync sem credencial degrada para "não coletei nada",
não derruba os outros provedores que estavam funcionando.

## Como o GCP precifica compute

Ao contrário de Azure e AWS, que cobram por instância, o GCP cobra **vCPU/hora e
GB/hora separados**. Montamos o preço de cada tipo de máquina a partir das duas
taxas, para que o packer os consuma como qualquer outro candidato.

Os tipos são os que a Google publica, com os tamanhos e a memória de cada
classe (`standard`, `highmem`, `highcpu`). Antes, uma grade única de
(vCPU, GB) gerava nomes que não existem, como `e2-standard-64` (o E2 vai até
32 vCPU) e `c3-standard-16` (o C3 é 4, 8, 22, 44…). O Terraform usa o nome.

Uma passada no catálogo serve todas as regiões pedidas: cada SKU de taxa diz
em `serviceRegions` onde vale.

Sem dependência externa (urllib da stdlib). Roda offline, nunca no caminho
crítico.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint

logger = logging.getLogger(__name__)

# Service IDs do catálogo — fixos e públicos.
COMPUTE_SERVICE = "6F81-5844-456A"
STORAGE_SERVICE = "95FF-2EF5-5EA1"
_BASE = "https://cloudbilling.googleapis.com/v1/services/{servico}/skus"
SOURCE = "gcp_billing_catalog_api"

_NANOS = Decimal("1000000000")
# Teto de páginas: o catálogo de Compute tem ~33 mil SKUs (7 páginas de 5 mil),
# e o limite evita laço infinito se a API passar a devolver um `nextPageToken`
# que não avança.
_MAX_PAGINAS = 20


@dataclass(frozen=True, slots=True)
class _Familia:
    """Classes publicadas da família: classe → (GB por vCPU, tamanhos em vCPU)."""

    rotulo: str
    classes: dict[str, tuple[int, tuple[int, ...]]]


# Famílias de propósito geral, compute e memória — as workhorses. Especializadas
# (GPU, M1/M2/M3 de memória extrema, Z3 de storage) distorcem o packing.
# T2A e C4A são ARM; o perfil de família lê a arquitetura pelo nome.
_FAMILIAS: dict[str, _Familia] = {
    "e2": _Familia(
        "E2",
        {
            "standard": (4, (2, 4, 8, 16, 32)),
            "highmem": (8, (2, 4, 8, 16)),
            "highcpu": (1, (2, 4, 8, 16, 32)),
        },
    ),
    "n2": _Familia(
        "N2",
        {
            "standard": (4, (2, 4, 8, 16, 32, 48, 64, 80, 96, 128)),
            "highmem": (8, (2, 4, 8, 16, 32, 48, 64, 80, 96, 128)),
            "highcpu": (1, (2, 4, 8, 16, 32, 48, 64, 80, 96)),
        },
    ),
    "n2d": _Familia(
        "N2D",
        {
            "standard": (4, (2, 4, 8, 16, 32, 48, 64, 80, 96, 128, 224)),
            "highmem": (8, (2, 4, 8, 16, 32, 48, 64, 80, 96)),
            "highcpu": (1, (2, 4, 8, 16, 32, 48, 64, 80, 96, 128, 224)),
        },
    ),
    "n4": _Familia(
        "N4",
        {
            "standard": (4, (2, 4, 8, 16, 32, 48, 64, 80)),
            "highmem": (8, (2, 4, 8, 16, 32, 48, 64, 80)),
            "highcpu": (2, (2, 4, 8, 16, 32, 48, 64, 80)),
        },
    ),
    "c3": _Familia(
        "C3",
        {
            "standard": (4, (4, 8, 22, 44, 88, 176)),
            "highmem": (8, (4, 8, 22, 44, 88, 176)),
            "highcpu": (2, (4, 8, 22, 44, 88, 176)),
        },
    ),
    "c4a": _Familia(
        "C4A",
        {
            "standard": (4, (1, 2, 4, 8, 16, 32, 48, 64, 72)),
            "highmem": (8, (1, 2, 4, 8, 16, 32, 48, 64, 72)),
            "highcpu": (2, (1, 2, 4, 8, 16, 32, 48, 64, 72)),
        },
    ),
    "t2a": _Familia("T2A", {"standard": (4, (1, 2, 4, 8, 16, 32, 48))}),
}

# A taxa base da família, e só ela: "N2D AMD Instance Core running in …".
# Prefixo solto (`n2 `) casava também "N2 Custom Instance Core", que é mais
# caro, e o último SKU lido vencia.
_SKU_DE_TAXA = re.compile(
    r"^(e2|n2|n2d|n4|c3|c4a|t2a)(?: amd| arm)? instance (core|ram) running in ",
    re.IGNORECASE,
)


def _get_json(url: str, *, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 - host fixo (googleapis.com)
        url, headers={"User-Agent": "oneshift-pricing/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host fixo
        dados: dict[str, Any] = json.loads(resp.read())
        return dados


def preco_unitario(sku: dict[str, Any]) -> Decimal | None:
    """Extrai o preço da primeira faixa não-zero, em unidades da moeda.

    O catálogo expressa valor em `units` + `nanos` (bilionésimos). A primeira
    faixa costuma ser 0 (tier gratuito); pegar cegamente `[0]` devolveria preço
    zero e faria a família parecer de graça.
    """
    try:
        expr = sku["pricingInfo"][0]["pricingExpression"]
        faixas = expr["tieredRates"]
    except (KeyError, IndexError, TypeError):
        return None

    for faixa in faixas:
        preco = faixa.get("unitPrice", {})
        valor = Decimal(str(preco.get("units", "0"))) + (
            Decimal(str(preco.get("nanos", 0))) / _NANOS
        )
        if valor > 0:
            return valor
    return None


def _classificar(sku: dict[str, Any], regiao: str) -> tuple[str, str] | None:
    """Devolve (família, dimensão) se o SKU for taxa de vCPU ou RAM da região.

    `dimensão` é 'cpu' ou 'ram'. Qualquer outra coisa — disco, licença, GPU,
    custom, sole tenancy, preço de committed use — devolve None.
    """
    if regiao not in (sku.get("serviceRegions") or []):
        return None

    cat = sku.get("category") or {}
    if cat.get("resourceFamily") != "Compute":
        return None
    # Só on-demand: 'Preemptible' e 'Commit1Yr' têm preço menor e não são
    # comparáveis com o on-demand que buscamos nos outros provedores.
    if cat.get("usageType") != "OnDemand":
        return None
    if str(cat.get("resourceGroup") or "") not in {"CPU", "RAM"}:
        return None

    m = _SKU_DE_TAXA.match(str(sku.get("description") or ""))
    if m is None:
        return None
    return _FAMILIAS[m.group(1).lower()].rotulo, "cpu" if m.group(2).lower() == "core" else "ram"


def skus_do_servico(
    servico: str, *, api_key: str, timeout: float = 60.0
) -> list[dict[str, Any]] | None:
    """Todos os SKUs de um serviço do catálogo, ou None se a API recusar.

    None, e não lista vazia, para quem chama distinguir "a API negou" de "não
    há nada": o primeiro é credencial, o segundo seria bug.
    """
    skus: list[dict[str, Any]] = []
    token = ""
    for _ in range(_MAX_PAGINAS):
        params = {"key": api_key, "pageSize": "5000"}
        if token:
            params["pageToken"] = token
        url = f"{_BASE.format(servico=servico)}?{urllib.parse.urlencode(params)}"
        try:
            payload = _get_json(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            logger.warning(
                "gcp_price_sync_falhou",
                extra={"status": e.code, "motivo": "credencial inválida ou API desabilitada"},
            )
            return None
        skus.extend(payload.get("skus", []))
        token = payload.get("nextPageToken") or ""
        if not token:
            break
    return skus


def _sem_credencial() -> None:
    logger.warning(
        "gcp_price_sync_sem_credencial",
        extra={
            "motivo": "GCP_BILLING_API_KEY ausente; a Catalog API recusa "
            "chamador anônimo (403) e não há fonte pública alternativa"
        },
    )


def fetch_gcp_compute_prices(
    *,
    api_key: str = "",
    regioes: Sequence[str] = ("us-central1",),
    timeout: float = 60.0,
) -> list[CloudPricePoint]:
    """Preços on-demand de Compute Engine, um conjunto por região pedida.

    Sem `api_key`, devolve lista vazia e registra o motivo — degradação
    graciosa, para que um sync sem credencial de GCP não derrube os provedores
    que estavam funcionando.
    """
    if not api_key:
        _sem_credencial()
        return []
    skus = skus_do_servico(COMPUTE_SERVICE, api_key=api_key, timeout=timeout)
    if skus is None:
        return []
    agora = datetime.now(UTC).replace(tzinfo=None)
    return [p for r in regioes for p in _pontos_da_regiao(skus, r, agora)]


def _pontos_da_regiao(
    skus: list[dict[str, Any]], regiao: str, agora: datetime
) -> list[CloudPricePoint]:
    # Duas taxas por família: vCPU/hora e GB/hora.
    taxas: dict[str, dict[str, Decimal]] = {}
    for sku in skus:
        classificado = _classificar(sku, regiao)
        if classificado is None:
            continue
        rotulo, dimensao = classificado
        preco = preco_unitario(sku)
        if preco is not None:
            taxas.setdefault(rotulo, {})[dimensao] = preco

    pontos: list[CloudPricePoint] = []
    for chave, familia in _FAMILIAS.items():
        dim = taxas.get(familia.rotulo, {})
        cpu, ram = dim.get("cpu"), dim.get("ram")
        if cpu is None or ram is None:
            # Família sem uma das taxas nesta região (o T2A não existe em São
            # Paulo, por exemplo) não tem preço de máquina.
            continue
        for classe, (gb_por_vcpu, tamanhos) in familia.classes.items():
            for vcpus in tamanhos:
                gb = vcpus * gb_por_vcpu
                pontos.append(
                    CloudPricePoint(
                        provider="gcp",
                        sku_key=f"{chave}-{classe}-{vcpus}",
                        service_name=f"Compute Engine {familia.rotulo}",
                        service_type="virtual_server",
                        region=regiao,
                        price=(cpu * vcpus) + (ram * gb),
                        currency="USD",
                        unit="instance_hour",
                        vcpus=Decimal(vcpus),
                        ram_gib=Decimal(gb),
                        source=SOURCE,
                        confidence="official",
                        captured_at=agora,
                    )
                )
    return pontos
