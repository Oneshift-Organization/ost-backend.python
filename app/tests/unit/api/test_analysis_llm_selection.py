from types import SimpleNamespace
from typing import Any

import pytest

from app.api.v1.routes import analysis


@pytest.mark.parametrize(
    ("provider", "mcp_enabled", "expected_mcp"),
    [
        ("nvidia", True, True),
        ("nvidia", False, False),
        ("anthropic", True, False),
    ],
)
def test_make_orchestrator_controls_mcp_by_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    mcp_enabled: bool,
    expected_mcp: bool,
) -> None:
    fake_llm_client = object()
    fake_mcp_client = object()
    mcp_factory_calls = 0

    def fake_get_mcp_prompt_client() -> object:
        nonlocal mcp_factory_calls
        mcp_factory_calls += 1
        return fake_mcp_client

    settings = SimpleNamespace(
        llm_provider=provider,
        mcp_prompts_enabled=mcp_enabled,
    )

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "_make_llm_client",
        lambda: fake_llm_client,
    )
    monkeypatch.setattr(
        analysis,
        "get_mcp_prompt_client",
        fake_get_mcp_prompt_client,
    )

    orchestrator = analysis._make_orchestrator()

    assert orchestrator._client is fake_llm_client

    if expected_mcp:
        assert orchestrator._mcp_client is fake_mcp_client
        assert mcp_factory_calls == 1
    else:
        assert orchestrator._mcp_client is None
        assert mcp_factory_calls == 0


def test_make_llm_client_selects_nvidia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        llm_provider="nvidia",
        nvidia_api_key="nvapi-test",
        nvidia_model="nvidia/test-model",
        nvidia_base_url="https://example.invalid/v1",
        nvidia_timeout_seconds=45.0,
    )
    received: dict[str, Any] = {}
    fake_client = object()

    def fake_nvidia_client(**kwargs: Any) -> object:
        received.update(kwargs)
        return fake_client

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "NvidiaLLMClient",
        fake_nvidia_client,
    )

    result = analysis._make_llm_client()

    assert result is fake_client
    assert received == {
        "api_key": "nvapi-test",
        "model": "nvidia/test-model",
        "base_url": "https://example.invalid/v1",
        "timeout_seconds": 45.0,
    }