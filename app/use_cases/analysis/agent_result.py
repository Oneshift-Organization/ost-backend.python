"""Recepção da análise do agente: autoridade + sugestões.

O agente busca a request, produz a análise e devolve aqui. Este módulo:

1. traduz o payload determinístico do backend num mapa de caminho → valor;
2. aplica `domain/services/authority.aplicar_autoridade` — sobrescreve só o que
   é **derivação**, preserva estimativa e julgamento;
3. processa as **sugestões**: contestação de severidade alta rebaixa a confiança
   **sem trocar o número**;
4. devolve o corrigido, com o bruto do agente ao lado para auditoria.

O passo 3 é o que faz a inteligência do modelo entrar sem contaminar o cálculo.
Ele vira sinal, não fonte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.services.authority import ResultadoAutoridade, Substituicao, aplicar_autoridade
from app.domain.services.confidence import rebaixar
from app.schemas.analysis_v2 import AnalysisNarrativeDraft, AnalysisV2Response
from app.use_cases.analysis.decision_support import impose_decision

# Tipos de sugestão que afetam a confiança quando a severidade é alta. Os demais
# (oportunidade, risco, dado_faltante) são informativos — o agente comentando o
# contexto, não duvidando de um insumo nosso.
_CONTESTACOES = frozenset({"contestacao_preco", "contestacao_equivalencia"})

_EFEITO_REBAIXOU = "confianca_rebaixada"
_EFEITO_INFO = "informativo"

# Carimbado aqui, nunca aceito do agente — pelo mesmo motivo que
# `efeito_backend`: ele não pode afirmar na tela que uma dúvida dele é nossa.
_ORIGEM_AGENTE = "agente"

# Campos que o backend reescreve, descartando o que vier na entrada.
_CARIMBADOS = frozenset({"efeito_backend", "origem"})


@dataclass(frozen=True, slots=True)
class ResultadoRecepcao:
    resposta: dict[str, Any]
    sugestoes: list[dict[str, Any]]
    confianca_final: str
    autoridade: ResultadoAutoridade


def mapear_verdade(
    payload_backend: dict[str, Any],
    resposta_agente: dict[str, Any],
) -> dict[str, Any]:
    """Traduz o payload determinístico em caminhos de campo da resposta.

    Precisa ver a resposta do agente porque o **índice** de
    `provider_comparison` depende da ordem em que ELE mandou. Casar por posição
    escreveria o custo da OCI no item do Azure — o número certo no lugar errado,
    que é pior que número errado, porque parece plausível.

    Só emite caminhos que existem na resposta: campo ausente é assunto de
    emissão, não de autoridade (ver `authority._escrever`).
    """
    resumo = payload_backend.get("resumo_executivo") or {}
    por_provedor = resumo.get("por_provedor") or []
    if not por_provedor:
        return {}

    verdade: dict[str, Any] = {}
    do_backend = {str(p.get("provedor", "")).lower(): p for p in por_provedor}

    recomendado = resumo.get("recomendado")
    if recomendado:
        verdade["recommended_provider"] = str(recomendado).upper()

    for i, pc in enumerate(resposta_agente.get("provider_comparison") or []):
        chave = str(pc.get("provider", "")).lower()
        real = do_backend.get(chave)
        if real is None:
            # Provedor que o agente inventou e o backend não avaliou. Não há
            # verdade a impor — a limpeza dele é outra decisão.
            continue
        verdade[f"provider_comparison.{i}.estimated_monthly_cost"] = float(
            real["custo_estimado_total"]
        )
        verdade[f"provider_comparison.{i}.estimated_monthly_savings"] = float(
            real["economia_mensal"]
        )
        verdade[f"provider_comparison.{i}.confidence_level"] = str(
            real.get("confianca", "low")
        )

    melhor = do_backend.get(str(recomendado).lower()) if recomendado else None
    atual = float(resumo.get("custo_mensal_atual") or 0.0)
    economia = float(melhor["economia_mensal"]) if melhor else 0.0
    verdade["savings_summary.total_monthly_savings_usd"] = economia
    verdade["savings_summary.percentage_reduction"] = (
        round(economia / atual * 100, 2) if atual > 0 else 0.0
    )
    verdade["savings_summary.confidence_level"] = (
        str(melhor.get("confianca", "low")) if melhor else "low"
    )

    conf = (payload_backend.get("premissas") or {}).get("confianca_analise") or {}
    if conf.get("nivel"):
        verdade["analysis_confidence"] = conf["nivel"]

    return verdade


def processar_sugestoes(
    sugestoes: list[dict[str, Any]],
    confianca_inicial: str,
) -> tuple[list[dict[str, Any]], str]:
    """Preenche `efeito_backend` e devolve a confiança após as contestações.

    `efeito_backend` e `origem` **nunca** vêm do agente — se viessem, ele
    poderia afirmar na tela que "a confiança foi rebaixada" quando não foi, ou
    assinar a própria dúvida como se fosse do backend. Qualquer valor que chegue
    nesses campos é descartado e reescrito aqui.

    Só contestação com severidade alta rebaixa, e **um degrau no total**, não um
    por sugestão: dez contestações não tornam a análise dez vezes menos
    confiável, e permitir isso daria ao agente um jeito de zerar a confiança
    repetindo a mesma queixa.
    """
    processadas: list[dict[str, Any]] = []
    houve_contestacao_alta = False

    for s in sugestoes:
        tipo = str(s.get("tipo", ""))
        alta = str(s.get("severidade", "")).lower() == "alta"
        contesta = tipo in _CONTESTACOES and alta
        houve_contestacao_alta = houve_contestacao_alta or contesta
        processadas.append(
            {
                **{k: v for k, v in s.items() if k not in _CARIMBADOS},
                "efeito_backend": _EFEITO_REBAIXOU if contesta else _EFEITO_INFO,
                "origem": _ORIGEM_AGENTE,
            }
        )

    final = rebaixar(confianca_inicial) if houve_contestacao_alta else confianca_inicial
    return processadas, final


def sugestoes_do_backend(payload_backend: dict[str, Any]) -> list[dict[str, Any]]:
    """Alertas que o próprio backend levantou ao montar o payload.

    Chegam com `efeito_backend` preenchido na origem (`Alerta.as_sugestao`) —
    não é promessa, é o que já foi aplicado à confiança. Reescrevê-los aqui
    perderia o `origem: backend`, e é ele que permite à tela distinguir "o
    modelo desconfia do nosso preço" de "nós desconfiamos do nosso preço".
    """
    premissas = payload_backend.get("premissas") or {}
    return [dict(s) for s in (premissas.get("sugestoes_backend") or [])]


def receber_analise_do_agente(
    payload_backend: dict[str, Any],
    resposta_agente: dict[str, Any],
    sugestoes: list[dict[str, Any]] | None = None,
) -> ResultadoRecepcao:
    """Aplica autoridade e sugestões. Função pura — persistência é da rota."""
    verdade = mapear_verdade(payload_backend, resposta_agente)
    autoridade = aplicar_autoridade(resposta_agente, verdade)

    if "decision_support" in payload_backend:
        finalized = impose_decision(
            AnalysisNarrativeDraft.model_validate(autoridade.resposta), payload_backend,
        )
        imposed = AnalysisV2Response.model_validate(finalized.model_dump()).model_dump(mode="json")
        # Preserve other agent-surface fields (payback, provenance, etc.).
        corrected = {**autoridade.resposta, **imposed}
        overrides = tuple(Substituicao(key, autoridade.resposta.get(key), value)
                          for key, value in imposed.items()
                          if autoridade.resposta.get(key) != value)
        autoridade = ResultadoAutoridade(corrected, autoridade.substituicoes + overrides)

    conf_inicial = str(
        autoridade.resposta.get("analysis_confidence")
        or (payload_backend.get("premissas") or {})
        .get("confianca_analise", {})
        .get("nivel")
        or "low"
    )
    processadas, conf_final = processar_sugestoes(sugestoes or [], conf_inicial)

    # As do backend vêm primeiro e **já processadas**: o efeito delas foi
    # aplicado em `build_migration_context`, e `conf_inicial` já sai de lá
    # rebaixada. Passá-las de novo por `processar_sugestoes` contaria o mesmo
    # rebaixamento duas vezes.
    todas = sugestoes_do_backend(payload_backend) + processadas

    resposta = dict(autoridade.resposta)
    resposta["analysis_confidence"] = conf_final
    resposta["sugestoes"] = todas

    return ResultadoRecepcao(
        resposta=resposta,
        sugestoes=todas,
        confianca_final=conf_final,
        autoridade=autoridade,
    )
