"""Anthropic Claude LLM client for the migration analysis pipeline.

Uses the official `anthropic` async SDK. The system prompt (rules +
JSON schema) is sent with ``cache_control: ephemeral`` — it is identical
across every call for the same ``response_model``, so it hits the prompt
cache after the first warm request, cutting latency and input-token cost
by ~90 % on the cached portion.

The user message (billing data) changes per request and is NOT cached.

See ``app/infrastructure/llm/orchestrator.py`` for prompt construction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, cast

import anthropic

from app.core.config import get_settings

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Full análise estruturada (viability + oneshift_scores + provider_comparison x N
# + cross_cloud_equivalents + 3 agent_views detalhados + action_plan + flags)
# passa de 8192 tokens em cenários grandes, cortando o JSON no meio e quebrando o
# parser. 16384 dá folga confortável para as análises estruturadas.
_MAX_OUTPUT_TOKENS = 16384


class ClaudeLLMClient:
    """Anthropic Claude client with prompt caching.

    Splits the orchestrator's single prompt into:
    - ``system``: static rules + JSON schema → cached (ephemeral)
    - ``user``: billing data payload → not cached (changes per call)
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.anthropic_api_key
        self._model = model or settings.anthropic_model
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def generate(
        self,
        prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        if not self._api_key:
            raise ValueError(
                "Anthropic API key is missing (ANTHROPIC_API_KEY)",
            )

        system_block, user_block = _split_prompt(prompt)

        create_kwargs: dict = {
            "model": self._model,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": user_block,
                }
            ],
        }

        if system_block:
            create_kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_block,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        message = await self._client.messages.create(**create_kwargs)

        logger.info(
            "claude_usage model=%s input=%d output=%d "
            "cache_read=%d cache_write=%d stop_reason=%s",
            self._model,
            message.usage.input_tokens,
            message.usage.output_tokens,
            getattr(
                message.usage,
                "cache_read_input_tokens",
                0,
            ),
            getattr(
                message.usage,
                "cache_creation_input_tokens",
                0,
            ),
            message.stop_reason,
        )

        text = _extract_text(message)
        raw = _extract_json(text)

        return response_model(**raw)


def _split_prompt(prompt: str) -> tuple[str, str]:
    """Split the orchestrator prompt into system and user sections."""

    split_marker = "Dados do Cliente (JSON):"
    idx = prompt.find(split_marker)

    if idx == -1:
        return "", prompt

    system_part = prompt[:idx].strip()
    user_part = prompt[idx:].strip()

    return system_part, user_part


def _extract_text(message: anthropic.types.Message) -> str:
    """Extract the first text block returned by Claude."""

    for block in message.content:
        if block.type == "text":
            return block.text

    msg = "Claude returned no text content block"
    raise ValueError(msg)


def _extract_json(text: str) -> dict[str, object]:
    """Extract a JSON object from Claude's response."""

    stripped = text.strip()

    if stripped.startswith("{"):
        return cast(
            "dict[str, object]",
            json.loads(stripped),
        )

    match = re.search(
        r"```(?:json)?\s*\n?({.*?})\s*\n?```",
        stripped,
        re.DOTALL,
    )

    if match:
        return cast(
            "dict[str, object]",
            json.loads(match.group(1)),
        )

    start = stripped.find("{")

    if start != -1:
        depth = 0

        for index, character in enumerate(
            stripped[start:],
            start,
        ):
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1

                if depth == 0:
                    return cast(
                        "dict[str, object]",
                        json.loads(stripped[start : index + 1]),
                    )

    logger.error(
        "claude_extract_failed len=%d\n"
        "--- BEGIN UNPARSEABLE TEXT ---\n"
        "%s\n"
        "--- END UNPARSEABLE TEXT ---",
        len(text),
        text,
    )

    msg = (
        "Could not extract JSON from Claude response:\n"
        f"{text[:500]}"
    )
    raise ValueError(msg)
