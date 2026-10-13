"""Representação v2 do snapshot: decision_support compacto (PLANO_DECISION_SUPPORT_COMPACTO K1)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.use_cases.agent.representacao_v2 import (
    CONTRATO_V2,
    OMITIDO,
    compactar_decision_support,
    reconstruir_sem_evidencias,
    representacao_v2,
    sem_evidencias,
)
from app.use_cases.analysis.decision_support import prepare_decision_payload


def _workload(
    nome: str, custo: float, lock: str | None, *, refatora: bool = False
) -> dict[str, Any]:
    meta: dict[str, Any] = {"requires_refactoring": refatora}
    if lock:
        meta["vendor_lock_in_level"] = lock
    return {
        "name": nome,
        "category": "database",
        "service_type": "managed_relational_database",
        "cost": custo,
        "migration": {"classe_migracao": "replatform"},
        "source_metadata": meta,
        "type_source": "curado",
        "evidence_source": "catalog_snapshot",
        "targets": [
            {"provider": p, "service_name": f"{p}-db", "metadata": {"requires_refactoring": False}}
            for p in ("gcp", "oci")
        ],
    }


def _payload(*, economia: float = 50.0) -> dict[str, Any]:
    return prepare_decision_payload(
        {
            "provedor_origem": "aws",
            "gasto_total": 1000.0,
            "decision_workloads": [
                _workload("RDS", 600, "medium"),
                _workload("S3", 300, "low"),
                _workload("WAF", 100, None, refatora=True),
            ],
            "resumo_executivo": {
                "custo_mensal_atual": 1000.0,
                "por_provedor": [
                    {
                        "provedor": p,
                        "economia_mensal": economia,
                        "cobertura": 0.9,
                        "custo_estimado_total": 1000.0 - economia,
                    }
                    for p in ("gcp", "oci")
                ],
            },
        }
    )


def test_so_a_evidencia_por_item_sai() -> None:
    decisao = _payload()["decision_support"]
    compacto = compactar_decision_support(decisao)
    assert reconstruir_sem_evidencias(compacto) == sem_evidencias(decisao)
    assert compacto["detalhe"]["omitido"] == list(OMITIDO)
    assert "score" not in compacto
    assert compacto["score_do_selecionado"] == decisao["selected_provider"]


def test_textos_de_regra_saem_uma_vez() -> None:
    compacto = compactar_decision_support(_payload()["decision_support"])
    usados = [e["rationale"] for e in compacto["workload_strategies"]]
    assert len(usados) == 6 and set(usados) <= set(compacto["textos"])
    assert len(compacto["textos"]) < len(usados) + sum(
        len(e.get("missing_information", [])) for e in compacto["workload_strategies"]
    )


def test_distribuicao_preserva_quantidade_e_custo() -> None:
    decisao = _payload()["decision_support"]
    compacto = compactar_decision_support(decisao)
    for completo, curto in zip(
        decisao["provider_scores"], compacto["provider_scores"], strict=True
    ):
        for eixo_c, eixo_k in zip(completo["axes"], curto["axes"], strict=True):
            for f_c, f_k in zip(eixo_c["factors"], eixo_k["factors"], strict=True):
                if "distribuicao" not in f_k:
                    assert f_k == f_c
                    continue
                grupos = f_k["distribuicao"].values()
                assert sum(g["servicos"] for g in grupos) == len(f_c["evidence"])
                assert sum(Decimal(g["custo"]) for g in grupos) == sum(
                    Decimal(e["cost_weight"]) for e in f_c["evidence"]
                )
                # Os números do fator não mudam.
                assert {k: v for k, v in f_k.items() if k != "distribuicao"} == {
                    k: v for k, v in f_c.items() if k != "evidence"
                }
    lock = next(
        f["distribuicao"]
        for a in compacto["provider_scores"][0]["axes"]
        for f in a["factors"]
        if f["id"] == "lock_in"
    )
    assert lock == {
        "desconhecido": {"servicos": 1, "custo": "100"},
        "low": {"servicos": 1, "custo": "300"},
        "medium": {"servicos": 1, "custo": "600"},
    }


def test_sem_provedor_selecionado_continua_sem() -> None:
    decisao = _payload(economia=-50.0)["decision_support"]
    assert decisao["selected_provider"] is None and decisao["score"] is None
    compacto = compactar_decision_support(decisao)
    assert compacto["selected_provider"] is None
    assert compacto["score_do_selecionado"] is None
    assert reconstruir_sem_evidencias(compacto) == sem_evidencias(decisao)


def test_score_que_nao_e_copia_vai_inteiro() -> None:
    decisao = _payload()["decision_support"]
    decisao["score"] = {**decisao["score"], "value": 1.0}
    compacto = compactar_decision_support(decisao)
    assert compacto["score_do_selecionado"] is None
    assert compacto["score"]["value"] == 1.0


def test_representacao_v2_so_troca_o_decision_support() -> None:
    payload = _payload()
    entregue = representacao_v2(payload)
    assert {k: v for k, v in entregue.items() if k != "decision_support"} == {
        k: v for k, v in payload.items() if k != "decision_support"
    }
    assert entregue["decision_support"]["detalhe"]["rota"].endswith("/decision-support")
    assert CONTRATO_V2 == "agent-snapshot-v2"
