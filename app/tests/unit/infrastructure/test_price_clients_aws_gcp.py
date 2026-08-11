"""Parsing dos clientes de preço AWS e GCP.

Sem rede: testes que dependem de API externa falham por motivo errado (a AWS
mudou de região, o CI está sem saída) e ninguém confia neles depois da terceira
vez. O que interessa travar é a transformação — e ela é pura.

Os payloads de exemplo têm o formato real, capturado das respostas de produção.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.infrastructure.pricing import aws_price_client as aws
from app.infrastructure.pricing import gcp_price_client as gcp

# ── AWS: seleção de famílias ────────────────────────────────────────────


@pytest.mark.parametrize(
    "tipo",
    ["m5.large", "m8i.32xlarge", "c6a.4xlarge", "r5n.xlarge", "t3.micro", "t4g.nano"],
)
def test_familias_workhorse_entram(tipo: str) -> None:
    assert aws._e_familia_geral(tipo)


@pytest.mark.parametrize(
    "tipo",
    [
        "p4d.24xlarge",   # GPU
        "g5.xlarge",      # GPU
        "i3.large",       # storage denso
        "x2gd.medium",    # memória extrema
        "u-6tb1.metal",   # SAP HANA
        "inf2.xlarge",    # acelerador
    ],
)
def test_familias_especializadas_ficam_de_fora(tipo: str) -> None:
    """Não é purismo: uma família de GPU tem preço por vCPU altíssimo e
    distorceria o packing — entraria num mix por acaso ou faria o provedor
    parecer caro sem motivo."""
    assert not aws._e_familia_geral(tipo)


def test_tipo_sem_ponto_nao_quebra() -> None:
    """Entrada malformada não pode derrubar o sync inteiro."""
    assert not aws._e_familia_geral("m5large")
    assert aws._familia("m5large") == ""


# ── AWS: parsing de campos ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("192 GiB", Decimal("192")),
        ("0.5 GiB", Decimal("0.5")),
        ("1,024 GiB", Decimal("1024")),  # separador de milhar
        ("NA", None),
        ("", None),
        (None, None),
        (42, None),
    ],
)
def test_ram_gib(bruto: object, esperado: Decimal | None) -> None:
    assert aws._ram_gib(bruto) == esperado


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("2.8560000000", Decimal("2.8560000000")),
        ("0", None),      # preço zero não é preço
        ("-1", None),
        ("abc", None),
        (None, None),
    ],
)
def test_decimal_recusa_valor_inutil(bruto: object, esperado: Decimal | None) -> None:
    assert aws._decimal(bruto) == esperado


def test_regiao_desconhecida_falha_alto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falhar é melhor que devolver lista vazia: o sync reportaria
    'aws: fetched=0' como se a AWS não tivesse preço nenhum."""
    with pytest.raises(ValueError, match="região AWS não mapeada"):
        aws.fetch_aws_ec2_prices(region="marte-central-1")


def test_monta_price_points_do_payload_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """Formato real da fonte, com um caso de duplicata e um de lixo."""
    rotulo = "US East (N. Virginia)"
    payload = {
        "regions": {
            rotulo: {
                "a": {
                    "Instance Type": "m5.large",
                    "price": "0.0960000000",
                    "vCPU": "2",
                    "Memory": "8 GiB",
                },
                # mesmo tipo, mais caro — deve ser descartado
                "b": {
                    "Instance Type": "m5.large",
                    "price": "0.1200000000",
                    "vCPU": "2",
                    "Memory": "8 GiB",
                },
                "c": {  # GPU: fora do filtro
                    "Instance Type": "p4d.24xlarge",
                    "price": "32.77",
                    "vCPU": "96",
                    "Memory": "1152 GiB",
                },
                "d": {  # sem memória: inútil como candidato
                    "Instance Type": "c5.xlarge",
                    "price": "0.17",
                    "vCPU": "4",
                    "Memory": "NA",
                },
            }
        }
    }
    monkeypatch.setattr(aws, "_get_json", lambda *a, **k: payload)

    pontos = aws.fetch_aws_ec2_prices(region="us-east-1")

    assert len(pontos) == 1
    p = pontos[0]
    assert p.sku_key == "m5.large"
    assert p.price == Decimal("0.0960000000"), "manteve o mais barato da duplicata"
    assert p.provider == "aws"
    assert p.vcpus == Decimal("2")
    assert p.ram_gib == Decimal("8")
    assert p.confidence == "official"
    assert p.source == "aws_pricing_calculator"
    assert p.captured_at is not None, "sem captura, 'official' não diz nada"


def test_burstable_fica_marcado_no_nome() -> None:
    """t3 tem baseline de CPU, não vCPU dedicado. Entra como candidato, mas
    quem auditar precisa conseguir ver que é burstable."""
    assert aws._familia("t3.micro") in aws._BURSTABLE
    assert aws._familia("m5.large") not in aws._BURSTABLE


# ── GCP: degradação sem credencial ──────────────────────────────────────


def test_sem_api_key_devolve_vazio_sem_estourar() -> None:
    """A Catalog API recusa chamador anônimo (403) e não há fonte pública
    alternativa — verificado, não suposto.

    Um sync sem credencial de GCP não pode derrubar os provedores que estavam
    funcionando: degrada para 'não coletei nada'.
    """
    assert gcp.fetch_gcp_compute_prices(api_key="") == []


# ── GCP: parsing ────────────────────────────────────────────────────────


def test_preco_ignora_faixa_gratuita() -> None:
    """A primeira faixa costuma ser 0 (tier gratuito). Pegar `[0]` cegamente
    faria a família inteira parecer de graça."""
    sku = {
        "pricingInfo": [
            {
                "pricingExpression": {
                    "tieredRates": [
                        {"unitPrice": {"units": "0", "nanos": 0}},
                        {"unitPrice": {"units": "0", "nanos": 31611000}},
                    ]
                }
            }
        ]
    }
    assert gcp._preco_unitario(sku) == Decimal("0.031611")


def test_preco_de_sku_malformado_e_none() -> None:
    assert gcp._preco_unitario({}) is None
    assert gcp._preco_unitario({"pricingInfo": []}) is None


def test_classifica_taxa_de_cpu_e_ram() -> None:
    def _sku(desc: str, grupo: str) -> dict:
        return {
            "description": desc,
            "serviceRegions": ["us-central1"],
            "category": {
                "resourceFamily": "Compute",
                "resourceGroup": grupo,
                "usageType": "OnDemand",
            },
        }

    assert gcp._classificar(_sku("N2 Instance Core running in Americas", "CPU"), "us-central1") == (
        "N2",
        "cpu",
    )
    assert gcp._classificar(_sku("E2 Instance Ram running in Americas", "RAM"), "us-central1") == (
        "E2",
        "ram",
    )


def test_descarta_o_que_nao_e_on_demand_da_regiao() -> None:
    """Preemptible e committed use têm preço menor e não são comparáveis com o
    on-demand que buscamos nos outros provedores."""
    base = {
        "description": "N2 Instance Core running in Americas",
        "serviceRegions": ["us-central1"],
        "category": {
            "resourceFamily": "Compute",
            "resourceGroup": "CPU",
            "usageType": "Preemptible",
        },
    }
    assert gcp._classificar(base, "us-central1") is None

    outra_regiao = {**base, "category": {**base["category"], "usageType": "OnDemand"}}
    assert gcp._classificar(outra_regiao, "europe-west1") is None

    disco = {
        **base,
        "category": {**base["category"], "usageType": "OnDemand", "resourceGroup": "PDStandard"},
    }
    assert gcp._classificar(disco, "us-central1") is None
