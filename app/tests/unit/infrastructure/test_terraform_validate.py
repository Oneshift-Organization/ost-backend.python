"""`terraform validate` — o que o `fmt` não vê.

`fmt` olha sintaxe. `validate` compara contra o schema do provider: atributo que
não existe, tipo errado, argumento obrigatório faltando. Foi rodando isto à mão
que apareceram dois bugs reais — nome de recurso com `_`, que o GCP recusa, e
`labels` escrito como bloco em vez de atributo.

Os testes que precisam do binário pulam sem ele. Os de política — desligado por
default, recusa sem cache de provider — rodam sempre, porque são justamente as
condições que impedem a validação de rodar em produção por engano.
"""

from __future__ import annotations

import os
import shutil

import pytest

from app.core import config
from app.core.config import Settings
from app.infrastructure.terraform.renderers import renderizar
from app.infrastructure.terraform.validate import validar
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.terraform.build_blueprint import montar_blueprint

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
    "custos_auxiliares": {"armazenamento_ssd": 800.0, "banco_dados": 620.0},
}


def _arquivos() -> dict[str, str]:
    entrada = TerraformGenerationIn(
        analysis_job_id="62d67cd7-2b80-4e61-9e72-2f17505d262c",
        project_name="proj-teste",
        target_provider="gcp",
        region="us-central1",
        network={"cidr": "10.0.0.0/16"},
    )
    return renderizar(montar_blueprint(_PAYLOAD, entrada))


@pytest.fixture
def _ligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERRAFORM_VALIDATE_ENABLED", "true")
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


# ── política: quando NÃO roda ───────────────────────────────────────────


def test_ligado_por_padrao_desde_a_entrega() -> None:
    """Default mudou para `True` em 13/09/2026, junto com o Dockerfile que traz
    o binário e o mirror de provider."""
    assert Settings(_env_file=None).terraform_validate_enabled is True


def test_desligar_explicitamente_e_declarado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quando alguém desliga, o resumo diz que não validou em vez de fingir."""
    monkeypatch.setenv("TERRAFORM_VALIDATE_ENABLED", "false")
    config.get_settings.cache_clear()
    try:
        r = validar(_arquivos(), formatado=True)
        assert r.validado is False
        assert r.motivo_sem_validacao == "validação desabilitada neste ambiente"
    finally:
        config.get_settings.cache_clear()


@pytest.mark.skipif(
    shutil.which("terraform") is None,
    reason="sem o binário, a checagem de binário dispara antes da de cache",
)
def test_sem_cache_de_provider_recusa(_ligado, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem `TF_PLUGIN_CACHE_DIR` o `init` rebaixa ~99 MB por geração. Com carga
    isso satura a rede do cluster e transforma uma feature barata numa cara sem
    ninguém perceber — recusar é melhor que aceitar."""
    monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)
    monkeypatch.delenv("TF_CLI_CONFIG_FILE", raising=False)
    r = validar(_arquivos(), formatado=True)
    assert r.validado is False
    assert "TF_PLUGIN_CACHE_DIR" in (r.motivo_sem_validacao or "")
    assert "TF_CLI_CONFIG_FILE" in (r.motivo_sem_validacao or "")


def test_o_formatado_atravessa_intacto(_ligado, monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualquer que seja o motivo de não validar, o `fmt` não é apagado."""
    monkeypatch.delenv("TF_PLUGIN_CACHE_DIR", raising=False)
    monkeypatch.delenv("TF_CLI_CONFIG_FILE", raising=False)
    assert validar(_arquivos(), formatado=True).formatado is True
    assert validar(_arquivos(), formatado=False).formatado is False


# ── com o binário presente ──────────────────────────────────────────────

_precisa_binario = pytest.mark.skipif(
    shutil.which("terraform") is None or not os.environ.get("TF_PLUGIN_CACHE_DIR"),
    reason="precisa do binário do Terraform e de TF_PLUGIN_CACHE_DIR",
)


@_precisa_binario
def test_o_pacote_real_e_valido_contra_o_schema_do_provider(_ligado) -> None:
    """A asserção mais forte do projeto: o que geramos é Terraform aceito pelo
    provider do Google, não só texto com sintaxe correta."""
    r = validar(_arquivos(), formatado=True)
    assert r.validado is True, r.erros
    assert r.erros == []


@_precisa_binario
def test_configuracao_invalida_e_reprovada_com_motivo(_ligado) -> None:
    arquivos = _arquivos()
    arquivos["main.tf"] += (
        '\nresource "google_compute_instance" "x" {\n'
        "  atributo_inexistente = 1\n}\n"
    )
    r = validar(arquivos, formatado=True)
    assert r.validado is False
    assert any("atributo_inexistente" in e for e in r.erros)


@_precisa_binario
def test_erro_nao_vaza_caminho_temporario(_ligado) -> None:
    """O texto vai para a tela do cliente."""
    arquivos = _arquivos()
    arquivos["main.tf"] += '\nresource "google_compute_instance" "y" {\n  z = 1\n}\n'
    for erro in validar(arquivos, formatado=True).erros:
        assert "/tmp" not in erro
        assert len(erro) <= 400
