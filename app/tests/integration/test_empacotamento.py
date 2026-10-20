"""Cotação de compute nos dois bills reais (PLANO_EMPACOTAMENTO).

Dois defeitos faziam o destino sair várias vezes mais caro que a origem:

- F1: pool com capacidade média fracionada (0,13 vCPU, US$ 7) cotado como
  máquina inteira ligada 730 h (US$ 86, 11,6x). Agora o custo é o da
  capacidade-hora medida.
- F2: o Savings Plan de compute ficava no saldo mantido ao custo atual enquanto
  o destino pagava on-demand as horas que ele cobria. Agora sai da origem junto
  com os pools.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.analysis import MigrationContext
from app.use_cases.analysis.equivalence_pricing import reconcile_pricing
from app.use_cases.analysis.equivalence_projection import project_equivalences
from app.use_cases.analysis.run_analysis_pipeline import build_pipeline_llm_input_payload

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _payload(ctx: MigrationContext) -> dict[str, Any]:
    payload, _ = build_pipeline_llm_input_payload(ctx)
    return payload


def _razoes(payload: dict[str, Any]) -> dict[str, float]:
    razoes = {}
    for pool in payload["pools_computacao"]:
        origem = pool.get("custo_computacao") or 0
        for e in pool.get("equivalentes") or []:
            custo = (e.get("financeiro") or {}).get("custo_mensal_estimado")
            if origem > 0 and custo is not None:
                razoes[f"{pool['familia']}/{pool['regiao']}→{e['provedor']}"] = custo / origem
    return razoes


async def test_bill_sem_compromisso_nao_cota_pool_varias_vezes_acima(
    gcp_context: MigrationContext,
) -> None:
    # Antes: N1/americas 20x, N1/southamerica-east1 11,6x, E2/southamerica-east1 4,1x.
    acima = {k: round(v, 1) for k, v in _razoes(_payload(gcp_context)).items() if v > 2.5}
    assert not acima, acima


async def test_compromisso_de_compute_sai_da_origem_com_os_pools(
    aws_context: MigrationContext,
) -> None:
    payload = _payload(aws_context)
    compromisso = payload["premissas"]["compromisso_de_compute"]
    # ComputeSP:3yrNoUpfront (8.184,00) + EC2SP:r7g (1.897,65); a reserva de RDS
    # e de ElastiCache fica de fora.
    assert compromisso["valor"] == pytest.approx(10081.65, abs=0.05)
    # O crédito vai para cada destino que cotou pool, e só para ele. Quais destinos
    # cotam depende dos preços do ambiente: o CI só tem o catálogo seed, sem Azure.
    cotaram = {
        e["provedor"].lower()
        for pool in payload["pools_computacao"]
        for e in pool.get("equivalentes") or []
        if (e.get("financeiro") or {}).get("custo_mensal_estimado") is not None
    }
    assert cotaram
    assert set(compromisso["por_provedor"]) == cotaram

    atual = Decimal(str(payload["resumo_executivo"]["custo_mensal_atual"]))
    for linha in reconcile_pricing(project_equivalences(payload), payload):
        assert linha["status"] == "reconciled", linha
        credito = compromisso["por_provedor"].get(linha["provider"], 0)
        # O compromisso entrou no custo coberto, não no saldo mantido.
        saldo = Decimal(str(linha["unchanged_source_balance"]))
        coberto = Decimal(str(linha["source_covered"]))
        assert abs(saldo + coberto - atual) <= Decimal("0.05")
        assert credito <= float(coberto)


async def test_cotacao_declarada_como_capacidade_hora(
    aws_context: MigrationContext, gcp_context: MigrationContext
) -> None:
    for ctx in (aws_context, gcp_context):
        assert _payload(ctx)["premissas"]["cotacao_compute"]["base"] == "capacidade_hora"
