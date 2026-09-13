"""Renderização de HCL e empacotamento.

O que estes testes protegem é o que separa "gerar código" de "interpolar texto":
nenhum valor vindo de dado vira sintaxe, e nenhum arquivo fora da allowlist entra
no pacote que o cliente baixa.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.infrastructure.terraform.archive import (
    ArquivoNaoPermitidoError,
    montar_zip,
)
from app.infrastructure.terraform.gcp_renderer import renderizar
from app.infrastructure.terraform.hcl import (
    arquivo,
    atributo,
    bloco,
    identificador,
    referencia,
)
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint
from app.use_cases.terraform.generate_terraform import gerar_pacote

# ── fixtures ────────────────────────────────────────────────────────────


def _payload() -> dict:
    return {
        "provedor_origem": "aws",
        "pools_computacao": [
            {
                "familia": "M6I",
                "equivalentes": [
                    {
                        "provedor": "GCP",
                        "nome_servico": "Compute Engine N2",
                        "sku": "n2-standard-8",
                        "instancias": 4,
                        "correspondencia_tecnica": {"vcpus": 32.0, "ram_gb": 128.0},
                    }
                ],
            }
        ],
        "custos_auxiliares": {
            "armazenamento_ssd": 800.0,
            "armazenamento_objetos": 230.0,
            "banco_dados": 620.0,
        },
        "premissas": {
            "fx": {"par": "BRL/USD", "taxa": "5.70", "data": None, "fonte": "config"},
            "confianca_analise": {"nivel": "medium", "razao_custo_confiavel": 0.36},
            "sugestoes_backend": [
                {
                    "titulo": "Câmbio de BRL/USD sem data de cotação",
                    "detalhe": "A taxa veio de 'config' e não tem data.",
                }
            ],
        },
    }


def _entrada(**kw) -> TerraformGenerationIn:
    base = {
        "analysis_job_id": "62d67cd7-2b80-4e61-9e72-2f17505d262c",
        "project_name": "migracao-empresa-x",
        "environment": "dev",
        "target_provider": "gcp",
        "region": "southamerica-east1",
        "network": {"mode": "create", "cidr": "10.20.0.0/16"},
    }
    return TerraformGenerationIn(**{**base, **kw})


def _arquivos() -> dict[str, str]:
    return renderizar(
        montar_blueprint(_payload(), _entrada()),
        procedencia=_payload()["premissas"],
    )


# ── HCL: valores nunca viram sintaxe ────────────────────────────────────


def test_string_com_aspas_nao_quebra_o_arquivo() -> None:
    assert atributo("name", 'a "b" c') == '  name = "a \\"b\\" c"'


def test_interpolacao_vinda_de_dado_e_neutralizada() -> None:
    """Um valor com `${...}` viraria interpolação e leria o estado do Terraform.
    Só expressões que NÓS construímos passam por `referencia`."""
    assert "$${" in atributo("name", "${var.secreto}")


def test_referencia_sai_sem_aspas() -> None:
    assert atributo("network", referencia("google_compute_network.x.id")) == (
        "  network = google_compute_network.x.id"
    )


def test_identificador_invalido_falha_em_vez_de_gerar_lixo() -> None:
    """Falhar aqui é melhor que gerar um arquivo que não parseia — ou que
    parseia e faz outra coisa."""
    with pytest.raises(ValueError, match="identificador"):
        identificador("nome com espaço")
    with pytest.raises(ValueError, match="identificador"):
        bloco("resource", [], [atributo('x" { evil = "', 1)])


def test_atribuicoes_saem_alinhadas() -> None:
    """É o que `terraform fmt` faria. Fazendo aqui, o fmt vira conferência."""
    texto = arquivo([bloco("resource", ["t", "n"], [
        atributo("a", 1), atributo("bbbb", 2),
    ])])
    assert "  a    = 1" in texto
    assert "  bbbb = 2" in texto


# ── o pacote renderizado ────────────────────────────────────────────────


def test_gera_todos_os_arquivos() -> None:
    assert set(_arquivos()) == {
        "versions.tf", "providers.tf", "variables.tf", "main.tf",
        "outputs.tf", "terraform.tfvars.example", "README.md",
    }


def test_shape_derivado_vira_machine_type() -> None:
    assert 'machine_type = "n2-standard-8"' in _arquivos()["main.tf"]


def test_bucket_e_bucket_e_disco_e_disco() -> None:
    """O erro que a classificação antiga produziria: object storage virando
    `google_compute_disk`."""
    main = _arquivos()["main.tf"]
    assert 'resource "google_storage_bucket"' in main
    assert 'resource "google_compute_disk"' in main


def test_versao_do_provider_e_fixa() -> None:
    """`latest` num arquivo aplicado meses depois muda o plano sozinho."""
    versions = _arquivos()["versions.tf"]
    assert 'version = "~> 5.0"' in versions
    codigo = [x for x in versions.splitlines() if not x.strip().startswith("#")]
    assert "latest" not in "\n".join(codigo)


# ── segurança do que sai ────────────────────────────────────────────────


def test_nenhum_arquivo_contem_provisioner_ou_backend_remoto() -> None:
    """Não são filtrados: não existe código que os escreva."""
    for nome, conteudo in _arquivos().items():
        if nome.endswith(".tf"):
            for proibido in ("provisioner", "local-exec", "remote-exec", "backend "):
                assert proibido not in conteudo, f"{proibido} em {nome}"


def test_nenhuma_credencial_no_pacote() -> None:
    junto = "\n".join(_arquivos().values()).lower()
    for suspeito in ("private_key", "secret_access_key", "-----begin"):
        assert suspeito not in junto


def test_bucket_bloqueia_acesso_publico_por_padrao() -> None:
    main = _arquivos()["main.tf"]
    assert 'public_access_prevention    = "enforced"' in main or \
           'public_access_prevention = "enforced"' in main
    assert "uniform_bucket_level_access" in main


def test_banco_nasce_com_protecao_de_exclusao() -> None:
    assert "deletion_protection" in _arquivos()["main.tf"]


# ── o README carrega a procedência ──────────────────────────────────────


def test_readme_declara_o_que_foi_estimado() -> None:
    readme = _arquivos()["README.md"]
    assert "Estimado" in readme
    assert "Calculado" in readme


def test_readme_diz_de_quando_sao_os_dados() -> None:
    """Sem isso o cliente lê uma foto antiga como estado atual."""
    readme = _arquivos()["README.md"]
    assert "BRL/USD" in readme
    assert "sem data" in readme


def test_readme_nao_declara_economia() -> None:
    """A cotação usa região de referência, que pode não ser a escolhida —
    publicar um número seria comparar preços de lugares diferentes."""
    assert "não declara economia" in _arquivos()["README.md"]


def test_variavel_sem_default_para_o_que_ninguem_sabe() -> None:
    """O motor do banco não sai do billing. Sem default, o `terraform plan`
    para e pergunta em vez de aplicar um palpite nosso."""
    variables = _arquivos()["variables.tf"]
    assert 'variable "database_version"' in variables
    corpo = variables.split('variable "database_version"')[1].split("}")[0]
    assert "default" not in corpo


# ── ZIP ─────────────────────────────────────────────────────────────────


def test_zip_tem_raiz_e_todos_os_arquivos() -> None:
    resultado = gerar_pacote(_payload(), _entrada())
    dados = montar_zip(resultado.arquivos, raiz="migracao-empresa-x-terraform")
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        nomes = z.namelist()
    assert all(n.startswith("migracao-empresa-x-terraform/") for n in nomes)
    assert "migracao-empresa-x-terraform/main.tf" in nomes
    assert "migracao-empresa-x-terraform/manifest.json" in nomes


def test_arquivo_fora_da_allowlist_nao_entra() -> None:
    """Allowlist aplicada de novo na saída: `../` num ZIP é a vulnerabilidade
    clássica de extração."""
    with pytest.raises(ArquivoNaoPermitidoError):
        montar_zip({"../../etc/passwd": "x"}, raiz="p")


def test_raiz_com_caminho_e_saneada() -> None:
    dados = montar_zip({"main.tf": "x"}, raiz="../../evil")
    with zipfile.ZipFile(io.BytesIO(dados)) as z:
        assert not any(".." in n for n in z.namelist())


def test_manifesto_e_json_valido_com_as_premissas() -> None:
    resultado = gerar_pacote(_payload(), _entrada())
    manifesto = json.loads(resultado.arquivos["manifest.json"])
    assert manifesto["custo_declarado"] is None
    categorias = {p["categoria"] for p in manifesto["premissas"]}
    assert "derivacao" in categorias
    assert "estimativa" in categorias


# ── determinismo ────────────────────────────────────────────────────────


def test_duas_geracoes_produzem_os_mesmos_arquivos() -> None:
    """A razão de a derivação não passar por modelo."""
    a = gerar_pacote(_payload(), _entrada()).arquivos
    b = gerar_pacote(_payload(), _entrada()).arquivos
    assert a == b
