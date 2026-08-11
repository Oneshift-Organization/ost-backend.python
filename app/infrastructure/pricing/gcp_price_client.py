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
GB/hora separados** para as famílias modernas (N2, N2D, E2, C3…). O mesmo
problema do OCI, e a mesma solução: geramos configurações sintéticas a partir das
duas taxas, para que o packer as consuma como qualquer outro candidato — sem
espalhar lógica de GCP dentro dele.

Sem dependência externa (urllib da stdlib). Roda offline, nunca no caminho
crítico.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint

logger = logging.getLogger(__name__)

# Service ID do Compute Engine no catálogo — fixo e público.
_COMPUTE_SERVICE = "6F81-5844-456A"
_BASE = f"https://cloudbilling.googleapis.com/v1/services/{_COMPUTE_SERVICE}/skus"
_SOURCE = "gcp_billing_catalog_api"

_NANOS = Decimal("1000000000")
# Teto de páginas: o catálogo de Compute cabe folgado, e o limite evita laço
# infinito se a API passar a devolver um `nextPageToken` que não avança.
_MAX_PAGINAS = 20

# Configurações sintéticas por família: (vCPUs, GB de RAM). Escolhidas para
# cobrir a faixa que aparece nos bills reais sem explodir o número de candidatos
# — o packer combina instâncias, então não precisa de granularidade fina.
_FORMAS = ((2, 8), (4, 16), (8, 32), (16, 64), (32, 128), (64, 256))

# Famílias workhorse. Mesma lógica do filtro em Azure/AWS: especializadas
# distorcem o packing.
_FAMILIAS = {
    "n2": "N2",
    "n2d": "N2D",
    "e2": "E2",
    "c3": "C3",
}


def _get_json(url: str, *, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 - host fixo (googleapis.com)
        url, headers={"User-Agent": "oneshift-pricing/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host fixo
        dados: dict[str, Any] = json.loads(resp.read())
        return dados


def _preco_unitario(sku: dict[str, Any]) -> Decimal | None:
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
    preço de committed use — devolve None e é descartada.
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

    grupo = str(cat.get("resourceGroup") or "")
    if grupo not in {"CPU", "RAM"}:
        return None

    descricao = str(sku.get("description") or "").lower()
    for chave, rotulo in _FAMILIAS.items():
        # "N2 Instance Core running in Americas" / "N2 Instance Ram running in…"
        if descricao.startswith(f"{chave} "):
            return rotulo, "cpu" if grupo == "CPU" else "ram"
    return None


def fetch_gcp_compute_prices(
    *,
    api_key: str = "",
    region: str = "us-central1",
    timeout: float = 60.0,
) -> list[CloudPricePoint]:
    """Preços on-demand de Compute Engine para uma região.

    Sem `api_key`, devolve lista vazia e registra o motivo — degradação
    graciosa, para que um sync sem credencial de GCP não derrube os provedores
    que estavam funcionando.
    """
    if not api_key:
        logger.warning(
            "gcp_price_sync_sem_credencial",
            extra={
                "motivo": "GCP_BILLING_API_KEY ausente; a Catalog API recusa "
                "chamador anônimo (403) e não há fonte pública alternativa"
            },
        )
        return []

    # Duas taxas por família: vCPU/hora e GB/hora.
    taxas: dict[str, dict[str, Decimal]] = {}
    token = ""
    paginas = 0

    while paginas < _MAX_PAGINAS:
        params = {"key": api_key, "pageSize": "5000"}
        if token:
            params["pageToken"] = token
        try:
            payload = _get_json(f"{_BASE}?{urllib.parse.urlencode(params)}", timeout=timeout)
        except urllib.error.HTTPError as e:
            logger.warning(
                "gcp_price_sync_falhou",
                extra={"status": e.code, "motivo": "credencial inválida ou API desabilitada"},
            )
            return []

        for sku in payload.get("skus", []):
            classificado = _classificar(sku, region)
            if classificado is None:
                continue
            familia, dimensao = classificado
            preco = _preco_unitario(sku)
            if preco is None:
                continue
            taxas.setdefault(familia, {})[dimensao] = preco

        token = payload.get("nextPageToken") or ""
        paginas += 1
        if not token:
            break

    agora = datetime.now(UTC).replace(tzinfo=None)
    pontos: list[CloudPricePoint] = []

    for familia, dim in taxas.items():
        cpu, ram = dim.get("cpu"), dim.get("ram")
        if cpu is None or ram is None:
            # Família com só uma das taxas não permite montar o preço da forma.
            continue
        for vcpus, gb in _FORMAS:
            pontos.append(
                CloudPricePoint(
                    provider="gcp",
                    sku_key=f"{familia.lower()}-standard-{vcpus}",
                    service_name=f"Compute Engine {familia}",
                    service_type="virtual_server",
                    region=region,
                    price=(cpu * vcpus) + (ram * gb),
                    currency="USD",
                    unit="instance_hour",
                    vcpus=Decimal(vcpus),
                    ram_gib=Decimal(gb),
                    source=_SOURCE,
                    confidence="official",
                    captured_at=agora,
                )
            )

    return pontos
