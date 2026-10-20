"""Entrada do banco fora da AWS (PROXIMAS_EXECUCOES, item 13)."""

from __future__ import annotations

import re

from app.tests.unit.infrastructure.terraform_fixtures import com_topologia, pacote


def test_na_oci_o_banco_leva_os_nsgs_espelhados_da_origem() -> None:
    main = pacote(com_topologia("aws"), "oci")["main.tf"]
    bancos = re.findall(r'resource "oci_psql_db_system" "\w+" \{(.*?)\n\}', main, re.S)
    assert bancos
    for corpo in bancos:
        nsgs = re.search(r"nsg_ids\s*=\s*\[([^\]]+)\]", corpo)
        assert nsgs is not None
        for nome in re.findall(r"oci_core_network_security_group\.(\w+)\.id", nsgs.group(1)):
            assert f'resource "oci_core_network_security_group" "{nome}"' in main


def test_no_gcp_o_peering_do_banco_leva_as_rotas() -> None:
    main = pacote(com_topologia("aws"), "gcp")["main.tf"]
    bloco = re.search(
        r'resource "google_compute_network_peering_routes_config" "\w+" \{(.*?)\n\}', main, re.S
    )
    assert bloco is not None
    assert re.search(r"export_custom_routes\s*=\s*true", bloco.group(1))
    assert re.search(r"import_custom_routes\s*=\s*true", bloco.group(1))
