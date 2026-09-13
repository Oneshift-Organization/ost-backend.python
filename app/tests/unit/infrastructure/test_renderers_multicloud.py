"""As quatro nuvens.

Um teste por invariante que **não** pode depender do provedor: o pacote tem
sempre os mesmos arquivos, nunca carrega credencial, nunca escreve provisioner,
e respeita a regra de nome de cada serviço — que é onde as nuvens mais divergem.

Os casos aqui saíram de rodar `terraform validate` contra o schema real de cada
provider. Três deles falhavam: `bucket_prefix` da AWS passava do teto de 37,
`nome_cloud` com separador vazio (exigência da Azure) levantava ValueError, e a
OCI referenciava uma variável que ninguém declarava.
"""

from __future__ import annotations

import re

import pytest

from app.infrastructure.terraform.renderers import (
    arquivos_esperados,
    provedores_com_template,
    renderizar,
)
from app.infrastructure.terraform.renderers.base import nome_cloud
from app.schemas.terraform import ProvedorSuportado, TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint

_REGIAO = {
    "gcp": "southamerica-east1",
    "aws": "sa-east-1",
    "azure": "brazilsouth",
    "oci": "sa-saopaulo-1",
}
_SKU = {
    "gcp": "n2-standard-8",
    "aws": "m6i.2xlarge",
    "azure": "Standard_D8s_v3",
    "oci": "VM.Standard.E4.Flex",
}
TODAS = tuple(_REGIAO)


def _payload() -> dict:
    return {
        "provedor_origem": "aws",
        "pools_computacao": [
            {
                "familia": "M6I",
                "equivalentes": [
                    {
                        "provedor": p.upper(),
                        "nome_servico": "Compute",
                        "sku": sku,
                        "instancias": 4,
                        "correspondencia_tecnica": {"vcpus": 32.0, "ram_gb": 128.0},
                        "financeiro": {"confianca_preco": "official"},
                    }
                    for p, sku in _SKU.items()
                ],
            }
        ],
        "custos_auxiliares": {
            "armazenamento_ssd": 800.0,
            "armazenamento_objetos": 230.0,
            "banco_dados": 620.0,
        },
    }


def _arquivos(provedor: str, **kw) -> dict[str, str]:
    base = {
        "analysis_job_id": "62d67cd7-2b80-4e61-9e72-2f17505d262c",
        "project_name": "multicloud-demo",
        "target_provider": provedor,
        "region": _REGIAO[provedor],
        "network": {"cidr": "10.70.0.0/16"},
    }
    entrada = TerraformGenerationIn(**{**base, **kw})
    return renderizar(montar_blueprint(_payload(), entrada))


# ── o que vale em toda nuvem ────────────────────────────────────────────


def test_as_quatro_tem_adaptador() -> None:
    assert set(provedores_com_template()) == {p.value for p in ProvedorSuportado}


@pytest.mark.parametrize("provedor", TODAS)
def test_o_pacote_tem_sempre_os_mesmos_arquivos(provedor: str) -> None:
    """Quem já viu um pacote nosso de GCP encontra as mesmas coisas nos mesmos
    lugares num de AWS."""
    assert set(_arquivos(provedor)) == set(arquivos_esperados())


@pytest.mark.parametrize("provedor", TODAS)
def test_nenhuma_credencial_no_pacote(provedor: str) -> None:
    junto = "\n".join(_arquivos(provedor).values()).lower()
    for suspeito in ("private_key", "secret_access_key", "-----begin", "client_secret"):
        assert suspeito not in junto


@pytest.mark.parametrize("provedor", TODAS)
def test_nenhum_provisioner_nem_backend_remoto(provedor: str) -> None:
    """Não são filtrados: não existe código que os escreva."""
    for nome, conteudo in _arquivos(provedor).items():
        if nome.endswith(".tf"):
            for proibido in ("provisioner", "local-exec", "remote-exec", "backend "):
                assert proibido not in conteudo, f"{proibido} em {provedor}/{nome}"


@pytest.mark.parametrize("provedor", TODAS)
def test_versao_do_provider_e_fixa(provedor: str) -> None:
    codigo = [
        linha
        for linha in _arquivos(provedor)["versions.tf"].splitlines()
        if not linha.strip().startswith("#")
    ]
    assert "latest" not in "\n".join(codigo)


@pytest.mark.parametrize("provedor", TODAS)
def test_toda_variavel_sem_default_aparece_no_tfvars(provedor: str) -> None:
    """O que a análise não soube responder tem que ser visível para preencher —
    senão o cliente descobre no `plan`, uma por vez."""
    arquivos = _arquivos(provedor)
    sem_default = {
        nome
        for nome in re.findall(r'variable "(\w+)"', arquivos["variables.tf"])
        if "default" not in arquivos["variables.tf"].split(f'variable "{nome}"')[1].split("}")[0]
    }
    exemplo = arquivos["terraform.tfvars.example"]
    for nome in sem_default:
        assert nome in exemplo, f"{provedor}: {nome} não está no tfvars.example"


@pytest.mark.parametrize("provedor", TODAS)
def test_o_readme_declara_procedencia_e_nao_declara_economia(provedor: str) -> None:
    readme = _arquivos(provedor)["README.md"]
    assert "não declara economia" in readme
    assert provedor.upper() in readme


# ── onde as nuvens divergem ─────────────────────────────────────────────


def test_azure_exige_features_no_provider() -> None:
    """Sem `features {}`, mesmo vazio, o provider da Azure não inicializa."""
    assert "features {}" in _arquivos("azure")["providers.tf"]


def test_azure_cria_resource_group_antes_de_tudo() -> None:
    """Estrutura da Azure: não sai da análise, mas nada existe sem ele."""
    main = _arquivos("azure")["main.tf"]
    assert 'resource "azurerm_resource_group"' in main
    assert main.index("azurerm_resource_group") < main.index("azurerm_virtual_network")


def test_azure_vm_tem_nic_separada() -> None:
    """No GCP a interface é bloco dentro da instância; aqui é recurso próprio."""
    main = _arquivos("azure")["main.tf"]
    assert 'resource "azurerm_network_interface"' in main
    assert "network_interface_ids" in main


def test_nome_de_storage_da_azure_nao_tem_hifen() -> None:
    """3 a 24 caracteres, só minúscula e dígito. É a regra mais restritiva das
    quatro, e o separador vazio chegou a levantar ValueError."""
    nome = nome_cloud("multicloud-demo", "bucket_object_storage", maximo=24, separador="")
    assert nome.isalnum() and nome.islower()
    assert len(nome) <= 24


def test_bucket_da_aws_usa_prefixo_e_cabe_no_limite() -> None:
    """Nome de S3 é GLOBAL: nome fixo colidiria com outro cliente. E o provider
    limita `bucket_prefix` a 37 caracteres — passava disso."""
    main = _arquivos("aws")["main.tf"]
    assert "bucket_prefix" in main
    for prefixo in re.findall(r'bucket_prefix\s*=\s*"([^"]+)"', main):
        assert len(prefixo) <= 37, prefixo


def test_aws_bloqueia_acesso_publico_com_recurso_separado() -> None:
    """No GCP é atributo do bucket; aqui, sem o recurso, ele nasce sujeito a
    ACL pública."""
    assert "aws_s3_bucket_public_access_block" in _arquivos("aws")["main.tf"]


def test_oci_declara_toda_variavel_que_referencia() -> None:
    """A OCI referenciava `var.db_admin_password` sem declarar — o
    `terraform validate` reprovava o pacote inteiro."""
    arquivos = _arquivos("oci")
    declaradas = set(re.findall(r'variable "(\w+)"', arquivos["variables.tf"]))
    usadas = set(re.findall(r"var\.(\w+)", arquivos["main.tf"]))
    assert usadas <= declaradas, f"sem declaração: {usadas - declaradas}"


def test_oci_dimensiona_por_shape_flexivel() -> None:
    """A OCI cobra OCPU e memória separadamente, o que casa melhor com o modelo
    de pool do que um nome de instância fechado."""
    main = _arquivos("oci")["main.tf"]
    assert "shape_config" in main
    assert "ocpus" in main and "memory_in_gbs" in main


@pytest.mark.parametrize("provedor", TODAS)
def test_bucket_privado_por_padrao(provedor: str) -> None:
    main = _arquivos(provedor)["main.tf"]
    publico = {
        "gcp": 'public_access_prevention = "enforced"',
        "aws": "restrict_public_buckets = true",
        "azure": "allow_nested_items_to_be_public = false",
        "oci": 'access_type = "NoPublicAccess"',
    }[provedor]
    normalizado = " ".join(main.split())
    assert " ".join(publico.split()) in normalizado
