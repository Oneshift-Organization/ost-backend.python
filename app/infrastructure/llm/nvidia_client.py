from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, cast

from openai import AsyncOpenAI

from app.core.config import get_settings

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

_MAX_OUTPUT_TOKENS = 16384


class NvidiaLLMClient:
    """Cliente para os modelos NVIDIA NIM hospedados."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()

        self._api_key = api_key or settings.nvidia_api_key
        self._model = model or settings.nvidia_model
        self._base_url = base_url or settings.nvidia_base_url
        self._timeout_seconds = timeout_seconds or settings.nvidia_timeout_seconds

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout_seconds,
            max_retries=2,
        )

    async def generate(
        self,
        prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        if not self._api_key:
            raise ValueError("NVIDIA API key is missing (NVIDIA_API_KEY)")

        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            temperature=0,
            max_tokens=_MAX_OUTPUT_TOKENS,
            response_format={
                "type": "json_object",
            },
            extra_body={
                "chat_template_kwargs": {
                    "enable_thinking": False,
                }
            },
        )

        if not completion.choices:
            raise ValueError("NVIDIA returned no choices")

        choice = completion.choices[0]

        if choice.finish_reason == "length":
            raise ValueError("NVIDIA response was truncated by the token limit")

        content = choice.message.content

        if not content or not content.strip():
            raise ValueError("NVIDIA returned empty message content")

        if completion.usage:
            logger.info(
                ("nvidia_usage model=%s input=%d output=%d total=%d finish_reason=%s"),
                self._model,
                completion.usage.prompt_tokens,
                completion.usage.completion_tokens,
                completion.usage.total_tokens,
                choice.finish_reason,
            )

        raw = _extract_json(content)

        return response_model.model_validate(raw)


def _extract_json(text: str) -> dict[str, object]:
    stripped = text.strip()

    try:
        parsed = json.loads(stripped)

        if isinstance(parsed, dict):
            return cast("dict[str, object]", parsed)
    except json.JSONDecodeError:
        pass

    match = re.search(
        r"```(?:json)?\s*\n?({.*?})\s*\n?```",
        stripped,
        re.DOTALL,
    )

    if match:
        parsed = json.loads(match.group(1))

        if isinstance(parsed, dict):
            return cast("dict[str, object]", parsed)

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
                    parsed = json.loads(stripped[start : index + 1])

                    if isinstance(parsed, dict):
                        return cast(
                            "dict[str, object]",
                            parsed,
                        )

    logger.error(
        "nvidia_extract_failed length=%d response=%s",
        len(text),
        text[:1000],
    )

    raise ValueError("Could not extract JSON from NVIDIA response")
