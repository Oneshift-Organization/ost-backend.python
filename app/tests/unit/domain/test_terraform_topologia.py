"""Terraform com a topologia do inventário: versão, discos e banco (RI15.6, RI15.7)."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.domain.services.terraform_plan import derivar_plano
from app.domain.services.terraform_topologia import aplicar_topologia
from app.domain.services.terraform_topologia.bancos import (
    agrupar_sem_grupo,
    vcpus_da_classe,
    versao_padrao,
)
from app.domain.services.terraform_topologia.discos import avulsos, boot_medio

# ── versão ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("destino", "esperado"),
    [("aws", "16"), ("azure", "16"), ("gcp", "POSTGRES_16"), ("oci", "16")],
)
def test_versao_mais_frequente_no_formato_do_destino(destino: str, esperado: str | None) -> None:
    v = versao_padrao({"14.10": 11, "15.4": 9, "16.1": 12}, "postgresql", destino)
    assert v.default == esperado
    assert v.maior == "16"
    assert v.contagem == {"14": 11, "15": 9, "16": 12}


def test_versao_fora_do_destino_diz_o_que_ele_aceita() -> None:
    # O PostgreSQL da OCI começa no 14; a AWS ainda aceita o 13
    oci = versao_padrao({"13.7": 1}, "postgresql", "oci")
    assert oci.default is None
    assert "PostgreSQL 14 a 16" in oci.motivo
    assert versao_padrao({"13.7": 1}, "postgresql", "aws").default == "13"
    # A versão medida dentro do que o destino aceita não vira aviso de "fora"
    assert "fora" not in versao_padrao({"16.4": 1}, "postgresql", "oci").motivo
    # O HeatWave pede a versão completa do catálogo da OCI
    mysql = versao_padrao({"8.0.35": 1}, "mysql", "oci")
    assert mysql.default is None
    assert "catálogo da OCI" in mysql.motivo


def test_empate_fica_com_a_maior_e_menores_somam() -> None:
    assert versao_padrao({"14.1": 2, "15.2": 2}, "postgresql", "aws").default == "15"
    # 14.1 + 14.9 somam 3 e passam os 2 do 15.
    assert versao_padrao({"14.1": 2, "14.9": 1, "15.2": 2}, "postgresql", "aws").default == "14"


def test_mysql_e_versao_nao_suportada() -> None:
    assert versao_padrao({"8.0.35": 3}, "mysql", "gcp").default == "MYSQL_8_0"
    assert versao_padrao({"8.0.35": 3}, "mysql", "aws").default == "8.0"
    antiga = versao_padrao({"5.7.40": 3, "8.0.35": 1}, "mysql", "aws")
    assert antiga.default is None
    assert "5.7" in antiga.motivo
    # 17 existe nas nuvens, mas o azurerm 3.x recusa: o pacote não aplicaria.
    assert versao_padrao({"17.2": 1}, "postgresql", "azure").default is None
    assert versao_padrao({"19c": 1}, "oracle", "aws").maior is None
    assert versao_padrao({}, "postgresql", "aws").maior is None


def test_vcpus_da_classe_nas_tres_nomenclaturas() -> None:
    assert vcpus_da_classe("db.t4g.small") == 2
    assert vcpus_da_classe("Standard_D4ds_v5") == 4
    assert vcpus_da_classe("db-custom-8-32768") == 8
    assert vcpus_da_classe("ATP-ECPU") is None


def test_sem_grupo_agrupa_e_conta_os_sem_classe() -> None:
    grupos, sem_vcpu = agrupar_sem_grupo(
        [
            {"classe": "Standard_D4ds_v5", "motor": "postgres", "versao": "14"},
            {"classe": "Standard_D4ds_v5", "motor": "postgres", "versao": "14"},
            {"classe": "ATP-ECPU", "motor": "oracle", "versao": "19c"},
        ]
    )
    assert [(g.classe, g.quantidade, g.vcpus) for g in grupos] == [("Standard_D4ds_v5", 2, 4)]
    assert sem_vcpu == 1


# ── discos ──────────────────────────────────────────────────────────────


def _discos(boot: tuple[int, int], solto: tuple[int, int]) -> dict:
    return {
        "boot": {"armazenamento_ssd": {"gb": boot[0], "volumes": boot[1]}},
        "sem_anexo": {"armazenamento_ssd": {"gb": solto[0], "volumes": solto[1]}},
    }


def test_avulsos_sao_o_medido_menos_boot_e_sem_anexo() -> None:
    """Os números do mock da AWS: 48.626 GB medidos em 99 volumes."""
    medido = {"armazenamento_ssd": {"gb": 48626, "recursos": 99}}
    conta = avulsos(medido, _discos((36334, 74), (3479, 7)), "armazenamento_ssd")
    assert conta is not None
    assert (conta.gb, conta.volumes, conta.por_volume) == (8813, 18, 490)


def test_avulsos_nunca_abaixo_de_zero_e_sem_medicao_nao_se_mexe() -> None:
    medido = {"armazenamento_ssd": {"gb": 100, "recursos": 2}}
    conta = avulsos(medido, _discos((80, 2), (40, 1)), "armazenamento_ssd")
    assert conta is not None
    assert (conta.gb, conta.volumes) == (0, 0)
    assert avulsos({}, _discos((80, 2), (0, 0)), "armazenamento_ssd") is None


def test_boot_medio_ponderado_pelas_instancias() -> None:
    assert boot_medio(
        [
            {"disco_boot_gb": 100, "instancias": 3},
            {"disco_boot_gb": 20, "instancias": 1},
            {"disco_boot_gb": None, "instancias": 5},
        ]
    ) == (80, 4)
    assert boot_medio([{"disco_boot_gb": None, "instancias": 2}]) is None


# ── o plano com a topologia ─────────────────────────────────────────────


def _payload() -> dict:
    return {
        "provedor_origem": "aws",
        "moeda": "USD",
        "pools_computacao": [
            {
                "familia": "C7G",
                "regiao": "us-east-1",
                "equivalentes": [
                    {
                        "provedor": "GCP",
                        "sku": "e2-standard-32",
                        "instancias": 4,
                        "correspondencia_tecnica": {"vcpus": 128.0, "ram_gb": 512.0},
                    }
                ],
            }
        ],
        "custos_auxiliares": {"armazenamento_ssd": 5000.0, "banco_dados": 900.0},
        "inventario_medido": {
            "armazenamento": {"armazenamento_ssd": {"gb": 48626, "recursos": 99}},
            "bancos": [
                {
                    "classe": "db.t4g.small",
                    "motor": "PostgreSQL",
                    "multi_az": True,
                    "regiao": "us-east-1",
                    "quantidade": 38,
                    "custo": 800.0,
                }
            ],
        },
        "topologia": {
            "versao": "oneshift-topologia-v1",
            "detalhe": {
                "posicionamento": [
                    {
                        "familia": "C7G",
                        "regiao": "us-east-1",
                        "posicionamentos": [
                            {"subrede": "s1", "instancias": 3, "disco_boot_gb": 100},
                            {"subrede": "s2", "instancias": 1, "disco_boot_gb": 20},
                        ],
                        "sem_posicionamento": 0,
                    }
                ],
                "discos": _discos((36334, 74), (3479, 7)),
                "bancos": [
                    {
                        "classe": "db.t4g.small",
                        "motor": "PostgreSQL",
                        "multi_az": True,
                        "regiao": "us-east-1",
                        "versoes": {"14.10": 11, "15.4": 9, "16.1": 12},
                    }
                ],
                "bancos_sem_grupo": [],
            },
        },
    }


def _aplicar(payload: dict, destino: str = "gcp"):
    plano = derivar_plano(payload, provedor_destino=destino, regiao="us-central1")
    return plano, aplicar_topologia(
        plano, payload, destino=destino, rede="10.50.0.0/16", acesso_publico=False
    )


def test_boot_vira_disco_raiz_e_o_plano_de_entrada_fica_intacto() -> None:
    """Sem subrede de origem no detalhe, as fatias caem todas na padrão e viram uma VM
    só; o raiz é a média ponderada do boot das duas subredes: (3x100 + 1x20) / 4."""
    antes, depois = _aplicar(_payload())
    [vm] = [r for r in depois.recursos if r.logical_name.startswith("vm_")]
    assert vm.logical_name == "vm_c7g_us_east_1_padrao"
    assert vm.configuration["instances"] == 4
    assert vm.configuration["root_gb"] == 80
    assert vm.configuration["root_disk_type"] == "pd-balanced"
    assert any(p.campo == "vm_c7g_us_east_1.root_gb" for p in depois.premissas)
    [vm_antes] = [r for r in antes.recursos if r.logical_name.startswith("vm_")]
    assert "root_gb" not in vm_antes.configuration


def test_volumes_avulsos_substituem_o_medido_e_dizem_o_que_saiu() -> None:
    antes, depois = _aplicar(_payload())
    [disco] = [r for r in depois.recursos if r.configuration.get("disk_only")]
    assert (disco.configuration["volumes"], disco.configuration["size_gb"]) == (18, 490)
    [premissa] = [p for p in depois.premissas if p.campo == f"{disco.logical_name}.size_gb"]
    assert premissa.valor == "8,813 GB em 18 volumes"
    assert any("36,334 GB de boot" in a for a in depois.avisos)
    [disco_antes] = [r for r in antes.recursos if r.configuration.get("disk_only")]
    assert disco_antes.configuration["volumes"] == 99


def test_tudo_explicado_pelo_inventario_remove_o_volume() -> None:
    payload = _payload()
    payload["inventario_medido"]["armazenamento"]["armazenamento_ssd"] = {
        "gb": 39813,
        "recursos": 81,
    }
    _, depois = _aplicar(payload)
    assert not [r for r in depois.recursos if r.configuration.get("disk_only")]
    assert any(p.valor == "0 GB" for p in depois.premissas)


def test_versao_do_banco_vira_default_da_variavel() -> None:
    _, depois = _aplicar(_payload(), destino="gcp")
    assert depois.variaveis_padrao == {"database_version_postgresql": "POSTGRES_16"}
    [premissa] = [p for p in depois.premissas if p.campo.startswith("variavel.")]
    assert premissa.categoria == "derivacao"
    assert "14 (11), 15 (9), 16 (12)" in premissa.base
    assert any("versões 14 (11), 15 (9), 16 (12)" in a for a in depois.avisos)


def test_banco_sem_classe_no_billing_sai_com_a_do_inventario() -> None:
    payload = _payload()
    payload["inventario_medido"]["bancos"] = []  # Flexible Server: billing sem classe
    payload["topologia"]["detalhe"]["bancos_sem_grupo"] = [
        {"classe": "Standard_D4ds_v5", "motor": "postgres", "versao": "14"},
        {"classe": "Standard_D4ds_v5", "motor": "postgres", "versao": "14"},
    ]
    antes, depois = _aplicar(payload, destino="aws")
    assert [r.logical_name for r in antes.recursos if r.resource_type == "managed_database"] == [
        "banco_principal"
    ]
    [banco] = [r for r in depois.recursos if r.resource_type == "managed_database"]
    assert (banco.configuration["vcpus"], banco.configuration["instances"]) == (4, 2)
    assert banco.configuration["vcpu_source"] == "medido"
    assert not any(p.campo.startswith("banco_principal.") for p in depois.premissas)
    assert depois.variaveis_padrao == {"database_version_postgresql": "14"}


def test_motor_que_o_destino_nao_gera_fica_de_fora() -> None:
    payload = _payload()
    payload["inventario_medido"]["bancos"] = []
    payload["topologia"]["detalhe"]["bancos_sem_grupo"] = [
        {"classe": "Standard_D4ds_v5", "motor": "mysql", "versao": "8.0"}
    ]
    _, depois = _aplicar(payload, destino="azure")
    assert not [r for r in depois.recursos if r.resource_type == "managed_database"]
    assert any("mysql" in x for x in depois.nao_suportado)


def test_sem_detalhe_nao_muda_nada() -> None:
    payload = _payload()
    payload["topologia"]["detalhe"] = {}
    antes, depois = _aplicar(deepcopy(payload))
    assert depois.recursos == antes.recursos
    assert depois.premissas == antes.premissas
