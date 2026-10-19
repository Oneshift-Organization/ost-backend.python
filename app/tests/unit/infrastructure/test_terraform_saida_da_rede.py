"""Saída da rede espelhada: gateway de internet e NAT (PROXIMAS_EXECUCOES, item 12)."""

from __future__ import annotations

import ipaddress
import re

import pytest

from app.tests.unit.infrastructure.terraform_fixtures import carregar, com_topologia, pacote

_PARES = (("aws", "oci"), ("aws", "gcp"), ("aws", "azure"), ("azure", "aws"))
_NAT = {
    "oci": "oci_core_nat_gateway",
    "aws": "aws_nat_gateway",
    "azure": "azurerm_nat_gateway",
    "gcp": "google_compute_router_nat",
}


@pytest.mark.parametrize(("origem", "destino"), _PARES)
def test_rede_espelhada_sai_pela_nat(origem: str, destino: str) -> None:
    main = pacote(com_topologia(origem), destino)["main.tf"]
    assert f'resource "{_NAT[destino]}"' in main
    assert "A VPN entre as nuvens não é criada aqui" in main


def test_na_aws_o_nat_fica_numa_subrede_publica_livre() -> None:
    main = pacote(com_topologia("azure"), "aws")["main.tf"]
    blocos = re.findall(r'resource "aws_subnet" "(\w+)" \{(.*?)\n\}', main, re.S)
    cidr = re.compile(r'cidr_block\s*=\s*"([^"]+)"')
    cidrs = {nome: m.group(1) for nome, corpo in blocos if (m := cidr.search(corpo))}
    nat = next(n for n in cidrs if "saida_nat" in n)
    assert cidrs[nat].endswith("/28")
    outras = [ipaddress.ip_network(c) for n, c in cidrs.items() if n != nat]
    assert not any(ipaddress.ip_network(cidrs[nat]).overlaps(o) for o in outras)
    assert re.search(r'subnet_id\s*=\s*aws_subnet\.' + nat + r"\.id", main)
    assert 'resource "aws_route_table_association"' in main


def test_sem_topologia_nada_muda() -> None:
    main = pacote(carregar("payload_aws"), "oci")["main.tf"]
    assert "oci_core_nat_gateway" not in main
    assert "oci_core_internet_gateway" not in main
