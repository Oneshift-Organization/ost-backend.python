"""Pacote Terraform do escopo (T5) sobre os mocks das quatro nuvens."""

from __future__ import annotations

import os
import shutil
from decimal import Decimal
from typing import Any

import pytest

from app.core import config
from app.domain.services.terraform_topologia.escopo import (
    LigacaoEntreNuvens,
    RecorteDoEscopo,
    recortar_ao_escopo,
)
from app.domain.services.topologia.escopo import ENTRA, SAI, LigacaoNaFronteira
from app.domain.services.topologia.modelo import Natureza
from app.infrastructure.terraform.validate import validar
from app.tests.unit.infrastructure.terraform_fixtures import com_topologia, entrada, pacote
from app.use_cases.terraform.build_blueprint import montar_blueprint

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


# ── só o serviço (pedido de 18/10) ──────────────────────────────────────

_FRONTEIRA = (
    LigacaoNaFronteira(
        SAI, "AmazonEC2 (loja)", "AmazonRDS (loja)", Natureza.DADOS, ("tcp/5432",), True
    ),
    LigacaoNaFronteira(
        ENTRA, "AWSELB (loja)", "AmazonEC2 (loja)", Natureza.ENCAMINHAMENTO, ("tcp/8080",), True
    ),
    LigacaoNaFronteira(
        SAI, "AmazonEC2 (loja)", "AmazonEC2 (pagamentos)", Natureza.SERVICO, ("tcp/443",), False
    ),
)


def _so_a_vm(
    payload: dict[str, Any], fronteira: tuple[LigacaoNaFronteira, ...]
) -> RecorteDoEscopo:
    pool = payload["topologia"]["detalhe"]["posicionamento"][0]
    fatia = pool["posicionamentos"][0]
    return RecorteDoEscopo(
        aplicacoes=("loja",),
        unidades=1,
        maquinas={(fatia["subrede"], pool["familia"].upper()): 1},
        bancos=frozenset(),
        bancos_por_classe={},
        custos={},
        somente_os_servicos=True,
        servicos=("AmazonEC2",),
        fronteira=fronteira,
        avisos=("AWSELB não vira recurso neste pacote (teste)",),
    )


def _bancos(arquivos: dict[str, str]) -> int:
    return sum(
        texto.count(f'resource "{tipo}"')
        for nome, texto in arquivos.items()
        if nome.endswith(".tf")
        for tipo in (
            "aws_db_instance",
            "google_sql_database_instance",
            "azurerm_postgresql_flexible_server",
            "oci_database_autonomous_database",
        )
    )


@pytest.mark.parametrize(("origem", "destino"), list(zip(_ORIGENS, _DESTINOS, strict=True)))
def test_so_a_vm_cria_a_maquina_sem_banco_e_diz_a_fronteira(origem: str, destino: str) -> None:
    payload = recortar_ao_escopo(
        com_topologia(origem), _so_a_vm(com_topologia(origem), _FRONTEIRA)
    )
    arquivos = pacote(payload, destino)
    assert _vms(arquivos) == 1
    assert _bancos(arquivos) == 0
    readme = arquivos["README.md"]
    assert "Só os serviços escolhidos: **AmazonEC2**" in readme
    assert "### O que fica do outro lado (fronteira)" in readme
    assert (
        "- **forte** — AmazonEC2 (loja) migra e acessa dados em AmazonRDS (loja), que fica na "
        "origem (tcp/5432)" in readme
    )
    # A chamada fraca não leva a marca de forte
    assert "- AmazonEC2 (loja) migra e chama AmazonEC2 (pagamentos)" in readme
    blueprint = montar_blueprint(payload, entrada(destino))
    assert blueprint.warnings[0].startswith(
        "Só os serviços escolhidos: 3 dependência(s) atravessam a fronteira (2 forte(s)"
    )
    assert any("AWSELB (loja) fica na origem e encaminha tráfego" in a for a in blueprint.warnings)
    assert "AWSELB não vira recurso neste pacote (teste)" in blueprint.unsupported_items


def test_so_o_servico_sem_dependencia_diz_que_nada_atravessa() -> None:
    payload = recortar_ao_escopo(com_topologia("aws"), _so_a_vm(com_topologia("aws"), ()))
    readme = pacote(payload, "oci")["README.md"]
    assert "Nenhuma dependência de rede atravessa a fronteira" in readme
    blueprint = montar_blueprint(payload, entrada("oci"))
    assert any("nenhuma dependência de rede atravessa" in a for a in blueprint.warnings)


@pytest.mark.skipif(
    shutil.which("terraform") is None or not os.environ.get("TF_PLUGIN_CACHE_DIR"),
    reason="precisa do binário do Terraform e de TF_PLUGIN_CACHE_DIR",
)
@pytest.mark.parametrize(("origem", "destino"), list(zip(_ORIGENS, _DESTINOS, strict=True)))
def test_pacote_de_so_a_vm_passa_no_validate(
    monkeypatch: pytest.MonkeyPatch, origem: str, destino: str
) -> None:
    monkeypatch.setenv("TERRAFORM_VALIDATE_ENABLED", "true")
    config.get_settings.cache_clear()
    try:
        payload = recortar_ao_escopo(
            com_topologia(origem), _so_a_vm(com_topologia(origem), _FRONTEIRA)
        )
        r = validar(pacote(payload, destino), formatado=True)
    finally:
        config.get_settings.cache_clear()
    assert r.validado is True, r.erros
