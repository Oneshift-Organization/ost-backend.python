import asyncio
from unittest.mock import AsyncMock

import pytest
from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.schemas.analysis_v2 import AnalysisV2Response


@pytest.mark.asyncio
async def test_retries_once_then_succeeds():
    client = AsyncMock()
    client.generate.side_effect = [RuntimeError("boom"), AnalysisV2Response(viability_score=50)]
    orch = LLMOrchestrator(llm_client=client, retry_delay_seconds=0)
    result = await orch.generate_from_legacy({"total_spend": 1})
    assert result.viability_score == 50
    assert client.generate.call_count == 2


@pytest.mark.asyncio
async def test_global_timeout_enforced(monkeypatch):
    async def slow(*a, **k):
        await asyncio.sleep(10)

    client = AsyncMock()
    client.generate = slow
    orch = LLMOrchestrator(llm_client=client, timeout_seconds=0.05, retry_delay_seconds=0)
    with pytest.raises(TimeoutError):
        await orch.generate_from_legacy({"total_spend": 1})
