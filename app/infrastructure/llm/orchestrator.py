"""LLM orchestrator — V2 data-only prompt.

The prompt contains only the billing data and the expected output schema.
No instructions, no rules, no tone guidance. The model reasons from the
data and returns a JSON that matches ``AnalysisV2Response``.

See ``docs/refactor/analysis-v2-spec.md`` §4.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.domain.interfaces.i_llm_client import ILLMClient
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
        context_payload["source_provider"] = source_provider
        context_payload["billing_period"] = billing_period
        context_payload["total_spend"] = total_spend
        context_payload["currency"] = currency

        log_llm_input_payload("pipeline", context_payload)

        prompt = _build_prompt(context_payload)
        raw = await self._client.generate(prompt, AnalysisV2Response)

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

        prompt = _build_prompt(legacy_payload)
        raw = await self._client.generate(prompt, AnalysisV2Response)

        if isinstance(raw, AnalysisV2Response):
            return raw
        if isinstance(raw, dict):
            return AnalysisV2Response.resilient_parse(raw)
        raise TypeError(f"Expected AnalysisV2Response or dict, got {type(raw).__name__}")


def _build_prompt(billing_data: dict) -> str:
    """Data-only prompt: billing data + expected output schema. No instructions."""
    payload = {
        "billing_data": billing_data,
        "output_schema": AnalysisV2Response.model_json_schema(),
    }
    return json.dumps(payload, default=str)


def log_llm_input_payload(flow: str, billing_data: dict) -> None:
    source_provider = billing_data.get("source_provider", "unknown")
    billing_period = billing_data.get("billing_period", "unknown")
    total_spend = billing_data.get("total_spend", 0)
    currency = billing_data.get("currency", "unknown")
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
