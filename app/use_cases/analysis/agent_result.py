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

from app.domain.services.authority import ResultadoAutoridade, aplicar_autoridade
from app.domain.services.confidence import rebaixar

# Tipos de sugestão que afetam a confiança quando a severidade é alta. Os demais
# (oportunidade, risco, dado_faltante) são informativos — o agente comentando o
# contexto, não duvidando de um insumo nosso.
_CONTESTACOES = frozenset({"contestacao_preco", "contestacao_equivalencia"})

_EFEITO_REBAIXOU = "confianca_rebaixada"
_EFEITO_INFO = "informativo"


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

    `efeito_backend` **nunca** vem do agente — se viesse, ele poderia afirmar na
    tela que "a confiança foi rebaixada" quando não foi. Qualquer valor que
    chegue nesse campo é descartado e reescrito aqui.

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
                **{k: v for k, v in s.items() if k != "efeito_backend"},
                "efeito_backend": _EFEITO_REBAIXOU if contesta else _EFEITO_INFO,
            }
        )

    final = rebaixar(confianca_inicial) if houve_contestacao_alta else confianca_inicial
    return processadas, final


def receber_analise_do_agente(
    payload_backend: dict[str, Any],
    resposta_agente: dict[str, Any],
    sugestoes: list[dict[str, Any]] | None = None,
) -> ResultadoRecepcao:
    """Aplica autoridade e sugestões. Função pura — persistência é da rota."""
    verdade = mapear_verdade(payload_backend, resposta_agente)
    autoridade = aplicar_autoridade(resposta_agente, verdade)

    conf_inicial = str(
        autoridade.resposta.get("analysis_confidence")
        or (payload_backend.get("premissas") or {})
        .get("confianca_analise", {})
        .get("nivel")
        or "low"
    )
    processadas, conf_final = processar_sugestoes(sugestoes or [], conf_inicial)

    resposta = dict(autoridade.resposta)
    resposta["analysis_confidence"] = conf_final
    resposta["sugestoes"] = processadas

    return ResultadoRecepcao(
        resposta=resposta,
        sugestoes=processadas,
        confianca_final=conf_final,
        autoridade=autoridade,
    )
