"""`terraform fmt` sobre o pacote gerado.

Estes testes só rodam onde o binário existe. Não é frouxidão: o gerador já
produz o formato canônico por conta própria (`hcl.arquivo` alinha os `=`), e o
`fmt` serve de **conferência independente** — se ele mudar alguma coisa, é o
nosso gerador que saiu do padrão.

Foi exatamente assim que dois bugs apareceram: o mapa inline sem espaço dentro
das chaves, e o nome de recurso com `_` que o GCP recusa. Nenhum dos dois falhava
no nosso código.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.infrastructure.terraform.fmt import formatar
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.generate_terraform import gerar_pacote

pytestmark = pytest.mark.skipif(
    shutil.which("terraform") is None,
    reason="binário do Terraform ausente — o pacote não depende dele",
)


def _entrada(**kw) -> TerraformGenerationIn:
    base = {
        "analysis_job_id": "62d67cd7-2b80-4e61-9e72-2f17505d262c",
        "project_name": "proj-teste",
        "target_provider": "gcp",
        "region": "us-central1",
        "network": {"cidr": "10.0.0.0/16"},
    }
    return TerraformGenerationIn(**{**base, **kw})


_PAYLOAD = {
    "provedor_origem": "aws",
    "pools_computacao": [
        {
            "familia": "M6I",
            "equivalentes": [
                {
                    "provedor": "GCP",
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
}


def test_o_gerador_ja_produz_o_formato_canonico(tmp_path) -> None:
    """`fmt -check` sem `-write`: falha se houvesse algo a reformatar.

    É a asserção mais forte do módulo. Se ela quebrar, o gerador divergiu do
    formato oficial — e é melhor descobrir aqui do que o cliente descobrir num
    diff depois de versionar o pacote.
    """
    for nome, conteudo in gerar_pacote(_PAYLOAD, _entrada()).arquivos.items():
        if nome.endswith(".tf"):
            (tmp_path / nome).write_text(conteudo)

    resultado = subprocess.run(
        [shutil.which("terraform"), "fmt", "-check", "-diff", "-no-color", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # o returncode É a asserção
    )
    assert resultado.returncode == 0, resultado.stdout


def test_formatar_nao_altera_arquivo_ja_canonico() -> None:
    arquivos = {
        n: c
        for n, c in gerar_pacote(_PAYLOAD, _entrada()).arquivos.items()
        if n.endswith(".tf")
    }
    formatados, resumo = formatar(dict(arquivos))
    assert resumo.formatado is True
    assert formatados == arquivos


def test_formatar_corrige_hcl_torto() -> None:
    """Prova que o `fmt` está de fato rodando, e não passando batido."""
    torto = {"main.tf": 'resource "null_resource" "x" {\n    a = 1\n  bbbb = 2\n}\n'}
    formatados, resumo = formatar(torto)
    assert resumo.formatado is True
    assert formatados["main.tf"] != torto["main.tf"]
    assert "  a    = 1" in formatados["main.tf"]


def test_validate_nao_roda_e_o_motivo_fica_registrado() -> None:
    """Decisão de escopo: `validate` exige `init`, que baixa o provider. O
    pacote diz por que não validou em vez de só omitir."""
    _, resumo = formatar({"main.tf": 'variable "x" {\n  type = string\n}\n'})
    assert resumo.validado is False
    assert resumo.motivo_sem_validacao
    assert "init" in resumo.motivo_sem_validacao
