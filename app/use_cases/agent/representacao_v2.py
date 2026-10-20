"""Representação `agent-snapshot-v2`: `decision_support` compacto (PLANO_DECISION_SUPPORT_COMPACTO).

No bill AWS de referência o `decision_support` é 56% do payload que o agente
lê. Quase tudo é repetição: o `score` é cópia de um item de `provider_scores`,
os textos de regra se repetem a cada estratégia, e cada fator do score lista uma
evidência por serviço só para dizer peso e valor.

A forma compacta tira a repetição sem perda e troca a evidência por item pela
distribuição dela (quantos serviços e quanto custo em cada valor), com o
ponteiro para o detalhe completo. Bloqueios, elegibilidade, provedor
selecionado, notas, faixas, cobertura, pesos e regras ficam intactos: são eles
que decidem, e o MCP pediu que nada disso saia (revisão de 11/10, itens 5 e 6).

`reconstruir_sem_evidencias` desfaz a compactação. É a prova, nos testes, de
que só a evidência por item saiu, e o MCP pode fazer a mesma conta.

Funções puras, sem I/O. Preparação opcional por contract=agent-snapshot-v2 (K2).
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from app.use_cases.analysis.topologia_no_payload import compactar_topologia

CONTRATO_V2 = "agent-snapshot-v2"
ROTA_DO_DETALHE = "/v1/agent/snapshots/{snapshot_id}/decision-support"
OMITIDO = (
    "workload_strategies[].evidence",
    "provider_scores[].axes[].factors[].evidence",
)

# Campos de `WorkloadStrategy` com padrão: omitidos quando iguais ao padrão.
_PADROES_DA_ESTRATEGIA: Mapping[str, Any] = {
    "target_service": None,
    "strategy": None,
    "requires_refactoring": None,
    "vendor_lock_in_level": None,
    "alternative_targets": [],
    "missing_information": [],
    "assumptions": [],
}
_TEXTOS_EM_LISTA = ("missing_information", "assumptions")
# O valor que cada fator por serviço registra na evidência.
_VALOR_DO_FATOR = ("lock_in", "requires_refactoring")
_DESCONHECIDO = "desconhecido"


def representacao_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    """O payload que o agente recebe na v2: o da v1 com o `decision_support` compacto."""
    entregue = dict(compactar_topologia(dict(payload)))
    decisao = entregue.get("decision_support")
    if isinstance(decisao, Mapping):
        entregue["decision_support"] = compactar_decision_support(decisao)
    return entregue


def compactar_decision_support(decisao: Mapping[str, Any]) -> dict[str, Any]:
    textos: dict[str, str] = {}
    ids: dict[str, str] = {}

    def id_do_texto(texto: str) -> str:
        if texto not in ids:
            ids[texto] = f"t{len(ids) + 1}"
            textos[ids[texto]] = texto
        return ids[texto]

    estrategias = [
        _estrategia_compacta(e, id_do_texto) for e in decisao.get("workload_strategies") or []
    ]
    cenarios = [_cenario_compacto(c) for c in decisao.get("provider_scores") or []]
    compacto: dict[str, Any] = {
        k: v
        for k, v in decisao.items()
        if k not in ("score", "provider_scores", "workload_strategies")
    }
    score = decisao.get("score")
    referencia = _referencia_do_score(score, decisao.get("provider_scores") or [])
    if score is not None and referencia is None:
        compacto["score"] = score  # não é cópia de um cenário: vai inteiro
    compacto["score_do_selecionado"] = referencia
    compacto["provider_scores"] = cenarios
    compacto["workload_strategies"] = estrategias
    compacto["textos"] = textos
    compacto["detalhe"] = {"rota": ROTA_DO_DETALHE, "omitido": list(OMITIDO)}
    return compacto


def reconstruir_sem_evidencias(compacto: Mapping[str, Any]) -> dict[str, Any]:
    """O `decision_support` completo, com as evidências por item vazias."""
    textos = compacto.get("textos") or {}
    cenarios = [_cenario_reconstruido(c) for c in compacto.get("provider_scores") or []]
    decisao: dict[str, Any] = {
        k: v
        for k, v in compacto.items()
        if k not in ("score_do_selecionado", "textos", "detalhe", "provider_scores")
    }
    referencia = compacto.get("score_do_selecionado")
    if "score" not in decisao:
        decisao["score"] = next((c for c in cenarios if c.get("provider") == referencia), None)
    decisao["provider_scores"] = cenarios
    decisao["workload_strategies"] = [
        _estrategia_reconstruida(e, textos) for e in compacto.get("workload_strategies") or []
    ]
    return decisao


def sem_evidencias(decisao: Mapping[str, Any]) -> dict[str, Any]:
    """O `decision_support` completo com as evidências por item esvaziadas: o
    que `reconstruir_sem_evidencias` tem de devolver."""
    copia = dict(decisao)
    copia["workload_strategies"] = [
        {**e, "evidence": []} for e in decisao.get("workload_strategies") or []
    ]

    def cenario(c: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **c,
            "axes": [
                {
                    **a,
                    "factors": [
                        {**f, "evidence": []} if _por_servico(f) else dict(f)
                        for f in a.get("factors") or []
                    ],
                }
                for a in c.get("axes") or []
            ],
        }

    copia["provider_scores"] = [cenario(c) for c in decisao.get("provider_scores") or []]
    if isinstance(decisao.get("score"), Mapping):
        copia["score"] = cenario(decisao["score"])
    return copia


# ── estratégias ──────────────────────────────────────────────────────────────


def _estrategia_compacta(estrategia: Mapping[str, Any], id_do_texto: Any) -> dict[str, Any]:
    item: dict[str, Any] = {}
    for chave, valor in estrategia.items():
        if chave == "evidence":
            continue
        if chave in _PADROES_DA_ESTRATEGIA and valor == _PADROES_DA_ESTRATEGIA[chave]:
            continue
        if chave == "rationale":
            item[chave] = id_do_texto(valor)
        elif chave in _TEXTOS_EM_LISTA:
            item[chave] = [id_do_texto(t) for t in valor]
        else:
            item[chave] = valor
    return item


def _estrategia_reconstruida(item: Mapping[str, Any], textos: Mapping[str, str]) -> dict[str, Any]:
    estrategia: dict[str, Any] = {**_PADROES_DA_ESTRATEGIA, **item}
    estrategia["rationale"] = textos[item["rationale"]]
    for chave in _TEXTOS_EM_LISTA:
        estrategia[chave] = [textos[t] for t in item.get(chave) or []]
    estrategia["evidence"] = []
    return estrategia


# ── cenários do score ────────────────────────────────────────────────────────


def _cenario_compacto(cenario: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **cenario,
        "axes": [
            {**eixo, "factors": [_fator_compacto(f) for f in eixo.get("factors") or []]}
            for eixo in cenario.get("axes") or []
        ],
    }


def _cenario_reconstruido(cenario: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **cenario,
        "axes": [
            {
                **eixo,
                "factors": [
                    {k: v for k, v in f.items() if k != "distribuicao"}
                    | ({"evidence": []} if "distribuicao" in f else {})
                    for f in eixo.get("factors") or []
                ],
            }
            for eixo in cenario.get("axes") or []
        ],
    }


def _por_servico(fator: Mapping[str, Any]) -> bool:
    evidencias = fator.get("evidence") or []
    return bool(evidencias) and all(isinstance(e, Mapping) and "workload" in e for e in evidencias)


def _fator_compacto(fator: Mapping[str, Any]) -> dict[str, Any]:
    """Fator por serviço vira distribuição; os de evidência única ficam inteiros."""
    if not _por_servico(fator):
        return dict(fator)
    distribuicao: dict[str, dict[str, Any]] = {}
    for evidencia in fator.get("evidence") or []:
        chave = {"lock_in": "lock_in", "refactoring": "requires_refactoring"}.get(fator.get("id"))
        if chave is None or evidencia.get(chave) is None:
            chave = "assessed_value" if "assessed_value" in evidencia else next(
                (c for c in _VALOR_DO_FATOR if c in evidencia), None
            )
        valor = evidencia.get(chave) if chave else None
        rotulo = _DESCONHECIDO if valor is None else str(valor).lower()
        grupo = distribuicao.setdefault(rotulo, {"servicos": 0, "custo": Decimal(0)})
        grupo["servicos"] += 1
        grupo["custo"] += _decimal(evidencia.get("cost_weight"))
    compacto = {k: v for k, v in fator.items() if k != "evidence"}
    compacto["distribuicao"] = {
        rotulo: {"servicos": g["servicos"], "custo": str(g["custo"])}
        for rotulo, g in sorted(distribuicao.items())
    }
    return compacto


def _referencia_do_score(score: Any, cenarios: list[Mapping[str, Any]]) -> str | None:
    if not isinstance(score, Mapping):
        return None
    provedor = score.get("provider")
    igual = next((c for c in cenarios if c.get("provider") == provedor), None)
    return str(provedor) if igual is not None and dict(igual) == dict(score) else None


def _decimal(valor: Any) -> Decimal:
    try:
        return Decimal(str(valor)) if valor is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)
