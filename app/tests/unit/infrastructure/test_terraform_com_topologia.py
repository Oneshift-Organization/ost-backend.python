"""Pacote Terraform com a topologia do inventário (RI15), sobre os mocks reais.

Os payloads são os da caracterização mais a topologia que a análise congelou do
inventário do mesmo mock. O que se verifica aqui é o que a topologia acrescenta:
disco raiz, volumes avulsos, versão do banco, README e manifesto. Sem topologia,
nada muda — é o teste de caracterização.
"""

from __future__ import annotations

import json
import os
import re
import shutil

import pytest

from app.core import config
from app.infrastructure.terraform.renderers.base import texto_seguro
from app.infrastructure.terraform.validate import validar
from app.tests.unit.infrastructure.terraform_fixtures import (
    carregar,
    com_topologia,
    entrada,
    pacote,
)
from app.use_cases.terraform.generate_terraform import gerar_pacote

PROVEDORES = ("aws", "gcp", "azure", "oci")
PARES = [(o, d) for o in PROVEDORES for d in PROVEDORES if o != d]


def _bloco(main: str, tipo: str, nome: str) -> str:
    inicio = main.index(f'resource "{tipo}" "{nome}" {{')
    return main[inicio : main.index("\n}\n", inicio)]


# ── README e manifesto ──────────────────────────────────────────────────


@pytest.mark.parametrize(("origem", "destino"), PARES)
def test_readme_traz_a_topologia_de_origem(origem: str, destino: str) -> None:
    readme = pacote(com_topologia(origem), destino)["README.md"]
    assert "## Topologia de origem" in readme
    assert "### Ordem de migração" in readme
    assert "1. Onda 0 — fundação:" in readme
    # Sem topologia a seção não existe (a caracterização trava o resto).
    assert "Topologia de origem" not in pacote(carregar(f"payload_{origem}"), destino)["README.md"]


def test_readme_da_aws_tem_lacuna_dependencias_e_riscos() -> None:
    readme = pacote(com_topologia("aws"), "gcp")["README.md"]
    assert "82% do custo com recurso foi achado no inventário" in readme
    assert "- conta 333333333333, us-east-1: USD 12,265.40/mês" in readme
    assert "| monitoramento (prod) · us-east-1 | relatorios (prod) · us-east-1 | tcp/5432 |" in (
        readme
    )
    assert "| alta | Porta de banco aberta para a internet | 24 |" in readme
    # A contagem é a da análise inteira (8), não a da lista resumida (7).
    assert "Mais 8 achado(s) informativo(s)" in readme
    assert "não é replicada no destino" in readme
    assert "18 balanceador(es)" in readme


def test_manifesto_diz_de_qual_inventario_saiu() -> None:
    payload = com_topologia("aws")
    manifesto = json.loads(gerar_pacote(payload, entrada("gcp")).arquivos["manifest.json"])
    assert manifesto["topologia"]["fingerprint"] == payload["topologia"]["fingerprint"]
    assert manifesto["topologia"]["inventario_id"] == payload["topologia"]["inventario_id"]
    sem = json.loads(
        gerar_pacote(carregar("payload_aws"), entrada("gcp")).arquivos["manifest.json"]
    )
    assert "topologia" not in sem


def test_nome_do_inventario_nao_vira_codigo() -> None:
    """RI17: quebra de linha num comentário do `.tf` vira HCL na linha seguinte."""
    assert texto_seguro("app\nresource x {}\x00") == "app resource x {}"
    payload = com_topologia("oci")
    payload["topologia"]["resumo"]["dependencias_entre_ondas"] = [
        {"de": "a\n## injetado", "para": "b|c", "portas": ["tcp/1"]}
    ]
    readme = pacote(payload, "aws")["README.md"]
    assert "\n## injetado" not in readme
    assert "| a ## injetado | b\\|c | tcp/1 |" in readme


# ── disco raiz, volumes avulsos, versão ─────────────────────────────────


def test_boot_da_aws_vira_disco_raiz_em_cada_destino() -> None:
    payload = com_topologia("aws")
    gcp = pacote(payload, "gcp")["main.tf"]
    assert re.search(
        r'type\s*=\s*"pd-balanced"', _bloco(gcp, "google_compute_instance", "vm_c7g_us_east_1")
    )
    assert re.search(r"size\s*=\s*\d+", _bloco(gcp, "google_compute_instance", "vm_c7g_us_east_1"))
    azure = pacote(payload, "azure")["main.tf"]
    assert re.search(
        r"disk_size_gb\s*=\s*\d+",
        _bloco(azure, "azurerm_linux_virtual_machine", "vm_c7g_us_east_1"),
    )
    oci = pacote(payload, "oci")["main.tf"]
    assert "boot_volume_size_in_gbs" in _bloco(oci, "oci_core_instance", "vm_c7g_us_east_1")


def test_boot_do_gcp_vira_raiz_gp3_na_aws() -> None:
    main = pacote(com_topologia("gcp"), "aws")["main.tf"]
    vm = _bloco(main, "aws_instance", "vm_e2_us_central1")
    assert "root_block_device {" in vm
    assert re.search(r'volume_type\s*=\s*"gp3"', vm)


def test_volumes_avulsos_da_aws_sao_o_que_o_inventario_nao_explica() -> None:
    """48.626 GB em 99 volumes medidos, menos 74 de boot e 7 sem anexo: 18 de 490 GB."""
    main = pacote(com_topologia("aws"), "gcp")["main.tf"]
    disco = _bloco(main, "google_compute_disk", "disco_storage_ssd")
    assert re.search(r"count\s*=\s*18\b", disco)
    assert re.search(r"size\s*=\s*490\b", disco)
    readme = pacote(com_topologia("aws"), "gcp")["README.md"]
    assert "36,334 GB de boot (74 volume(s)) viraram disco raiz" in readme


def test_aviso_de_disco_so_fala_do_que_saiu() -> None:
    """Na Azure o mock não tem boot com custo: o aviso não diz "0 GB de boot"."""
    readme = pacote(com_topologia("azure"), "aws")["README.md"]
    assert "0 GB de boot" not in readme
    assert "1,024 GB sem anexo (2 volume(s)) não foram gerados" in readme


def test_versao_medida_vira_default() -> None:
    variaveis = pacote(com_topologia("aws"), "gcp")["variables.tf"]
    bloco = variaveis[variaveis.index('variable "database_version_postgresql"') :]
    bloco = bloco[: bloco.index("\n}\n")]
    assert re.search(r'default\s*=\s*"POSTGRES_1[2-6]"', bloco)
    assert "versão medida no inventário" in bloco
    # A Azure (PostgreSQL 14 no Flexible Server) para a AWS.
    variaveis = pacote(com_topologia("azure"), "aws")["variables.tf"]
    assert re.search(r'default\s*=\s*"14"', variaveis)


def test_com_versao_medida_nada_manda_preencher_a_versao() -> None:
    """A premissa do motor e o comentário do banco diziam "a versão não aparece no
    billing: preencha" mesmo com o default vindo do inventário."""
    arquivos = pacote(com_topologia("aws"), "gcp")
    assert "A versão não aparece no billing" not in arquivos["README.md"]
    assert "A versão não aparece no billing" not in arquivos["main.tf"]
    assert "Versão medida no inventário" in arquivos["main.tf"]
    sem = pacote(carregar("payload_aws"), "gcp")
    assert "A versão não aparece no billing" in sem["main.tf"]


# ── `terraform validate` nos 12 pares ───────────────────────────────────

_precisa_binario = pytest.mark.skipif(
    shutil.which("terraform") is None or not os.environ.get("TF_PLUGIN_CACHE_DIR"),
    reason="precisa do binário do Terraform e de TF_PLUGIN_CACHE_DIR",
)


@pytest.fixture
def _validacao_ligada(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERRAFORM_VALIDATE_ENABLED", "true")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


@_precisa_binario
@pytest.mark.parametrize(("origem", "destino"), PARES)
def test_pacote_com_topologia_e_valido(_validacao_ligada, origem: str, destino: str) -> None:
    r = validar(pacote(com_topologia(origem), destino), formatado=True)
    assert r.validado is True, r.erros
