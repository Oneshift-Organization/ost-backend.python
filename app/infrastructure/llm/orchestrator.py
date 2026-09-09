"""Orquestrador da análise gerada por IA.

Sem um cliente MCP, preserva o comportamento anterior: envia somente os
dados de billing e o schema esperado.

Com um cliente MCP, busca os prompts dos agentes OneShift e os inclui como
políticas obrigatórias da análise.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.domain.interfaces.i_llm_client import ILLMClient
from app.schemas.analysis_v2 import AnalysisV2Response

if TYPE_CHECKING:
    from app.domain.analysis import MigrationContext
    from app.infrastructure.mcp.prompts_client import (
        MCPPromptBundle,
        OneShiftMCPClient,
    )

logger = logging.getLogger(__name__)
_MCP_VIABILITY_MAX = 100
_MCP_TO_API_SCALE_FACTOR = 10


class LLMOrchestrator:
    def __init__(
        self,
        llm_client: ILLMClient,
        mcp_client: OneShiftMCPClient | None = None,
    ) -> None:
        self._client = llm_client
        self._mcp_client = mcp_client

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

        log_llm_input_payload(
            "pipeline",
            context_payload,
        )

        return await self._generate(context_payload)

    async def generate_from_legacy(
        self,
        legacy_payload: dict,
    ) -> AnalysisV2Response:
        """Executa a análise usando o payload legado já calculado."""
        log_llm_input_payload(
            "legacy",
            legacy_payload,
        )

        return await self._generate(legacy_payload)

    async def _generate(
        self,
        billing_data: dict,
    ) -> AnalysisV2Response:
        mcp_bundle: MCPPromptBundle | None = None

        if self._mcp_client is not None:
            mcp_bundle = (
                await self._mcp_client.get_all_prompts()
            )

            logger.info(
                "mcp_policies_selected contract=%s prompts=%d",
                mcp_bundle.prompt_contract_version,
                len(mcp_bundle.prompts),
            )

        prompt = _build_prompt(
            billing_data,
            mcp_bundle,
        )

        raw = await self._client.generate(
            prompt,
            AnalysisV2Response,
        )

        if isinstance(raw, AnalysisV2Response):
            result = raw
        elif isinstance(raw, dict):
            result = AnalysisV2Response.resilient_parse(raw)
        else:
            raise TypeError(
                "Expected AnalysisV2Response or dict, "
                f"got {type(raw).__name__}"
            )

        if mcp_bundle is not None:
            return _normalize_mcp_viability_score(result)

        return result


def _build_prompt(
    billing_data: dict,
    mcp_bundle: MCPPromptBundle | None = None,
) -> str:
    if mcp_bundle is None:
        # Compatibilidade: mantém exatamente o formato anterior.
        legacy_payload = {
            "billing_data": billing_data,
            "output_schema": (
                AnalysisV2Response.model_json_schema()
            ),
        }

        return json.dumps(
            legacy_payload,
            default=str,
        )

    payload = {
        "instructions": [
            (
                "Use todos os documentos de mcp_prompts como "
                "políticas obrigatórias da análise."
            ),
            (
                "Analise somente os valores existentes em "
                "billing_data."
            ),
            (
                "Não invente preços, custos, equivalências "
                "ou informações ausentes."
            ),
            (
                "Retorne exclusivamente um objeto JSON válido "
                "compatível com output_schema."
            ),
            (
                "Produza os textos humanos em português "
                "brasileiro."
            ),
            (
                "Siga a escala de viability_score definida "
                "pelos prompts do MCP."
            ),
        ],
        "mcp_prompt_contract_version": (
            mcp_bundle.prompt_contract_version
        ),
        "mcp_prompts": [
            prompt.model_dump()
            for prompt in mcp_bundle.prompts
        ],
        "billing_data": billing_data,
        "output_schema": (
            AnalysisV2Response.model_json_schema()
        ),
    }

    return json.dumps(
        payload,
        default=str,
        ensure_ascii=False,
    )


def _normalize_mcp_viability_score(
    result: AnalysisV2Response,
) -> AnalysisV2Response:
    """Converte a escala MCP 0..100 para o contrato atual 0..1000."""
    score = result.viability_score

    if 0 <= score <= _MCP_VIABILITY_MAX:
        normalized_score = (
            score * _MCP_TO_API_SCALE_FACTOR
        )

        logger.info(
            "mcp_viability_score_normalized "
            "source=%d api=%d",
            score,
            normalized_score,
        )

        return result.model_copy(
            update={
                "viability_score": normalized_score,
            }
        )

    return result


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