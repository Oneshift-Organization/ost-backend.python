"""Representação v2 nos dois bills reais (PLANO_DECISION_SUPPORT_COMPACTO K1).

Só a evidência por item sai; o resto do `decision_support` volta idêntico, e o
bloco fica menor que a metade.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domain.analysis import MigrationContext
from app.use_cases.agent.representacao_v2 import (
    compactar_decision_support,
    reconstruir_sem_evidencias,
    sem_evidencias,
)
from app.use_cases.analysis.run_analysis_pipeline import build_pipeline_llm_input_payload

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _tamanho(valor: Any) -> int:
    return len(json.dumps(valor, ensure_ascii=False, separators=(",", ":")))


async def test_decision_support_compacto_nos_bills_reais(
    aws_context: MigrationContext, gcp_context: MigrationContext
) -> None:
    for ctx in (aws_context, gcp_context):
        payload, _ = build_pipeline_llm_input_payload(ctx)
        decisao = payload["decision_support"]
        compacto = compactar_decision_support(decisao)

        assert reconstruir_sem_evidencias(compacto) == sem_evidencias(decisao)
        assert compacto["selected_provider"] == decisao["selected_provider"]
        assert _tamanho(compacto) < _tamanho(decisao) / 2
