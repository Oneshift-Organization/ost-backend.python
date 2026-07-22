import logging

import pytest

from app.infrastructure.llm.orchestrator import LLMOrchestrator
from app.infrastructure.llm.orchestrator import log_llm_input_payload
from app.schemas.analysis_v2 import AnalysisV2Response


class DummyLLMClient:
    async def generate(self, system: str, user: str, response_model):
        return AnalysisV2Response()


@pytest.mark.asyncio
async def test_generate_from_legacy_logs_payload_sent_to_llm(caplog):
    orchestrator = LLMOrchestrator(DummyLLMClient())
    legacy_payload = {
        "source_provider": "gcp",
        "billing_period": "2026-05",
        "total_spend": 123.45,
        "workloads": [{"service_name": "Compute Engine"}],
    }

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.llm.orchestrator"):
        await orchestrator.generate_from_legacy(legacy_payload)

    assert "llm_input_payload flow=legacy" in caplog.text
    assert '"service_name": "Compute Engine"' in caplog.text


def test_log_llm_input_payload_supports_preview_flow(caplog):
    payload = {
        "source_provider": "gcp",
        "billing_period": "2026-05",
        "total_spend": 123.45,
        "compute_pools": [{"project_id": "proj-001"}],
    }

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.llm.orchestrator"):
        log_llm_input_payload("preview", payload)

    assert "llm_input_payload flow=preview" in caplog.text
    assert '"project_id": "proj-001"' in caplog.text
