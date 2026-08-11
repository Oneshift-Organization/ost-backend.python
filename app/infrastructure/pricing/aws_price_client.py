"""Cliente de preços on-demand de EC2 (público, sem auth).

## Por que esta fonte e não a Price List API

A Price List Bulk API oficial
(``pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/{região}/index.json``)
é o caminho canônico, mas o arquivo de uma única região passa de 1 GB: produtos e
termos de todas as combinações de SO, tenancy e modelo de compra. Baixar e
percorrer isso num sync offline é caro e frágil.

O endpoint usado aqui é o mesmo que alimenta a calculadora pública da AWS. Vem
gzipado, tem ~57 KB por região, e já traz **preço, vCPU e memória juntos** — que
é exatamente o que o packer precisa. Sem credencial, sem SDK.

O preço é on-demand, Linux, shared tenancy, sem contrato — a mesma base do que
buscamos para Azure e OCI, então a comparação cross-cloud continua entre iguais.

Sem dependência externa (urllib da stdlib). Roda offline, nunca no caminho
crítico da análise.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.entities.cloud_price_point import CloudPricePoint

_BASE = (
    "https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/"
    "ec2-ondemand-without-sec-sel/{regiao}/Linux/index.json"
)
_SOURCE = "aws_pricing_calculator"

# Nome da região como a fonte o escreve. A API de preços usa o rótulo humano,
# não o código (`us-east-1`), então precisamos do mapa.
_REGIOES = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-2": "US West (Oregon)",
    "sa-east-1": "South America (Sao Paulo)",
    "eu-west-1": "EU (Ireland)",
    "eu-central-1": "EU (Frankfurt)",
}

# Famílias de propósito geral, compute e memória — as workhorses que servem de
# alvo de migração. Especializadas ficam de fora porque distorcem o packing:
# uma família de GPU tem preço por vCPU altíssimo e seria escolhida como
# "cara demais" ou, pior, entraria num mix por acaso.
#
#   m, t  propósito geral (t é burstable — ver nota abaixo)
#   c     compute-optimized
#   r     memory-optimized
#
# Fora: p/g (GPU), i/d/h (storage denso), x/z (memória extrema), u (SAP HANA),
# f (FPGA), vt/inf/trn (aceleradores).
_FAMILIAS_GERAIS = frozenset({"m", "c", "r", "t"})

# Burstable (t2/t3/t4g) tem baseline de CPU, não vCPU dedicado. Como alvo de
# migração de um pool com uso sustentado, é armadilha: o preço é atraente e a
# capacidade real, não. Incluímos porque cargas pequenas legitimamente cabem —
# mas o packer decide, e o rótulo fica no service_name para quem for auditar.
_BURSTABLE = frozenset({"t2", "t3", "t3a", "t4g"})


def _get_json(url: str, *, timeout: float) -> dict[str, Any]:
    """GET que lida com a resposta gzipada.

    A fonte devolve gzip independentemente do `Accept-Encoding`, e o urllib não
    descomprime sozinho — sem isto o `json.loads` estoura em
    `UnicodeDecodeError: invalid start byte`, que não diz nada sobre a causa.
    """
    req = urllib.request.Request(  # noqa: S310 - host fixo (awsstatic.com)
        url, headers={"User-Agent": "oneshift-pricing/1.0", "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host fixo
        bruto = resp.read()

    if bruto[:2] == b"\x1f\x8b":  # magic number do gzip
        bruto = gzip.decompress(bruto)
    dados: dict[str, Any] = json.loads(bruto)
    return dados


def _familia(instance_type: str) -> str:
    """`m5n.12xlarge` → `m5n`. Vazio se o formato não for reconhecido."""
    return instance_type.split(".", 1)[0] if "." in instance_type else ""


def _e_familia_geral(instance_type: str) -> bool:
    familia = _familia(instance_type)
    if not familia:
        return False
    if familia in _BURSTABLE:
        return True
    # A letra inicial define a classe; o resto é geração e variante (n = rede,
    # a = AMD, g = Graviton). Todas servem como alvo.
    return familia[0] in _FAMILIAS_GERAIS


def _decimal(valor: object) -> Decimal | None:
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return d if d > 0 else None


def _ram_gib(texto: object) -> Decimal | None:
    """`"192 GiB"` → `Decimal("192")`.

    Alguns itens trazem `NA` ou vêm sem a unidade; ambos viram None em vez de
    estourar, porque um SKU sem memória conhecida não serve como candidato e
    deve ser descartado em silêncio.
    """
    if not isinstance(texto, str):
        return None
    partes = texto.replace(",", "").split()
    return _decimal(partes[0]) if partes else None


def fetch_aws_ec2_prices(
    *, region: str = "us-east-1", timeout: float = 60.0
) -> list[CloudPricePoint]:
    """Preços on-demand de EC2 Linux para uma região.

    Devolve um `CloudPricePoint` por tipo de instância das famílias gerais, com
    specs (vCPU/RAM) vindos da mesma resposta — assim o matcher os consome como
    candidatos sem precisar casar chave com o catálogo seed.

    Região desconhecida levanta `ValueError`: falhar alto é melhor que devolver
    lista vazia e o sync reportar "0 preços" como se a AWS não tivesse nenhum.
    """
    rotulo = _REGIOES.get(region)
    if rotulo is None:
        raise ValueError(
            f"região AWS não mapeada: {region!r}. Conhecidas: {sorted(_REGIOES)}"
        )

    url = _BASE.format(regiao=urllib.parse.quote(rotulo))
    payload = _get_json(url, timeout=timeout)
    agora = datetime.now(UTC).replace(tzinfo=None)

    itens = payload.get("regions", {}).get(rotulo, {})
    pontos: dict[str, CloudPricePoint] = {}

    for item in itens.values():
        tipo = item.get("Instance Type")
        if not isinstance(tipo, str) or not _e_familia_geral(tipo):
            continue

        preco = _decimal(item.get("price"))
        vcpus = _decimal(item.get("vCPU"))
        ram = _ram_gib(item.get("Memory"))
        if preco is None or vcpus is None or ram is None:
            continue

        # A fonte pode listar o mesmo tipo mais de uma vez (variações de
        # metadado). Mantemos o mais barato: é o preço on-demand real, e
        # duplicata cara inflaria a cotação sem motivo.
        existente = pontos.get(tipo)
        if existente is not None and existente.price <= preco:
            continue

        pontos[tipo] = CloudPricePoint(
            provider="aws",
            sku_key=tipo,
            service_name="Amazon EC2" + (" (burstable)" if _familia(tipo) in _BURSTABLE else ""),
            service_type="virtual_server",
            region=region,
            price=preco,
            currency="USD",
            unit="instance_hour",
            vcpus=vcpus,
            ram_gib=ram,
            source=_SOURCE,
            confidence="official",
            captured_at=agora,
        )

    return list(pontos.values())
