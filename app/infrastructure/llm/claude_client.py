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

# Default model: Sonnet 4.6 — strong reasoning, fast, cost-efficient for FinOps
# analysis.  Swap to claude-opus-4-8 in prod settings for highest accuracy.
_DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_OUTPUT_TOKENS = 8192


class ClaudeLLMClient:
    """Anthropic Claude client with prompt caching.

    Splits the orchestrator's single prompt into:
    - ``system``: static rules + JSON schema → cached (ephemeral)
    - ``user``:   billing data payload → not cached (changes per call)

    This achieves ~90 % cache hit on the system block after the first
    warm request, matching the pattern recommended by the Anthropic SDK
    for repeated structured-output calls.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.anthropic_api_key
        self._model = model or settings.anthropic_model
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    async def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        if not self._api_key:
            raise ValueError("Anthropic API key is missing (ANTHROPIC_API_KEY)")

        system_block, user_block = _split_prompt(prompt)

        create_kwargs: dict = {
            "model": self._model,
            "max_tokens": _MAX_OUTPUT_TOKENS,
            "temperature": 1,
            "messages": [{"role": "user", "content": user_block}],
        }
        # Only set a cached system block when there is actual content to cache.
        # Anthropic rejects cache_control on empty text blocks.
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
            "claude_usage model=%s input=%d output=%d cache_read=%d cache_write=%d stop_reason=%s",
            self._model,
            message.usage.input_tokens,
            message.usage.output_tokens,
            getattr(message.usage, "cache_read_input_tokens", 0),
            getattr(message.usage, "cache_creation_input_tokens", 0),
            message.stop_reason,
        )

        text = _extract_text(message)

        raw = _extract_json(text)
        return response_model(**raw)


# ── helpers ──────────────────────────────────────────────────────────────────

def _split_prompt(prompt: str) -> tuple[str, str]:
    """Split the orchestrator's monolithic prompt into (system, user).

    The orchestrator builds one string:

        <rules and instructions>
        <JSON schema>

        Dados do Cliente (JSON):
        <billing data>

        Retorne APENAS JSON...

    Everything up to (but not including) "Dados do Cliente" goes into the
    cached system block.  The data + closing instruction go to the user
    message.
    """
    split_marker = "Dados do Cliente (JSON):"
    idx = prompt.find(split_marker)
    if idx == -1:
        # Fallback: put everything in user (no caching)
        return "", prompt

    system_part = prompt[:idx].strip()
    user_part = prompt[idx:].strip()
    return system_part, user_part


def _extract_text(message: anthropic.types.Message) -> str:
    for block in message.content:
        if block.type == "text":
            return block.text
    msg = "Claude returned no text content block"
    raise ValueError(msg)


def _extract_json(text: str) -> dict[str, object]:
    stripped = text.strip()

    # 1. Response is pure JSON
    if stripped.startswith("{"):
        return cast("dict[str, object]", json.loads(stripped))

    # 2. JSON inside a code fence (```json ... ``` or ``` ... ```)
    match = re.search(r"```(?:json)?\s*\n?({.*?})\s*\n?```", stripped, re.DOTALL)
    if match:
        return cast("dict[str, object]", json.loads(match.group(1)))

    # 3. First { ... } block anywhere in the text
    start = stripped.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(stripped[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return cast("dict[str, object]", json.loads(stripped[start : i + 1]))

    # DEBUG: texto COMPLETO (antes era text[:500], escondia onde o JSON quebrava)
    logger.error(
        "claude_extract_failed len=%d\n--- BEGIN UNPARSEABLE TEXT ---\n%s\n--- END UNPARSEABLE TEXT ---",
        len(text),
        text,
    )
    msg = f"Could not extract JSON from Claude response:\n{text[:500]}"
    raise ValueError(msg)
