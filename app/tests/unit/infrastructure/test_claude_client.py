from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from app.infrastructure.llm.claude_client import ClaudeLLMClient


class ClaudeResponse(BaseModel):
    status: str


class FakeMessages:
    def __init__(self) -> None:
        self.last_parameters: dict[str, Any] = {}

    async def create(
        self,
        **parameters: Any,
    ) -> Any:
        self.last_parameters = parameters

        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text='{"status":"CLAUDE_OK"}',
                )
            ],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=100,
            ),
            stop_reason="end_turn",
        )


async def test_generate_does_not_send_temperature() -> None:
    fake_messages = FakeMessages()

    client = ClaudeLLMClient(
        api_key="sk-ant-test",
        model="claude-sonnet-4-6",
    )

    cast(Any, client)._client = SimpleNamespace(
        messages=fake_messages,
    )

    prompt = """
Instruções da análise.

Dados do Cliente (JSON):
{"source_provider":"aws"}

Retorne apenas JSON.
""".strip()

    result = await client.generate(
        prompt,
        ClaudeResponse,
    )

    assert result.status == "CLAUDE_OK"

    parameters = fake_messages.last_parameters

    assert parameters["model"] == "claude-sonnet-4-6"
    assert parameters["max_tokens"] == 16384
    assert "temperature" not in parameters

    assert parameters["messages"] == [
        {
            "role": "user",
            "content": (
                'Dados do Cliente (JSON):\n{"source_provider":"aws"}\n\nRetorne apenas JSON.'
            ),
        }
    ]

    assert parameters["system"] == [
        {
            "type": "text",
            "text": "Instruções da análise.",
            "cache_control": {
                "type": "ephemeral",
            },
        }
    ]


async def test_resposta_cortada_pelo_limite_diz_que_foi_cortada() -> None:
    class Cortada(FakeMessages):
        async def create(self, **parameters: Any) -> Any:
            resposta = await super().create(**parameters)
            resposta.stop_reason = "max_tokens"
            resposta.content[0].text = '{"status": "CLAUDE'
            return resposta

    client = ClaudeLLMClient(api_key="sk-ant-test", model="claude-sonnet-4-6")
    cast(Any, client)._client = SimpleNamespace(messages=Cortada())
    with pytest.raises(ValueError, match="truncated by the token limit"):
        await client.generate("Dados do Cliente (JSON):\n{}", ClaudeResponse)
