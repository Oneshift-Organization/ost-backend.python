"""Pacote Terraform do escopo (T5) sobre os mocks das quatro nuvens."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.services.terraform_topologia.escopo import (
    LigacaoEntreNuvens,
    RecorteDoEscopo,
    recortar_ao_escopo,
)
from app.tests.unit.infrastructure.terraform_fixtures import com_topologia, pacote

_ORIGENS = ("aws", "azure", "gcp", "oci")
_DESTINOS = ("gcp", "aws", "oci", "azure")


def _recorte(payload: dict[str, Any]) -> RecorteDoEscopo:
    """A primeira fatia do primeiro pool: uma aplicação de uma subrede só."""
    pool = payload["topologia"]["detalhe"]["posicionamento"][0]
    fatia = pool["posicionamentos"][0]
    return RecorteDoEscopo(
        aplicacoes=("loja",),
        unidades=1,
        maquinas={(fatia["subrede"], pool["familia"].upper()): 1},
        bancos=frozenset(),
        bancos_por_classe={},
        custos={"armazenamento_ssd": Decimal("40")},
        ordens=((1, ("loja",)),),
        antes=(LigacaoEntreNuvens("pagamentos", 1, ("443",)),),
        para_tras=(LigacaoEntreNuvens("relatorios", 3, ()),),
    )


def _vms(arquivos: dict[str, str]) -> int:
    return sum(
        texto.count('resource "aws_instance"')
        + texto.count('resource "google_compute_instance"')
        + texto.count('resource "azurerm_linux_virtual_machine"')
        + texto.count('resource "oci_core_instance"')
        for nome, texto in arquivos.items()
        if nome.endswith(".tf")
    )


@pytest.mark.parametrize(("origem", "destino"), list(zip(_ORIGENS, _DESTINOS, strict=True)))
def test_pacote_do_escopo_e_menor_e_o_readme_diz_o_que_falta(origem: str, destino: str) -> None:
    payload = com_topologia(origem)
    conta = pacote(payload, destino)
    escopo = pacote(recortar_ao_escopo(payload, _recorte(payload)), destino)
    assert 0 < _vms(escopo) <= _vms(conta)
    assert len(escopo["main.tf"]) < len(conta["main.tf"])
    readme = escopo["README.md"]
    assert "## Escopo deste pacote" in readme
    assert "**loja**" in readme
    assert "### Conectividade entre nuvens a prever" in readme
    assert "- pagamentos (ordem 1), portas 443" in readme
    assert "- relatorios (ordem 3)" in readme
    assert "## Escopo deste pacote" not in conta["README.md"]
