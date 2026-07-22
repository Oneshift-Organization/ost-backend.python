from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import TYPE_CHECKING, cast

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_MAX_RETRIES = 2
_BASE_DELAY = 10.0

_FALLBACK_MODELS = [
    "nvidia/nemotron-3-super-120b-a12b:free",   # 262K output, 1M context
    "arcee-ai/trinity-large-thinking:free",      #  80K output, 262K context
    "deepseek/deepseek-v4-flash:free",           # 384K output, 1M context
    "openai/gpt-oss-120b:free",                  # 131K output, 131K context
    "z-ai/glm-4.5-air:free",                     #  96K output, 131K context
]


class OpenRouterLLMClient:
    """LLM client that calls OpenRouter Chat Completions API via raw HTTP.

    Falls back through high-capacity free models on repeated failures.
    Retries with exponential backoff + jitter on 429 rate-limits.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.openrouter_api_key
        self._primary_model = model or settings.openrouter_model
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://oneshift.com.br",
            "X-OpenRouter-Title": "OneShift Cloud Intelligence",
        }

    async def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        if not self._api_key:
            raise ValueError("OpenRouter API key is missing")

        models_to_try = [self._primary_model] + [m for m in _FALLBACK_MODELS if m != self._primary_model]
        last_exc: Exception | None = None

        for model in models_to_try:
            last_exc = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    result = await self._call_model(model, prompt, response_model)
                    return result
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code == 429 and attempt < _MAX_RETRIES:
                        delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 3)
                        logger.warning(
                            "openrouter_rate_limit model=%s attempt=%d retry_after=%.1f",
                            model, attempt + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("openrouter_non_retryable model=%s status=%d", model, exc.response.status_code)
                except Exception as exc:
                    last_exc = exc
                    logger.warning("openrouter_fallback model=%s error=%s", model, exc)
                    break

        raise cast("Exception", last_exc)

    async def _call_model(
        self,
        model: str,
        prompt: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 16384,
        }

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=self._headers)
            resp.raise_for_status()
            data = resp.json()

        text = self._extract_text(data)
        raw = self._extract_json(text)
        return response_model(**raw)

    def _extract_text(self, data: dict[str, object]) -> str:
        choices = cast("list[dict[str, object]]", data.get("choices", []))
        if not choices:
            msg = "OpenRouter returned no choices"
            raise ValueError(msg)
        message = cast("dict[str, object]", choices[0].get("message", {}))
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            msg = "OpenRouter returned empty message content"
            raise ValueError(msg)
        return content

    def _extract_json(self, text: str) -> dict[str, object]:
        stripped = text.strip()
        if stripped.startswith("{"):
            return cast("dict[str, object]", json.loads(stripped))
        match = re.search(r"```(?:json)?\s*\n?({.*?})\s*\n?```", stripped, re.DOTALL)
        if match:
            return cast("dict[str, object]", json.loads(match.group(1)))
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
        msg = f"Could not extract JSON from OpenRouter response:\n{text[:500]}"
        raise ValueError(msg)
