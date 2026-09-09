import json
import logging
from typing import Any

import pytest
from pydantic import BaseModel

from app.infrastructure.llm.orchestrator import (
    LLMOrchestrator,
    log_llm_input_payload,
)
from app.infrastructure.mcp.prompts_client import (
    MCPPrompt,
    MCPPromptBundle,
)
from app.schemas.analysis_v2 import AnalysisV2Response


class RecordingLLMClient:
    def __init__(
        self,
        viability_score: int = 0,
    ) -> None:
        self.viability_score = viability_score
        self.last_prompt = ""

    async def generate(
        self,
        prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        self.last_prompt = prompt

        return AnalysisV2Response(
            viability_score=self.viability_score,
        )


class DummyMCPClient:
    def __init__(self) -> None:
        self.calls = 0

    async def get_all_prompts(
        self,
    ) -> MCPPromptBundle:
        self.calls += 1

        agents = [
            "00_SH1FT_OPERATOR_MASTER",
            "01_SH1FT_ECONOMIST",
            "02_SH1FT_ARCHITECT",
            "03_SH1FT_STRATEGIST",
        ]

        return MCPPromptBundle(
            prompt_contract_version=(
                "oneshift-prompts-v1"
            ),
            prompts=[
                MCPPrompt(
                    agent=agent,
                    prompt_version="v1",
                    content=f"Política do agente {agent}",
                )
                for agent in agents
            ],
        )


@pytest.mark.asyncio
async def test_generate_from_legacy_logs_payload_sent_to_llm(
    caplog,
) -> None:
    llm_client = RecordingLLMClient()
    orchestrator = LLMOrchestrator(llm_client)

    legacy_payload = {
        "source_provider": "gcp",
        "billing_period": "2026-05",
        "total_spend": 123.45,
        "workloads": [
            {
                "service_name": "Compute Engine",
            }
        ],
    }

    with caplog.at_level(
        logging.WARNING,
        logger="app.infrastructure.llm.orchestrator",
    ):
        await orchestrator.generate_from_legacy(
            legacy_payload
        )

    assert "llm_input_payload flow=legacy" in caplog.text
    assert '"service_name": "Compute Engine"' in caplog.text


def test_log_llm_input_payload_supports_preview_flow(
    caplog,
) -> None:
    payload = {
        "source_provider": "gcp",
        "billing_period": "2026-05",
        "total_spend": 123.45,
        "compute_pools": [
            {
                "project_id": "proj-001",
            }
        ],
    }

    with caplog.at_level(
        logging.WARNING,
        logger="app.infrastructure.llm.orchestrator",
    ):
        log_llm_input_payload(
            "preview",
            payload,
        )

    assert "llm_input_payload flow=preview" in caplog.text
    assert '"project_id": "proj-001"' in caplog.text


@pytest.mark.asyncio
async def test_without_mcp_preserves_legacy_prompt() -> None:
    llm_client = RecordingLLMClient(
        viability_score=85,
    )

    orchestrator = LLMOrchestrator(llm_client)

    billing_data = {
        "source_provider": "aws",
        "total_spend": 1000,
    }

    result = await orchestrator.generate_from_legacy(
        billing_data
    )

    prompt = json.loads(llm_client.last_prompt)

    assert set(prompt) == {
        "billing_data",
        "output_schema",
    }

    assert prompt["billing_data"] == billing_data
    assert "mcp_prompts" not in prompt

    # Sem MCP, não pode alterar a escala existente.
    assert result.viability_score == 85


@pytest.mark.asyncio
async def test_with_mcp_adds_all_prompts() -> None:
    llm_client = RecordingLLMClient(
        viability_score=85,
    )

    mcp_client: Any = DummyMCPClient()

    orchestrator = LLMOrchestrator(
        llm_client,
        mcp_client=mcp_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "aws",
            "total_spend": 1000,
        }
    )

    prompt = json.loads(llm_client.last_prompt)

    assert mcp_client.calls == 1

    assert (
        prompt["mcp_prompt_contract_version"]
        == "oneshift-prompts-v1"
    )

    assert len(prompt["mcp_prompts"]) == 4

    assert [
        item["agent"]
        for item in prompt["mcp_prompts"]
    ] == [
        "00_SH1FT_OPERATOR_MASTER",
        "01_SH1FT_ECONOMIST",
        "02_SH1FT_ARCHITECT",
        "03_SH1FT_STRATEGIST",
    ]

    # MCP produz 0..100; a API atual entrega 0..1000.
    assert result.viability_score == 850


@pytest.mark.asyncio
async def test_mcp_does_not_multiply_existing_scale_twice() -> None:
    llm_client = RecordingLLMClient(
        viability_score=850,
    )

    mcp_client: Any = DummyMCPClient()

    orchestrator = LLMOrchestrator(
        llm_client,
        mcp_client=mcp_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "aws",
            "total_spend": 1000,
        }
    )

    assert result.viability_score == 850