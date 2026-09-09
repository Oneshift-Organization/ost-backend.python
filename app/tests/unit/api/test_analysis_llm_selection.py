from types import SimpleNamespace
from typing import Any

import pytest

from app.api.v1.routes import analysis


def test_make_orchestrator_configures_claude_primary_and_nvidia_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_client = object()
    fallback_client = object()
    fallback_mcp_client = object()
    mcp_factory_calls = 0

    def fake_get_mcp_prompt_client() -> object:
        nonlocal mcp_factory_calls
        mcp_factory_calls += 1
        return fallback_mcp_client

    settings = SimpleNamespace(
        llm_provider="anthropic",
        llm_fallback_provider="nvidia",
        mcp_prompts_enabled=True,
    )

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "_make_llm_client",
        lambda: primary_client,
    )
    monkeypatch.setattr(
        analysis,
        "_make_nvidia_client",
        lambda: fallback_client,
    )
    monkeypatch.setattr(
        analysis,
        "get_mcp_prompt_client",
        fake_get_mcp_prompt_client,
    )

    orchestrator = analysis._make_orchestrator()

    assert orchestrator._client is primary_client
    assert orchestrator._mcp_client is None
    assert orchestrator._fallback_client is fallback_client
    assert orchestrator._fallback_mcp_client is fallback_mcp_client
    assert mcp_factory_calls == 1


def test_make_orchestrator_keeps_fallback_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_client = object()
    nvidia_factory_calls = 0
    mcp_factory_calls = 0

    def fake_make_nvidia_client() -> object:
        nonlocal nvidia_factory_calls
        nvidia_factory_calls += 1
        return object()

    def fake_get_mcp_prompt_client() -> object:
        nonlocal mcp_factory_calls
        mcp_factory_calls += 1
        return object()

    settings = SimpleNamespace(
        llm_provider="anthropic",
        llm_fallback_provider="none",
        mcp_prompts_enabled=True,
    )

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "_make_llm_client",
        lambda: primary_client,
    )
    monkeypatch.setattr(
        analysis,
        "_make_nvidia_client",
        fake_make_nvidia_client,
    )
    monkeypatch.setattr(
        analysis,
        "get_mcp_prompt_client",
        fake_get_mcp_prompt_client,
    )

    orchestrator = analysis._make_orchestrator()

    assert orchestrator._client is primary_client
    assert orchestrator._mcp_client is None
    assert orchestrator._fallback_client is None
    assert orchestrator._fallback_mcp_client is None
    assert nvidia_factory_calls == 0
    assert mcp_factory_calls == 0


def test_make_orchestrator_supports_nvidia_as_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_client = object()
    primary_mcp_client = object()
    mcp_factory_calls = 0

    def fake_get_mcp_prompt_client() -> object:
        nonlocal mcp_factory_calls
        mcp_factory_calls += 1
        return primary_mcp_client

    settings = SimpleNamespace(
        llm_provider="nvidia",
        llm_fallback_provider="none",
        mcp_prompts_enabled=True,
    )

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "_make_llm_client",
        lambda: primary_client,
    )
    monkeypatch.setattr(
        analysis,
        "get_mcp_prompt_client",
        fake_get_mcp_prompt_client,
    )

    orchestrator = analysis._make_orchestrator()

    assert orchestrator._client is primary_client
    assert orchestrator._mcp_client is primary_mcp_client
    assert orchestrator._fallback_client is None
    assert orchestrator._fallback_mcp_client is None
    assert mcp_factory_calls == 1


def test_make_orchestrator_does_not_load_mcp_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    primary_client = object()
    fallback_client = object()
    mcp_factory_calls = 0

    def fake_get_mcp_prompt_client() -> object:
        nonlocal mcp_factory_calls
        mcp_factory_calls += 1
        return object()

    settings = SimpleNamespace(
        llm_provider="anthropic",
        llm_fallback_provider="nvidia",
        mcp_prompts_enabled=False,
    )

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "_make_llm_client",
        lambda: primary_client,
    )
    monkeypatch.setattr(
        analysis,
        "_make_nvidia_client",
        lambda: fallback_client,
    )
    monkeypatch.setattr(
        analysis,
        "get_mcp_prompt_client",
        fake_get_mcp_prompt_client,
    )

    orchestrator = analysis._make_orchestrator()

    assert orchestrator._client is primary_client
    assert orchestrator._mcp_client is None
    assert orchestrator._fallback_client is fallback_client
    assert orchestrator._fallback_mcp_client is None
    assert mcp_factory_calls == 0


def test_make_llm_client_selects_nvidia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        llm_provider="nvidia",
    )
    fake_client = object()

    monkeypatch.setattr(
        analysis,
        "get_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        analysis,
        "_make_nvidia_client",
        lambda: fake_client,
    )

    assert analysis._make_llm_client() is fake_client


def test_make_nvidia_client_uses_configured_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
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

    result = analysis._make_nvidia_client()

    assert result is fake_client
    assert received == {
        "api_key": "nvapi-test",
        "model": "nvidia/test-model",
        "base_url": "https://example.invalid/v1",
        "timeout_seconds": 45.0,
    }