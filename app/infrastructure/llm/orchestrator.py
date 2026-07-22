"""LLM orchestrator — production system prompt + data-only user message.

The system message carries the Operator's rules, grounding constraints and
tone guidance (``system_prompt.SYSTEM_PROMPT``, static, cacheable). The user
message is pure JSON with the billing data — no wrapper, no embedded schema;
the expected output shape is enforced via structured output on the LLM
client side (Task 3, Agno).

See ``docs/refactor/analysis-v2-spec.md`` §4.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.domain.interfaces.i_llm_client import ILLMClient
from app.infrastructure.llm.system_prompt import SYSTEM_PROMPT
from app.schemas.analysis_v2 import AnalysisV2Response

if TYPE_CHECKING:
    from pydantic import BaseModel

    from app.domain.analysis import MigrationContext

logger = logging.getLogger(__name__)


class LLMOrchestrator:
    def __init__(
        self,
        llm_client: ILLMClient,
        timeout_seconds: float | None = None,
        retry_delay_seconds: float = 3.0,
    ) -> None:
        settings = get_settings()
        self._client = llm_client
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.llm_timeout_seconds
        )
        self._max_retries = settings.llm_max_retries
        self._retry_delay = retry_delay_seconds

    async def _call_llm(self, user_message: str) -> AnalysisV2Response:
        async with asyncio.timeout(self._timeout):  # orçamento GLOBAL (inclui retry)
            for attempt in range(self._max_retries + 1):
                try:
                    raw = await self._client.generate(
                        SYSTEM_PROMPT, user_message, AnalysisV2Response
                    )
                    result = _normalize(raw)
                    # Resposta "válida" porém semanticamente vazia (JSON aninhado
                    # numa chave fora da tabela de aliases valida com defaults —
                    # A4/A5): gasta o retry nela em vez de aceitar silencioso.
                    if _is_empty(result):
                        # Diagnóstico: quais chaves o modelo realmente devolveu
                        # (aponta lacunas na tabela de aliases do resilient_parse)
                        keys = sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
                        logger.warning("llm_empty_response raw_keys=%s", keys)
                        msg = "resposta do LLM semanticamente vazia após validação"
                        raise ValueError(msg)
                    return result
                except (TimeoutError, asyncio.CancelledError):
                    raise
                except Exception:
                    if attempt >= self._max_retries:
                        raise
                    logger.warning(
                        "llm_attempt_failed attempt=%d, retrying", attempt + 1, exc_info=True
                    )
                    await asyncio.sleep(self._retry_delay)
        # Every branch of the loop above either returns or raises; this is
        # unreachable at runtime and exists only to satisfy static return
        # completeness checks (mypy).
        raise RuntimeError("unreachable")

    async def generate_report(
        self,
        context: MigrationContext,
        source_provider: str,
        billing_period: str,
        total_spend: float,
        currency: str,
        pools_json: str,
    ) -> AnalysisV2Response:
        context_payload = json.loads(pools_json)
        context_payload["provedor_origem"] = source_provider
        context_payload["periodo_faturamento"] = billing_period
        context_payload["gasto_total"] = total_spend
        context_payload["moeda"] = currency

        log_llm_input_payload("pipeline", context_payload)

        user_message = json.dumps(context_payload, default=str)
        return await self._call_llm(user_message)

    async def generate_from_legacy(
        self,
        legacy_payload: dict,
    ) -> AnalysisV2Response:
        """Run analysis on a pre-computed legacy workload dict."""
        log_llm_input_payload("legacy", legacy_payload)

        user_message = json.dumps(legacy_payload, default=str)
        return await self._call_llm(user_message)


def _normalize(raw: BaseModel | dict[str, Any]) -> AnalysisV2Response:
    if isinstance(raw, AnalysisV2Response):
        return raw
    if isinstance(raw, dict):
        return AnalysisV2Response.resilient_parse(raw)
    raise TypeError(f"Expected AnalysisV2Response or dict, got {type(raw).__name__}")


def _is_empty(resp: AnalysisV2Response) -> bool:
    """Resposta validada mas sem conteúdo útil (tudo em defaults)."""
    return (
        not resp.recommendation
        and not resp.provider_comparison
        and resp.viability_score == 0
    )


def log_llm_input_payload(flow: str, billing_data: dict) -> None:
    source_provider = billing_data.get("provedor_origem", billing_data.get("source_provider", "unknown"))
    billing_period = billing_data.get("periodo_faturamento", billing_data.get("billing_period", "unknown"))
    total_spend = billing_data.get("gasto_total", billing_data.get("total_spend", 0))
    currency = billing_data.get("moeda", billing_data.get("currency", "unknown"))
    payload = json.dumps(billing_data, default=str, indent=2, ensure_ascii=False)

    logger.warning(
        "llm_input_payload flow=%s source=%s period=%s total=%s %s bytes=%d\n"
        "--- BEGIN LLM INPUT ---\n%s\n--- END LLM INPUT ---",
        flow,
        source_provider,
        billing_period,
        total_spend,
        currency,
        len(payload.encode("utf-8")),
        payload,
    )
