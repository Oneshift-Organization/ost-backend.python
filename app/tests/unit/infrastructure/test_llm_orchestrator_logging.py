import json
import logging
from typing import Any

import pytest
from pydantic import BaseModel

from app.infrastructure.llm import orchestrator as orchestrator_module
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
        self.calls = 0

    async def generate(
        self,
        prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        self.calls += 1
        self.last_prompt = prompt

        return AnalysisV2Response(
            viability_score=self.viability_score,
        )


class FailingLLMClient:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def generate(
        self,
        prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        self.calls += 1
        raise self.error


class FakeAnthropicStatusError(Exception):
    def __init__(
        self,
        status_code: int,
        body: object | None = None,
    ) -> None:
        super().__init__(
            f"Anthropic respondeu com HTTP {status_code}"
        )
        self.status_code = status_code
        self.body = body


class FakeAnthropicConnectionError(Exception):
    pass


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


@pytest.mark.asyncio
async def test_primary_success_does_not_call_fallback_or_mcp() -> None:
    primary_client = RecordingLLMClient(
        viability_score=780,
    )
    fallback_client = RecordingLLMClient(
        viability_score=75,
    )
    fallback_mcp_client: Any = DummyMCPClient()

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
        fallback_mcp_client=fallback_mcp_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "aws",
            "total_spend": 1000,
        }
    )

    assert result.viability_score == 780
    assert primary_client.calls == 1
    assert fallback_client.calls == 0
    assert fallback_mcp_client.calls == 0


@pytest.mark.parametrize(
    "status_code",
    [402, 429, 500, 502, 503, 504, 529],
)
@pytest.mark.asyncio
async def test_anthropic_retryable_status_uses_nvidia_fallback(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module.anthropic,
        "APIStatusError",
        FakeAnthropicStatusError,
    )
    primary_error = FakeAnthropicStatusError(
        status_code,
    )
    primary_client = FailingLLMClient(
        primary_error,
    )
    fallback_client = RecordingLLMClient(
        viability_score=75,
    )
    fallback_mcp_client: Any = DummyMCPClient()

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
        fallback_mcp_client=fallback_mcp_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "aws",
            "total_spend": 1000,
        }
    )

    assert primary_client.calls == 1
    assert fallback_client.calls == 1
    assert fallback_mcp_client.calls == 1
    assert result.viability_score == 750


@pytest.mark.asyncio
async def test_anthropic_connection_error_uses_nvidia_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module.anthropic,
        "APIConnectionError",
        FakeAnthropicConnectionError,
    )
    primary_client = FailingLLMClient(
        FakeAnthropicConnectionError(
            "Connection error"
        )
    )
    fallback_client = RecordingLLMClient(
        viability_score=72,
    )

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "aws",
        }
    )

    assert primary_client.calls == 1
    assert fallback_client.calls == 1
    assert result.viability_score == 72


@pytest.mark.asyncio
async def test_anthropic_spend_limit_uses_nvidia_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module.anthropic,
        "APIStatusError",
        FakeAnthropicStatusError,
    )
    primary_client = FailingLLMClient(
        FakeAnthropicStatusError(
            400,
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "Specified spend limit reached",
                    "details": {
                        "error_code": (
                            "enforced_spend_limit_reached"
                        )
                    },
                }
            },
        )
    )
    fallback_client = RecordingLLMClient(
        viability_score=74,
    )

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "gcp",
        }
    )

    assert primary_client.calls == 1
    assert fallback_client.calls == 1
    assert result.viability_score == 74


@pytest.mark.asyncio
async def test_malformed_claude_output_uses_nvidia_fallback() -> None:
    primary_client = FailingLLMClient(
        ValueError(
            "Could not extract JSON from Claude response"
        )
    )
    fallback_client = RecordingLLMClient(
        viability_score=71,
    )

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
    )

    result = await orchestrator.generate_from_legacy(
        {
            "source_provider": "azure",
        }
    )

    assert primary_client.calls == 1
    assert fallback_client.calls == 1
    assert result.viability_score == 71


@pytest.mark.parametrize(
    "status_code",
    [400, 401, 403, 404],
)
@pytest.mark.asyncio
async def test_non_retryable_anthropic_status_does_not_use_fallback(
    status_code: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator_module.anthropic,
        "APIStatusError",
        FakeAnthropicStatusError,
    )
    primary_error = FakeAnthropicStatusError(
        status_code,
        {
            "error": {
                "type": "invalid_request_error",
                "message": "Invalid request",
            }
        },
    )
    primary_client = FailingLLMClient(
        primary_error,
    )
    fallback_client = RecordingLLMClient(
        viability_score=70,
    )

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
    )

    with pytest.raises(FakeAnthropicStatusError):
        await orchestrator.generate_from_legacy(
            {
                "source_provider": "aws",
            }
        )

    assert primary_client.calls == 1
    assert fallback_client.calls == 0


@pytest.mark.asyncio
async def test_unexpected_primary_error_does_not_use_fallback() -> None:
    primary_client = FailingLLMClient(
        RuntimeError("Erro interno da aplicação")
    )
    fallback_client = RecordingLLMClient(
        viability_score=70,
    )

    orchestrator = LLMOrchestrator(
        llm_client=primary_client,
        fallback_client=fallback_client,
    )

    with pytest.raises(
        RuntimeError,
        match="Erro interno da aplicação",
    ):
        await orchestrator.generate_from_legacy(
            {
                "source_provider": "aws",
            }
        )

    assert primary_client.calls == 1
    assert fallback_client.calls == 0