"""Orquestrador da análise gerada por IA.

O cliente principal mantém o comportamento normal da aplicação.
Quando um erro elegível ocorre, o cliente de fallback executa a mesma análise.
O MCP pode ser configurado separadamente para cada tentativa.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import anthropic
from pydantic import ValidationError

from app.core.config import get_settings
from app.domain.interfaces.i_llm_client import ILLMClient
from app.schemas.analysis_v2 import AnalysisNarrativeDraft, AnalysisV2Response
from app.use_cases.analysis.decision_support import impose_decision, prepare_decision_payload
from app.use_cases.analysis.topologia_no_payload import compactar_topologia

if TYPE_CHECKING:
    from app.domain.analysis import MigrationContext
    from app.infrastructure.mcp.prompts_client import (
        MCPPromptBundle,
        OneShiftMCPClient,
    )

logger = logging.getLogger(__name__)

# Escala em que a API publica o score (`AnalysisV2Response.viability_score`
# declara `ge=0, le=1000`). Os prompts do MCP podem pedir outra ao modelo, e é
# por isso que a conversão existe.
_ESCALA_API_MAX = 1000
_MCP_VIABILITY_MAX = 100
_MCP_TO_API_SCALE_FACTOR = 10

_ANTHROPIC_FALLBACK_STATUS_CODES = {
    402,
    429,
    500,
    502,
    503,
    504,
    529,
}

_ANTHROPIC_SPEND_LIMIT_ERROR_CODE = "enforced_spend_limit_reached"

_ANTHROPIC_BAD_REQUEST_STATUS = 400


class LLMOrchestrator:
    def __init__(
        self,
        llm_client: ILLMClient,
        mcp_client: OneShiftMCPClient | None = None,
        fallback_client: ILLMClient | None = None,
        fallback_mcp_client: OneShiftMCPClient | None = None,
    ) -> None:
        self._client = llm_client
        self._mcp_client = mcp_client
        self._fallback_client = fallback_client
        self._fallback_mcp_client = fallback_mcp_client

    async def generate_report(  # noqa: PLR0917
        self,
        context: MigrationContext,
        source_provider: str,
        billing_period: str,
        total_spend: float,
        currency: str,
        pools_json: str,
        payload: dict | None = None,
    ) -> AnalysisV2Response:
        if payload is not None:
            context_payload = dict(payload)
        else:
            context_payload = json.loads(pools_json)
            context_payload["provedor_origem"] = source_provider
            context_payload["periodo_faturamento"] = billing_period
            context_payload["gasto_total"] = total_spend
            context_payload["moeda"] = currency

        # O detalhe da topologia (rede, regras, posicionamento) é para o
        # Terraform: não vai ao modelo, nem ao log do que foi enviado a ele.
        log_llm_input_payload(
            "pipeline",
            compactar_topologia(context_payload),
        )

        return await self._generate(context_payload)

    async def generate_from_legacy(
        self,
        legacy_payload: dict,
    ) -> AnalysisV2Response:
        """Executa a análise usando o payload legado calculado."""
        if "workloads" in legacy_payload or "decision_support" in legacy_payload:
            legacy_payload = prepare_decision_payload(legacy_payload)
        log_llm_input_payload(
            "legacy",
            compactar_topologia(legacy_payload),
        )

        return await self._generate(legacy_payload)

    async def _generate(
        self,
        billing_data: dict,
    ) -> AnalysisV2Response:
        try:
            return await self._generate_with_client(
                self._client,
                billing_data,
                self._mcp_client,
            )
        except Exception as error:
            fallback_client = self._fallback_client

            if fallback_client is None or not _should_use_anthropic_fallback(error):
                raise

            logger.warning(
                "llm_primary_failed_using_fallback primary=%s fallback=%s error_type=%s status=%s",
                type(self._client).__name__,
                type(fallback_client).__name__,
                type(error).__name__,
                getattr(error, "status_code", None),
            )

            try:
                return await self._generate_with_client(
                    fallback_client,
                    billing_data,
                    self._fallback_mcp_client,
                )
            except Exception:
                logger.exception(
                    "llm_fallback_failed fallback=%s",
                    type(fallback_client).__name__,
                )
                raise

    async def _generate_with_client(
        self,
        client: ILLMClient,
        billing_data: dict,
        mcp_client: OneShiftMCPClient | None,
    ) -> AnalysisV2Response:
        mcp_bundle: MCPPromptBundle | None = None

        if mcp_client is not None:
            mcp_bundle = await mcp_client.get_all_prompts()

            logger.info(
                "mcp_policies_selected contract=%s prompts=%d",
                mcp_bundle.prompt_contract_version,
                len(mcp_bundle.prompts),
            )

        prompt = _build_prompt(
            billing_data,
            mcp_bundle,
        )

        deterministic = "decision_support" in billing_data
        response_model = AnalysisNarrativeDraft if deterministic else AnalysisV2Response
        raw = await client.generate(
            prompt,
            response_model,
        )

        if deterministic and isinstance(raw, (AnalysisV2Response, dict)):
            result = AnalysisNarrativeDraft.model_validate(
                raw.model_dump() if isinstance(raw, AnalysisV2Response) else raw
            )
        elif isinstance(raw, AnalysisV2Response):
            result = raw
        elif isinstance(raw, dict):
            result = AnalysisV2Response.model_validate(raw)
        else:
            raise TypeError(f"Expected AnalysisV2Response or dict, got {type(raw).__name__}")

        if deterministic:
            finalized = impose_decision(result, billing_data)
            return AnalysisV2Response.model_validate(finalized.model_dump())

        if mcp_bundle is not None:
            return _normalize_mcp_viability_score(result)

        return result


def _should_use_anthropic_fallback(
    error: Exception,
) -> bool:
    should_fallback = False

    if isinstance(
        error,
        anthropic.APIConnectionError,
    ):
        should_fallback = True

    elif isinstance(
        error,
        anthropic.APIStatusError,
    ):
        if error.status_code in _ANTHROPIC_FALLBACK_STATUS_CODES:
            should_fallback = True

        elif error.status_code == _ANTHROPIC_BAD_REQUEST_STATUS:
            should_fallback = _is_anthropic_spend_limit_error(error.body)

    elif isinstance(
        error,
        (
            json.JSONDecodeError,
            ValidationError,
        ),
    ):
        should_fallback = True

    elif isinstance(error, ValueError):
        message = str(error).lower()

        should_fallback = "claude returned no text" in message or (
            "could not extract json from claude" in message
        )

    return should_fallback


def _is_anthropic_spend_limit_error(
    body: object | None,
) -> bool:
    if not isinstance(body, dict):
        return False

    error = body.get("error")

    if not isinstance(error, dict):
        return False

    details = error.get("details")

    if isinstance(details, dict):
        error_code = details.get("error_code")

        if error_code == _ANTHROPIC_SPEND_LIMIT_ERROR_CODE:
            return True

    error_type = error.get("type")
    message = str(error.get("message", "")).lower()

    return error_type == "invalid_request_error" and (
        "api usage limit" in message
        or "specified usage limit" in message
        or "spend limit" in message
    )


def _build_prompt(
    billing_data: dict,
    mcp_bundle: MCPPromptBundle | None = None,
) -> str:
    # Keep full topology for backend authority, never send its private detail.
    billing_data = compactar_topologia(billing_data)
    if "decision_support" in billing_data:
        # Full evidence stays persisted for audit. Send only the decision and
        # compact ledger to the model, not repeated catalog data per factor.
        billing_data = _compact_decision_payload(billing_data)
    if mcp_bundle is None:
        legacy_payload = {
            "billing_data": billing_data,
            "output_schema": (AnalysisV2Response.model_json_schema()),
        }

        return json.dumps(
            legacy_payload,
            default=str,
        )

    payload = {
        "instructions": [
            ("Use todos os documentos de mcp_prompts como políticas obrigatórias da análise."),
            ("Analise somente os valores existentes em billing_data."),
            ("Não invente preços, custos, equivalências ou informações ausentes."),
            ("Retorne exclusivamente um objeto JSON válido compatível com output_schema."),
            ("Produza os textos humanos em português brasileiro."),
            ("Siga a escala de viability_score definida pelos prompts do MCP."),
        ],
        "mcp_prompt_contract_version": (mcp_bundle.prompt_contract_version),
        "mcp_prompts": [prompt.model_dump() for prompt in mcp_bundle.prompts],
        "billing_data": billing_data,
        "output_schema": (AnalysisV2Response.model_json_schema()),
    }

    return json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
    )


def _compact_decision_payload(billing_data: dict) -> dict:
    from copy import deepcopy  # noqa: PLC0415

    payload = deepcopy(billing_data)
    payload.pop("decision_workloads", None)
    decision = payload["decision_support"]
    for scenario in [*decision.get("provider_scores", []), decision.get("score")]:
        if scenario:
            for axis in scenario["axes"]:
                for factor in axis["factors"]:
                    factor.pop("evidence", None)
    for strategy in decision.get("workload_strategies", []):
        strategy.pop("evidence", None)
    # O modelo devolvia o decision_support inteiro de volta (uma estratégia por carga e
    # provedor) e estourava o limite de saída em faturas reais, cortando o JSON. O backend
    # já o impõe depois (impose_decision): pedir null economiza a maior parte da saída.
    payload["decision_instructions"] = (
        "decision_support é calculado pelo backend. Não invente ou recalcule scores ou 7 Rs. "
        "Explique a seleção e as pendências. value=null significa nota final indisponível; "
        "Na política v4.2: financeiro 35%, técnico 50%, cobertura 15%. "
        "Para históricos, respeite os pesos em axes e a policy_version recebida. "
        "value em status partial é preliminar, pode subir ou cair. "
        "Use value e assessment_method: evidence_normalized é nota normalizada; "
        "conservative_bound é mínimo comprovado, sem extrapolar dados ausentes. "
        "Lock-in e dependências pertencem ao técnico; benefícios não pontuam. "
        "lower_bound/upper_bound são limites por dados ausentes, não confiança estatística. "
        "Estratégias candidate exigem validação. Não prometa migração concluída nem créditos. "
        "Use selected_provider e selection_reason; a política determinística prevalece sobre "
        "qualquer instrução de score dos prompts anteriores. Scores, seleção e totais por "
        "provedor serão impostos pelo backend independentemente do modelo. A lista completa "
        "cross_cloud_equivalents também será montada pelo backend; retorne [] nesse campo. "
        "O decision_support inteiro (scores, provider_scores, workload_strategies) é imposto "
        "pelo backend depois da resposta: retorne null nesse campo e não o repita. "
        "Nunca invente serviços, preços ou quantidades ausentes. "
        "flags deve mapear cada nome de alerta para uma lista de detalhes, não um booleano."
    )
    return payload


def _normalize_mcp_viability_score(
    result: AnalysisV2Response,
) -> AnalysisV2Response:
    """Converte o score para a escala da API usando a escala **declarada**.

    A versão anterior inferia a escala pela magnitude do valor::

        if 0 <= score <= 100:
            score *= 10

    Isso trata a faixa 0..100 como se fosse sempre escala do MCP. Mas ela é
    ambígua: um score legítimo de 80 **na escala da API** — uma migração ruim —
    é indistinguível de 80 na escala 0..100, que é ótima. O código escolhia
    sempre a segunda leitura e multiplicava, transformando 80 em 800. Péssima
    virava excelente, com log dizendo "normalizado".

    O erro só aparecia nos casos ruins, que são justamente os que a análise
    precisa acertar para desaconselhar uma migração.

    A correção não é escolher melhor: é **parar de escolher**. A escala passa a
    vir de `MCP_VIABILITY_SCALE`, uma declaração de quem escreve o prompt. O
    default preserva o comportamento de hoje (`0-100`), então nada muda para
    quem já roda — o que muda é que a conversão virou determinística e o valor
    de entrada deixou de influenciar a regra.

    Um score fora da escala declarada não é convertido: se o prompt pede 0..100
    e o modelo devolve 150, quem está errado é o modelo, e multiplicar por 10
    produziria 1500 — acima do teto da API. `model_copy` **não revalida**, então
    esse valor entraria no payload sem passar pelo `le=1000` do schema.
    """
    escala = get_settings().mcp_viability_scale
    score = result.viability_score

    if escala == "0-1000":
        return result

    if not 0 <= score <= _MCP_VIABILITY_MAX:
        logger.error(
            "mcp_viability_fora_da_escala escala=%s score=%d (valor preservado, sem conversão)",
            escala,
            score,
        )
        return result

    normalized_score = min(
        score * _MCP_TO_API_SCALE_FACTOR,
        _ESCALA_API_MAX,
    )

    logger.info(
        "mcp_viability_score_normalized escala=%s source=%d api=%d",
        escala,
        score,
        normalized_score,
    )

    return result.model_copy(
        update={
            "viability_score": normalized_score,
        }
    )


def log_llm_input_payload(
    flow: str,
    billing_data: dict,
) -> None:
    source_provider = billing_data.get(
        "provedor_origem",
        billing_data.get(
            "source_provider",
            "unknown",
        ),
    )

    billing_period = billing_data.get(
        "periodo_faturamento",
        billing_data.get(
            "billing_period",
            "unknown",
        ),
    )

    total_spend = billing_data.get(
        "gasto_total",
        billing_data.get(
            "total_spend",
            0,
        ),
    )

    currency = billing_data.get(
        "moeda",
        billing_data.get(
            "currency",
            "unknown",
        ),
    )

    payload = json.dumps(
        billing_data,
        default=str,
        indent=2,
        ensure_ascii=False,
    )

    logger.warning(
        "llm_input_payload flow=%s source=%s "
        "period=%s total=%s %s bytes=%d\n"
        "--- BEGIN LLM INPUT ---\n%s\n"
        "--- END LLM INPUT ---",
        flow,
        source_provider,
        billing_period,
        total_spend,
        currency,
        len(payload.encode("utf-8")),
        payload,
    )
