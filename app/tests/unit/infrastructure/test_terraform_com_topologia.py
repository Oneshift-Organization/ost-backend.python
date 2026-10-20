"""Pacote Terraform com a topologia do inventário (RI15), sobre os mocks reais.

Os payloads são os da caracterização mais a topologia que a análise congelou do
inventário do mesmo mock. O que se verifica aqui é o que a topologia acrescenta:
disco raiz, volumes avulsos, versão do banco, README e manifesto. Sem topologia,
nada muda — é o teste de caracterização.
"""

from __future__ import annotations

import ipaddress
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
from app.use_cases.terraform.build_blueprint import montar_blueprint
from app.use_cases.terraform.generate_terraform import gerar_pacote

PROVEDORES = ("aws", "gcp", "azure", "oci")
PARES = [(o, d) for o in PROVEDORES for d in PROVEDORES if o != d]


def _bloco(main: str, tipo: str, nome: str) -> str:
    """O bloco do recurso; ``nome`` terminado em `_` pega a primeira fatia do pool."""
    alvo = f'resource "{tipo}" "{nome}' + ("" if nome.endswith("_") else '" {')
    inicio = main.index(alvo)
    return main[inicio : main.index("\n}\n", inicio)]


def _blocos(main: str, tipo: str) -> dict[str, str]:
    return {
        m.group(1): main[m.start() : main.index("\n}\n", m.start())]
        for m in re.finditer(rf'resource "{tipo}" "([^"]+)" {{', main)
    }


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
    vm = _bloco(gcp, "google_compute_instance", "vm_c7g_us_east_1_")
    assert re.search(r'type\s*=\s*"pd-balanced"', vm)
    assert re.search(r"size\s*=\s*\d+", vm)
    azure = pacote(payload, "azure")["main.tf"]
    assert re.search(
        r"disk_size_gb\s*=\s*\d+",
        _bloco(azure, "azurerm_linux_virtual_machine", "vm_c7g_us_east_1_"),
    )
    oci = pacote(payload, "oci")["main.tf"]
    assert "boot_volume_size_in_gbs" in _bloco(oci, "oci_core_instance", "vm_c7g_us_east_1_")


def test_boot_do_gcp_vira_raiz_gp3_na_aws() -> None:
    main = pacote(com_topologia("gcp"), "aws")["main.tf"]
    vm = _bloco(main, "aws_instance", "vm_e2_us_central1_")
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


# ── subredes espelhadas e posicionamento (I8c) ──────────────────────────


def test_subredes_espelhadas_com_enderecamento_novo() -> None:
    """As 8 subredes /20 da AWS com VM posicionada viram 8 /20 dentro da faixa
    pedida, fora da subrede padrão e sem sobrepor."""
    main = pacote(com_topologia("aws"), "gcp")["main.tf"]
    espelhadas = {
        n: b
        for n, b in _blocos(main, "google_compute_subnetwork").items()
        if n != "subnet_principal"
    }
    assert len(espelhadas) == 8
    faixas = []
    for b in espelhadas.values():
        achado = re.search(r'ip_cidr_range\s*=\s*"([^"]+)"', b)
        assert achado
        faixas.append(ipaddress.ip_network(achado.group(1)))
    rede, padrao = ipaddress.ip_network("10.50.0.0/16"), ipaddress.ip_network("10.50.0.0/24")
    assert all(f.subnet_of(rede) and not f.overlaps(padrao) and f.prefixlen == 20 for f in faixas)
    assert len({str(f) for f in faixas}) == 8
    assert main.count("# Espelha a subrede de origem") == 8


def test_aws_pega_a_zona_pela_posicao() -> None:
    main = pacote(com_topologia("gcp"), "aws")["main.tf"]
    assert 'data "aws_availability_zones" "disponiveis"' in main
    subredes = _blocos(main, "aws_subnet")
    assert re.search(r'availability_zone\s*=\s*"sa-east-1a"', subredes["subnet_principal"])
    espelhadas = [b for n, b in subredes.items() if n != "subnet_principal"]
    assert espelhadas
    assert all("element(data.aws_availability_zones.disponiveis.names" in b for b in espelhadas)


@pytest.mark.parametrize(("origem", "destino"), PARES)
def test_vms_do_pool_somam_o_aprovado(origem: str, destino: str) -> None:
    """O posicionamento muda onde as VMs ficam, nunca quantas (RI15.5)."""

    def por_pool(payload: dict) -> dict[str, int]:
        soma: dict[str, int] = {}
        for r in montar_blueprint(payload, entrada(destino)).resources:
            if r.resource_type.value == "compute" and not r.configuration.get("disk_only"):
                pool = str(r.configuration.get("pool") or r.logical_name)
                soma[pool] = soma.get(pool, 0) + int(r.configuration["instances"])
        return soma

    assert por_pool(com_topologia(origem)) == por_pool(carregar(f"payload_{origem}"))


def test_banco_na_aws_ganha_grupo_de_subredes_completado() -> None:
    """O Flexible Server estava numa subrede só (regional): o grupo de subredes do
    RDS é completado com uma de outra zona, e a premissa diz isso."""
    arquivos = pacote(com_topologia("azure"), "aws")
    grupo = _bloco(arquivos["main.tf"], "aws_db_subnet_group", "banco_postgresql_standard_d4ds_v5_")
    assert "aws_subnet.subnet_dados.id" in grupo
    assert grupo.count("aws_subnet.") == 2
    banco = _bloco(arquivos["main.tf"], "aws_db_instance", "banco_postgresql_standard_d4ds_v5")
    assert "db_subnet_group_name" in banco
    assert "o grupo foi completado com" in arquivos["README.md"]


def test_redes_de_origem_contadas_pelo_inventario() -> None:
    """No GCP a VPC não tem CIDR: a contagem vem do inventário, não dos CIDRs."""
    payload = com_topologia("gcp")
    redes = payload["topologia"]["resumo"]["contagens"]["fundacao"]["rede"]
    assert redes > 1
    readme = pacote(payload, "aws")["README.md"]
    assert f"As {redes} redes de origem viram uma" in readme


def test_aviso_de_subrede_sem_maquina_diz_o_cidr() -> None:
    readme = pacote(com_topologia("gcp"), "aws")["README.md"]
    assert "Ficaram sem máquina: app-us-central1 10.2" in readme


def test_ip_publico_so_onde_o_destino_e_publico() -> None:
    payload = com_topologia("aws")
    publicas = [s for s in payload["topologia"]["detalhe"]["rede"]["subredes"] if s["publica"]]
    assert publicas, "o mock da AWS tem subrede pública com recurso posicionado"
    com = pacote(payload, "oci", network={"cidr": "10.50.0.0/16", "public_access": True})
    sem = pacote(payload, "oci", network={"cidr": "10.50.0.0/16", "public_access": False})
    assert re.search(r"assign_public_ip\s*=\s*true", com["main.tf"])
    assert not re.search(r"assign_public_ip\s*=\s*true", sem["main.tf"])
    assert "eram públicas na origem" in sem["README.md"]


def test_faixa_que_sobrepoe_a_origem_e_avisada() -> None:
    readme = pacote(com_topologia("aws"), "gcp", network={"cidr": "10.10.0.0/16"})["README.md"]
    assert "sobrepõe a origem" in readme


def test_rede_pequena_divide_e_rede_minima_avisa() -> None:
    pequena = pacote(com_topologia("aws"), "gcp", network={"cidr": "10.50.0.0/22"})
    assert "estratégia: todas /26" in pequena["README.md"]
    minima = pacote(com_topologia("aws"), "gcp", network={"cidr": "10.50.0.0/24"})
    assert "tudo foi para a subrede padrão" in minima["README.md"]
    assert "Espelha a subrede de origem" not in minima["main.tf"]


# ── grupos de segurança (I8b) ───────────────────────────────────────────


def test_aws_grupo_regras_e_anexos() -> None:
    main = pacote(com_topologia("azure"), "aws")["main.tf"]
    grupos = _blocos(main, "aws_security_group")
    assert grupos
    entradas = _blocos(main, "aws_vpc_security_group_ingress_rule")
    saidas = _blocos(main, "aws_vpc_security_group_egress_rule")
    assert entradas
    assert len(saidas) == len(grupos)  # saída liberada em todo grupo
    vm = _bloco(main, "aws_instance", "vm_esv5_eastus_")
    assert "vpc_security_group_ids = [aws_security_group." in vm
    banco = _bloco(main, "aws_db_instance", "banco_postgresql_standard_d4ds_v5")
    assert "vpc_security_group_ids" in banco  # banco na VPC nova recebe o grupo


def test_gcp_tag_e_firewall() -> None:
    main = pacote(com_topologia("aws"), "gcp")["main.tf"]
    firewalls = _blocos(main, "google_compute_firewall")
    assert firewalls
    assert all(
        "target_tags" in b and re.search(r'direction\s*=\s*"INGRESS"', b)
        for b in firewalls.values()
    )
    nomes = [re.search(r'name\s*=\s*"([^"]+)"', b).group(1) for b in firewalls.values()]
    assert len(nomes) == len(set(nomes))
    assert all(len(n) <= 63 for n in nomes)
    vm = _bloco(main, "google_compute_instance", "vm_c7g_us_east_1_")
    assert re.search(r'tags\s*=\s*\["sg-', vm)


def test_azure_asg_na_nic_e_nsg_por_subrede() -> None:
    main = pacote(com_topologia("oci"), "azure")["main.tf"]
    assert _blocos(main, "azurerm_application_security_group")
    assert _blocos(main, "azurerm_network_interface_application_security_group_association")
    nsgs = _blocos(main, "azurerm_network_security_group")
    assert nsgs
    for nsg in nsgs.values():
        prioridades = [int(x) for x in re.findall(r"priority\s*=\s*(\d+)", nsg)]
        # As duas últimas são o isolamento entre subredes (T2)
        dependencias, isolamento = prioridades[:-2], prioridades[-2:]
        assert dependencias == list(range(100, 100 + 10 * len(dependencias), 10))
        assert isolamento == [4095, 4096]
    assert any("destination_application_security_group_ids" in nsg for nsg in nsgs.values())
    assert len(_blocos(main, "azurerm_subnet_network_security_group_association")) == len(nsgs)


@pytest.mark.parametrize("origem", ["aws", "gcp", "oci"])
def test_azure_subrede_sem_dependencia_nao_fala_com_outra(origem: str) -> None:
    """T2: a regra padrão `AllowVnetInBound` liberava tudo entre as subredes da VNet.
    Toda subrede espelhada ganha NSG: ela fala com ela mesma, e o resto da VNet é negado."""
    main = pacote(com_topologia(origem), "azure")["main.tf"]
    subredes = {
        nome: bloco
        for nome, bloco in _blocos(main, "azurerm_subnet").items()
        if nome != "subnet_principal"  # a padrão não é espelho de origem
    }
    nsgs = _blocos(main, "azurerm_network_security_group")
    assert subredes and len(nsgs) == len(subredes)
    for nsg in nsgs.values():
        negar = re.search(r'name\s*=\s*"negar-outras-subredes"(.*?)\}', nsg, re.S)
        assert negar, nsg
        assert re.search(r'access\s*=\s*"Deny"', negar.group(1))
        assert re.search(r'source_address_prefix\s*=\s*"VirtualNetwork"', negar.group(1))
        assert '"permitir-mesma-subrede"' in nsg


def test_banco_na_subrede_de_dados_espelhada() -> None:
    """T3: o banco do mock Azure (subrede "dados") vai para a subrede espelhada.

    Na OCI, endpoint privado. Na Azure, a subrede é delegada ao PostgreSQL Flexible, com
    zona DNS privada e sem acesso público. Só o mock Azure diz a subrede do banco, e
    Azure para Azure não é par: a origem é trocada para exercitar o destino Azure."""
    oci = pacote(com_topologia("azure"), "oci")["main.tf"]
    banco = _bloco(oci, "oci_psql_db_system", "banco_")
    assert re.search(r"subnet_id\s*=\s*oci_core_subnet\.subnet_dados\.id", banco)

    azure = pacote({**com_topologia("azure"), "provedor_origem": "aws"}, "azure")["main.tf"]
    subrede = _bloco(azure, "azurerm_subnet", "subnet_dados")
    assert "Microsoft.DBforPostgreSQL/flexibleServers" in subrede
    banco = _bloco(azure, "azurerm_postgresql_flexible_server", "banco_")
    assert re.search(r"delegated_subnet_id\s*=\s*azurerm_subnet\.subnet_dados\.id", banco)
    assert re.search(r"public_network_access_enabled\s*=\s*false", banco)
    assert len(_blocos(azure, "azurerm_private_dns_zone")) == 1
    # O banco não entra em ASG: a subrede dele abre a porta do PostgreSQL para a VNet
    nsg = _bloco(azure, "azurerm_network_security_group", "nsg_subnet_dados")
    assert re.search(r'destination_port_range\s*=\s*"5432"', nsg)


def test_gcp_banco_com_acesso_privado() -> None:
    """T3: o Cloud SQL usa `private_network`; sem a conexão de acesso privado, o apply falha."""
    for origem in ("aws", "azure", "oci"):
        main = pacote(com_topologia(origem), "gcp")["main.tf"]
        assert len(_blocos(main, "google_service_networking_connection")) == 1
        assert len(_blocos(main, "google_compute_global_address")) == 1
        banco = _bloco(main, "google_sql_database_instance", "banco_")
        assert "google_service_networking_connection.acesso_privado" in banco


def test_oci_nsg_e_combinado() -> None:
    arquivos = pacote(com_topologia("aws"), "oci")
    main = arquivos["main.tf"]
    assert _blocos(main, "oci_core_network_security_group")
    regras = _blocos(main, "oci_core_network_security_group_security_rule")
    assert any(re.search(r'direction\s*=\s*"EGRESS"', b) for b in regras.values())
    vm = _bloco(main, "oci_core_instance", "vm_c7g_us_east_1_")
    ids = re.search(r"nsg_ids\s*=\s*\[([^\]]*)\]", vm)
    assert ids and 1 <= ids.group(1).count(".id") <= 5  # a OCI aceita 5 por VNIC
    assert "grupo combinado" in arquivos["README.md"]


@pytest.mark.parametrize(("origem", "destino"), PARES)
def test_regra_de_risco_alto_nao_vai_para_o_destino(origem: str, destino: str) -> None:
    """Administração ou banco aberto para a internet não é replicado (RI15.4)."""
    main = pacote(com_topologia(origem), destino)["main.tf"]
    abertos = []
    for tipo, internet in (
        ("aws_vpc_security_group_ingress_rule", r'cidr_ipv4\s*=\s*"0\.0\.0\.0/0"'),
        ("google_compute_firewall", r'source_ranges\s*=\s*\["0\.0\.0\.0/0"'),
        ("oci_core_network_security_group_security_rule", r'source\s*=\s*"0\.0\.0\.0/0"'),
    ):
        for nome, bloco in _blocos(main, tipo).items():
            if re.search(internet, bloco) and re.search(r"\b(22|3389|5432|3306|1433)\b", bloco):
                abertos.append(nome)
    for nsg in _blocos(main, "azurerm_network_security_group").values():
        for regra in nsg.split("security_rule {")[1:]:
            if '"0.0.0.0/0"' in regra and re.search(r'"(22|3389|5432|3306|1433)"', regra):
                abertos.append("nsg")
    assert abertos == []


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
