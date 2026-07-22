from unittest.mock import AsyncMock, patch

import pytest
from agno.models.anthropic import Claude
from app.infrastructure.llm.agno_client import AgnoLLMClient
from app.schemas.analysis_v2 import AnalysisV2Response


@pytest.mark.asyncio
async def test_generate_returns_typed_response():
    client = AgnoLLMClient(provider="anthropic", api_key="test", model_id="claude-sonnet-4-6")
    fake_run = AsyncMock()
    fake_run.content = AnalysisV2Response(viability_score=72)
    with patch("app.infrastructure.llm.agno_client.Agent") as agent_cls:
        agent_cls.return_value.arun = AsyncMock(return_value=fake_run)
        result = await client.generate("system", "{}", AnalysisV2Response)
    assert isinstance(result, AnalysisV2Response)
    assert result.viability_score == 72
    # structured output + system prompt passados ao Agent
    kwargs = agent_cls.call_args.kwargs
    assert kwargs["output_schema"] is AnalysisV2Response
    assert kwargs["system_message"] == "system"


def test_claude_model_configured_with_cache_and_temp():
    client = AgnoLLMClient(provider="anthropic", api_key="k", model_id="claude-sonnet-4-6")
    m = client._model
    assert isinstance(m, Claude)
    assert m.cache_system_prompt is True
    assert m.temperature == 0.2
