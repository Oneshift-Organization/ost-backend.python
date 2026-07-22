"""LLM orchestrator — production system prompt + data-only user message.

The system message carries the Operator's rules, grounding constraints and
tone guidance (``system_prompt.SYSTEM_PROMPT``, static, cacheable). The user
message is pure JSON with the billing data — no wrapper, no embedded schema;
the expected output shape is enforced via structured output on the LLM
client side (Task 3, Agno).

See ``docs/refactor/analysis-v2-spec.md`` §4.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.domain.interfaces.i_llm_client import ILLMClient
from app.infrastructure.llm.system_prompt import SYSTEM_PROMPT
from app.schemas.analysis_v2 import AnalysisV2Response

if TYPE_CHECKING:
    from app.domain.analysis import MigrationContext

logger = logging.getLogger(__name__)


class LLMOrchestrator:
    def __init__(self, llm_client: ILLMClient) -> None:
        self._client = llm_client

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
        raw = await self._client.generate(SYSTEM_PROMPT, user_message, AnalysisV2Response)

        if isinstance(raw, AnalysisV2Response):
            return raw
        if isinstance(raw, dict):
            return AnalysisV2Response.resilient_parse(raw)
        raise TypeError(f"Expected AnalysisV2Response or dict, got {type(raw).__name__}")

    async def generate_from_legacy(
        self,
        legacy_payload: dict,
    ) -> AnalysisV2Response:
        """Run analysis on a pre-computed legacy workload dict."""
        log_llm_input_payload("legacy", legacy_payload)

        user_message = json.dumps(legacy_payload, default=str)
        raw = await self._client.generate(SYSTEM_PROMPT, user_message, AnalysisV2Response)

        if isinstance(raw, AnalysisV2Response):
            return raw
        if isinstance(raw, dict):
            return AnalysisV2Response.resilient_parse(raw)
        raise TypeError(f"Expected AnalysisV2Response or dict, got {type(raw).__name__}")


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
