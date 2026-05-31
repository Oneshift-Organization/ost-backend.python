from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import TYPE_CHECKING, Any, cast

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_MAX_RETRIES = 5
_BASE_DELAY = 15.0  # seconds


class GeminiLLMClient:
    """Real LLM client that calls the Gemini API.

    Sends the full prompt (system instructions + serialized context)
    as a single user message. Expects a valid JSON response matching
    the requested ``response_model``.

    Retries on 429 (rate limit) with exponential backoff + jitter.
    If the primary model fails repeatedly with 429, falls back to
    ``gemini-1.5-flash`` which has higher free-tier rate limits.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.gemini_api_key
        self._model = model or settings.gemini_model
        self._fallback_model = "gemini-1.5-flash"

    async def generate(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        models_to_try = [self._model, self._fallback_model] if self._model != self._fallback_model else [self._model]
        last_exc: Exception | None = None

        for model_name in models_to_try:
            last_exc = None
            url = f"{GEMINI_BASE_URL}/{model_name}:generateContent?key={self._api_key}"
            payload = self._build_payload(prompt)

            for attempt in range(_MAX_RETRIES + 1):
                try:
                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(url, json=payload)
                        resp.raise_for_status()
                        data = resp.json()

                    text = self._extract_text(data)
                    raw = self._extract_json(text)
                    return response_model(**raw)
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code == 429 and attempt < _MAX_RETRIES:
                        delay = _BASE_DELAY * (2 ** attempt) + random.uniform(0, 5)
                        if attempt == 0 and model_name != self._model:
                            logger.warning("rate_limit_429 on fallback model %s, retrying", model_name)
                        await asyncio.sleep(delay)
                        continue
                    if exc.response.status_code == 429:
                        logger.warning("rate_limit_429 exhausted on %s after %d retries", model_name, _MAX_RETRIES + 1)
                    raise

        raise cast("Exception", last_exc)

    def _build_payload(self, prompt: str) -> dict[str, object]:
        return {
            "contents": [
                {
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 8192,
            },
        }

    def _extract_text(self, data: dict[str, object]) -> str:
        candidates = cast("list[dict[str, object]]", data.get("candidates", []))
        if not candidates:
            msg = "Gemini returned no candidates"
            raise ValueError(msg)
        finish = candidates[0].get("finishReason")
        if finish and finish != "STOP":
            msg = f"Gemini generation incomplete (finishReason={finish}): response truncated"
            raise ValueError(msg)
        content = cast("dict[str, object]", candidates[0].get("content", {}))
        parts = cast("list[dict[str, object]]", content.get("parts", []))
        if not parts:
            msg = "Gemini returned no parts in candidate content"
            raise ValueError(msg)
        text = cast("str | None", parts[0].get("text"))
        if text is None:
            msg = "Gemini returned no text in first part"
            raise ValueError(msg)
        return text

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
        msg = f"Could not extract JSON from Gemini response:\n{text[:500]}"
        raise ValueError(msg)
