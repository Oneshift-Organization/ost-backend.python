"""Client LLM unificado via framework Agno.

Um único client cobre anthropic/gemini/openrouter com:
- structured output nativo (``output_schema`` → API estruturada do provider);
- temperature 0.2 (determinismo — relatorio.md A7);
- prompt caching do system block no Claude (``cache_system_prompt`` — L2).
Substitui os antigos claude_client/gemini_client/openrouter_client.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from agno.agent import Agent
from agno.models.anthropic import Claude
from agno.models.google import Gemini
from agno.models.openrouter import OpenRouter

from app.domain.interfaces.i_llm_client import ILLMClient

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.2
_MAX_OUTPUT_TOKENS = 16384


class AgnoLLMClient(ILLMClient):
    def __init__(self, provider: str, api_key: str, model_id: str) -> None:
        if not api_key:
            msg = f"API key ausente para provider={provider}"
            raise ValueError(msg)
        self._provider = provider
        self._model = _build_model(provider, api_key, model_id)

    async def generate(self, system: str, user: str, response_model: type[BaseModel]) -> BaseModel:
        agent = Agent(
            model=self._model,
            system_message=system,
            output_schema=response_model,
            # JSON mode em vez de strict structured output: o schema completo do
            # AnalysisV2Response (aninhado + dict[str, list[Any]]) estoura o limite
            # de compilação de gramática da Anthropic ("compiled grammar is too
            # large", 400). Em JSON mode o schema vai via prompt e o Agno valida
            # client-side; o resilient_parse do orchestrator cobre desvios.
            use_json_mode=True,
            markdown=False,
            telemetry=False,
        )
        run = await agent.arun(user)

        metrics = getattr(run, "metrics", None)
        if metrics is not None:
            logger.info(
                "llm_usage provider=%s input=%s output=%s cache_read=%s cache_write=%s",
                self._provider,
                getattr(metrics, "input_tokens", None),
                getattr(metrics, "output_tokens", None),
                getattr(metrics, "cache_read_tokens", None),
                getattr(metrics, "cache_write_tokens", None),
            )

        # Preferimos SEMPRE o dict cru extraído do texto do modelo: a conversão
        # typed do Agno valida com extra=ignore e descarta chaves variantes que
        # a tabela de aliases do resilient_parse saberia normalizar (lição A5).
        raw_text = _last_assistant_text(run)
        if raw_text:
            raw = _extract_json(raw_text)
            if raw is not None:
                return raw  # type: ignore[return-value]

        content = run.content
        if isinstance(content, response_model):
            return content
        if isinstance(content, dict):  # orchestrator aplica resilient_parse
            return content  # type: ignore[return-value]
        if isinstance(content, str):
            raw = _extract_json(content)
            if raw is not None:
                logger.warning("agno_json_mode_fallback: JSON extraído de resposta str")
                return raw  # type: ignore[return-value]
        msg = f"Agno retornou tipo inesperado: {type(content).__name__}"
        raise ValueError(msg)


def _last_assistant_text(run: object) -> str | None:
    """Texto cru da última mensagem do modelo no RunOutput do Agno."""
    messages = getattr(run, "messages", None) or []
    for m in reversed(messages):
        if getattr(m, "role", "") == "assistant" and isinstance(getattr(m, "content", None), str):
            return m.content
    return None


def _extract_json(text: str) -> dict | None:
    """Extrai o primeiro objeto JSON de um texto (com ou sem cerca de código)."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    start = stripped.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(stripped[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(stripped[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return parsed if isinstance(parsed, dict) else None
    return None


def _build_model(provider: str, api_key: str, model_id: str) -> Claude | Gemini | OpenRouter:
    match provider:
        case "anthropic":
            return Claude(
                id=model_id,
                api_key=api_key,
                temperature=_TEMPERATURE,
                max_tokens=_MAX_OUTPUT_TOKENS,
                cache_system_prompt=True,  # L2: system block cacheado
            )
        case "gemini":
            return Gemini(
                id=model_id,
                api_key=api_key,
                temperature=_TEMPERATURE,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            )
        case "openrouter":
            return OpenRouter(
                id=model_id,
                api_key=api_key,
                temperature=_TEMPERATURE,
                max_tokens=_MAX_OUTPUT_TOKENS,
            )
        case _:
            msg = f"provider LLM desconhecido: {provider}"
            raise ValueError(msg)
