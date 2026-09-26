"""Pacote Terraform a partir de cada mock, validado pelo binário real (E8/E9).

Pula sem o binário e sem cache de providers: o `validate` baixaria centenas de
MB de provider a cada execução. No CI com a imagem (filesystem mirror) e em dev
com `TF_PLUGIN_CACHE_DIR`, roda — um destino por origem, cobrindo os quatro
renderers.
"""

from __future__ import annotations

import os
import shutil
from uuid import uuid4

import pytest

from app.infrastructure.terraform.fmt import formatar
from app.infrastructure.terraform.renderers import renderizar
from app.infrastructure.terraform.validate import validar
from app.schemas.terraform import TerraformGenerationIn
from app.use_cases.analysis.run_analysis_pipeline import build_pipeline_llm_input_payload
from app.use_cases.terraform.build_blueprint import montar_blueprint

from .conftest import _cleanup, _context, _ingest

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(
        shutil.which("terraform") is None
        or not (os.environ.get("TF_PLUGIN_CACHE_DIR") or os.environ.get("TF_CLI_CONFIG_FILE")),
        reason="precisa do binário do Terraform e de cache de providers",
    ),
]

_REGIOES = {
    "gcp": "us-central1",
    "aws": "sa-east-1",
    "azure": "brazilsouth",
    "oci": "sa-saopaulo-1",
}


@pytest.mark.parametrize(
    ("mock", "destino"),
    [("cur", "gcp"), ("bigquery", "aws"), ("azure", "oci"), ("oci", "azure")],
)
async def test_pacote_do_mock_valida(_db_ready, mocks_billing, mock, destino) -> None:
    rid = await _ingest(getattr(mocks_billing, mock))
    try:
        payload, _ = build_pipeline_llm_input_payload(await _context(rid))
    finally:
        await _cleanup(rid)
    entrada = TerraformGenerationIn(
        analysis_job_id=uuid4(),
        project_name=f"mock-{mock}",
        environment="prod",
        target_provider=destino,
        region=_REGIOES[destino],
        availability={"high_availability": True},
    )
    arquivos, resumo = formatar(renderizar(montar_blueprint(payload, entrada)))
    assert resumo.formatado
    validacao = validar(arquivos, formatado=True)
    assert validacao.validado and not validacao.erros, validacao
