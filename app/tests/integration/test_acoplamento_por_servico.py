"""Acoplamento por serviço nos dois bills reais (PLANO_LOCKIN_INVENTARIO, A1 a A3).

A tela do bill AWS mostrava 68 linhas para ~30 serviços, "Bancos de dados
(agregado)" ao lado do AmazonRDS e o "Do total" passando de 100%. Aqui a visão
sai do mesmo payload da análise e tem de fechar com o gasto.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

import pytest

from app.domain.analysis import MigrationContext
from app.domain.services.acoplamento_por_servico import montar_acoplamento_por_servico
from app.domain.services.decision_support import build_decision_support
from app.use_cases.analysis.consultar_acoplamento import precos_por_servico
from app.use_cases.analysis.equivalence_projection import project_equivalences
from app.use_cases.analysis.run_analysis_pipeline import build_pipeline_llm_input_payload

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _payload(ctx: MigrationContext) -> dict[str, Any]:
    payload, _ = build_pipeline_llm_input_payload(ctx)
    return payload


async def test_uma_linha_por_servico_fechando_com_o_gasto(
    aws_context: MigrationContext, gcp_context: MigrationContext
) -> None:
    for ctx in (aws_context, gcp_context):
        _uma_linha_por_servico(_payload(ctx))


async def test_por_destino_e_o_que_o_score_usa(
    aws_context: MigrationContext, gcp_context: MigrationContext
) -> None:
    for ctx in (aws_context, gcp_context):
        _por_destino_igual_ao_score(_payload(ctx))


def _uma_linha_por_servico(payload: dict[str, Any]) -> None:
    r = montar_acoplamento_por_servico(payload)

    ids = Counter(s.id for s in r.servicos)
    assert max(ids.values()) == 1, "serviço repetido"
    assert not any("agregado" in s.nome for s in r.servicos)
    assert r.reconciliacao.status == "fecha", r.reconciliacao
    assert sum(s.fracao or 0 for s in r.servicos) <= 1
    assert r.resumo.servicos == len(r.servicos)
    assert (
        r.resumo.low + r.resumo.medium + r.resumo.high + r.resumo.a_confirmar == r.resumo.servicos
    )
    # Os pools detalham as máquinas virtuais, não viram linha própria.
    com_pools = [s for s in r.servicos if s.pools]
    assert len(com_pools) == 1 and com_pools[0].service_type == "virtual_server"
    assert not r.pools_sem_servico
    assert len(com_pools[0].pools) == len(payload["pools_computacao"])


def _por_destino_igual_ao_score(payload: dict[str, Any]) -> None:
    visao = {s.nome: s for s in montar_acoplamento_por_servico(payload).servicos}
    decisao = build_decision_support(payload)
    comparados = 0
    for cenario in decisao.provider_scores:
        tecnico = next(a for a in cenario.axes if a.id == "technical")
        lock = next(f for f in tecnico.factors if f.id == "lock_in")
        for amostra in lock.evidence:
            assert visao[amostra["workload"]].por_destino[cenario.provider] == amostra["lock_in"]
            comparados += 1
        fator = next(f for f in tecnico.factors if f.id == "refactoring")
        for amostra in fator.evidence:
            servico = visao[amostra["workload"]]
            assert (
                servico.refatoracao_por_destino[cenario.provider]
                == (amostra["requires_refactoring"])
            )
            comparados += 1
    assert comparados > 0


async def test_a_tabela_de_equivalencia_continua_igual(aws_context: MigrationContext) -> None:
    """A visão nova não mexe na lista de preço por destino (blocos A e B)."""
    payload = _payload(aws_context)
    antes = [r.model_dump() for r in project_equivalences(payload)]
    montar_acoplamento_por_servico(payload)
    assert [r.model_dump() for r in project_equivalences(payload)] == antes


async def test_banco_cotado_em_cada_motor_e_opcoes_com_preco(
    aws_context: MigrationContext, gcp_context: MigrationContext
) -> None:
    """PLANO_EQUIVALENTES_E_TARIFAS §6: cada destino do banco cotado em MySQL e em
    PostgreSQL, e a opção marcada no total com o preço da tabela de equivalentes."""
    for ctx in (aws_context, gcp_context):
        payload = _payload(ctx)
        cotacoes = payload["custos_auxiliares"].get("equivalentes_banco_dados") or []
        for cotacao in cotacoes:
            assert set(cotacao["por_motor"]) == {"mysql", "postgresql"}, cotacao["provedor"]
            assert all(v > 0 for v in cotacao["por_motor"].values())
        precos = precos_por_servico(r.model_dump() for r in project_equivalences(payload))
        visao = montar_acoplamento_por_servico(payload, precos)
        for servico in visao.servicos:
            for destino, opcoes in servico.opcoes_por_destino.items():
                preco = precos.get((servico.nome, destino))
                for opcao in opcoes:
                    if opcao.no_total:
                        assert opcao.custo_estimado is not None and preco is not None
                        assert abs(opcao.custo_estimado - preco) <= Decimal("0.01")
                    if opcao.custo_estimado is not None:
                        assert opcao.custo_estimado >= 0
