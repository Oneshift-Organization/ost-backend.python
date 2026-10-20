"""Provedor que o agente comparou e o backend não avaliou vira sugestão (lote v4, V4.2)."""

from __future__ import annotations

from typing import Any

from app.use_cases.analysis.agent_result import provedores_nao_avaliados


def _payload() -> dict[str, Any]:
    return {
        "provedor_origem": "aws",
        "decision_support": {
            "provider_scores": [{"provider": "oci"}, {"provider": "azure"}, {"provider": "gcp"}]
        },
    }


def _resposta(*comparacao: dict[str, Any]) -> dict[str, Any]:
    return {"provider_comparison": list(comparacao)}


def test_provedor_fora_do_score_vira_sugestao_com_o_texto_do_agente() -> None:
    sugestoes = provedores_nao_avaliados(
        _payload(),
        _resposta(
            {"provider": "OCI", "overall_score": 80},
            {
                "provider": "IBM",
                "overall_score": 95,
                "estimated_monthly_cost": 10.0,
                "strengths": ["preço baixo"],
                "weaknesses": [],
            },
        ),
    )
    assert len(sugestoes) == 1
    sugestao = sugestoes[0]
    assert sugestao["tipo"] == "provedor_nao_avaliado"
    assert sugestao["provedor"] == "ibm"
    # Só o texto do agente: nota e custo dele não passam
    assert sugestao["o_que_o_agente_disse"] == {"strengths": ["preço baixo"]}


def test_origem_e_provedores_avaliados_nao_viram_sugestao() -> None:
    resposta = _resposta({"provider": "aws"}, {"provider": "gcp"}, {"provider": "Azure"})
    assert provedores_nao_avaliados(_payload(), resposta) == []


def test_repetido_vira_uma_sugestao_so() -> None:
    resposta = _resposta({"provider": "ibm"}, {"provider": "IBM"})
    assert len(provedores_nao_avaliados(_payload(), resposta)) == 1


def test_payload_sem_decisao_do_backend_nao_gera_nada() -> None:
    assert provedores_nao_avaliados({}, _resposta({"provider": "ibm"})) == []
