from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from app.infrastructure.llm.nvidia_client import (
    NvidiaLLMClient,
    _extract_json,
)


class NvidiaResponse(BaseModel):
    status: str


class FakeCompletions:
    def __init__(self, completion: Any) -> None:
        self.completion = completion
        self.last_parameters: dict[str, Any] = {}

    async def create(self, **parameters: Any) -> Any:
        self.last_parameters = parameters
        return self.completion


def make_completion(
    content: str = '{"status":"NVIDIA_OK"}',
    finish_reason: str = "stop",
) -> Any:
    choice = SimpleNamespace(
        finish_reason=finish_reason,
        message=SimpleNamespace(content=content),
    )

    return SimpleNamespace(
        choices=[choice],
        usage=None,
    )


async def test_generate_returns_validated_response() -> None:
    fake_completions = FakeCompletions(make_completion())

    client = NvidiaLLMClient(
        api_key="nvapi-test",
        model="nvidia/test-model",
    )

    cast(Any, client)._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=fake_completions,
        )
    )

    result = await client.generate(
        "Retorne um JSON",
        NvidiaResponse,
    )

    assert result.status == "NVIDIA_OK"
    assert fake_completions.last_parameters["model"] == "nvidia/test-model"
    assert fake_completions.last_parameters["temperature"] == 0
    assert fake_completions.last_parameters["response_format"] == {
        "type": "json_object"
    }


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"status":"JSON_DIRETO"}', "JSON_DIRETO"),
        ('```json\n{"status":"JSON_BLOCO"}\n```', "JSON_BLOCO"),
        ('Resposta: {"status":"JSON_TEXTO"} fim.', "JSON_TEXTO"),
    ],
)
def test_extract_json(
    content: str,
    expected: str,
) -> None:
    result = _extract_json(content)

    assert result["status"] == expected


async def test_generate_rejects_truncated_response() -> None:
    client = NvidiaLLMClient(api_key="nvapi-test")

    fake_completions = FakeCompletions(
        make_completion(finish_reason="length")
    )

    cast(Any, client)._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=fake_completions,
        )
    )

    with pytest.raises(
        ValueError,
        match="truncated",
    ):
        await client.generate(
            "Retorne um JSON",
            NvidiaResponse,
        )


async def test_generate_rejects_empty_response() -> None:
    client = NvidiaLLMClient(api_key="nvapi-test")

    fake_completions = FakeCompletions(
        make_completion(content=""),
    )

    cast(Any, client)._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=fake_completions,
        )
    )

    with pytest.raises(
        ValueError,
        match="empty",
    ):
        await client.generate(
            "Retorne um JSON",
            NvidiaResponse,
        )


async def test_generate_rejects_response_without_choices() -> None:
    client = NvidiaLLMClient(api_key="nvapi-test")

    completion = SimpleNamespace(
        choices=[],
        usage=None,
    )

    fake_completions = FakeCompletions(completion)

    cast(Any, client)._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=fake_completions,
        )
    )

    with pytest.raises(
        ValueError,
        match="no choices",
    ):
        await client.generate(
            "Retorne um JSON",
            NvidiaResponse,
        )