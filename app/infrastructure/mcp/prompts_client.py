from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from time import monotonic
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_MCP_PROTOCOL_VERSION = "2024-11-05"


class MCPPrompt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent: str
    prompt_version: str = "unknown"
    content: str


class MCPPromptBundle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_contract_version: str
    prompts: list[MCPPrompt] = Field(default_factory=list)

    def render_for_llm(self) -> str:
        sections = [
            (
                f"## Agente: {prompt.agent}\n"
                f"Versão: {prompt.prompt_version}\n\n"
                f"{prompt.content}"
            )
            for prompt in self.prompts
        ]

        return "\n\n".join(sections)


class MCPClientError(RuntimeError):
    """Erro de comunicação ou contrato com o servidor MCP."""


class OneShiftMCPClient:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout_seconds: float | None = None,
        cache_seconds: int | None = None,
    ) -> None:
        settings = get_settings()

        self._url = (
            url if url is not None else settings.mcp_url
        ).rstrip("/")

        self._token = (
            token if token is not None else settings.mcp_token
        )

        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.mcp_timeout_seconds
        )

        configured_cache_seconds = (
            cache_seconds
            if cache_seconds is not None
            else settings.mcp_prompt_cache_seconds
        )

        self._cache_seconds = max(
            configured_cache_seconds,
            0,
        )

        self._cached_bundle: MCPPromptBundle | None = None
        self._cache_expires_at = 0.0
        self._cache_lock = asyncio.Lock()

    async def get_all_prompts(self) -> MCPPromptBundle:
        if not self._url:
            raise MCPClientError("MCP_URL não foi configurada")

        if not self._token:
            raise MCPClientError("MCP_TOKEN não foi configurado")

        cached_bundle = self._cached_bundle

        if (
            cached_bundle is not None
            and monotonic() < self._cache_expires_at
        ):
            logger.debug(
                "mcp_prompt_cache_hit contract=%s prompts=%d",
                cached_bundle.prompt_contract_version,
                len(cached_bundle.prompts),
            )

            return cached_bundle

        async with self._cache_lock:
            cached_bundle = self._cached_bundle

            if (
                cached_bundle is not None
                and monotonic() < self._cache_expires_at
            ):
                logger.debug(
                    "mcp_prompt_cache_hit_after_lock "
                    "contract=%s prompts=%d",
                    cached_bundle.prompt_contract_version,
                    len(cached_bundle.prompts),
                )

                return cached_bundle

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                ) as client:
                    bundle = await self._load_prompts(client)
            except httpx.TimeoutException as error:
                raise MCPClientError(
                    "Tempo limite excedido ao consultar o MCP"
                ) from error
            except httpx.HTTPError as error:
                raise MCPClientError(
                    "Falha de comunicação com o MCP"
                ) from error

            if self._cache_seconds > 0:
                self._cached_bundle = bundle
                self._cache_expires_at = (
                    monotonic() + self._cache_seconds
                )

            logger.info(
                "mcp_prompts_loaded contract=%s "
                "prompts=%d cache_seconds=%d",
                bundle.prompt_contract_version,
                len(bundle.prompts),
                self._cache_seconds,
            )

            return bundle

    async def _load_prompts(
        self,
        client: httpx.AsyncClient,
    ) -> MCPPromptBundle:
        base_headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _MCP_PROTOCOL_VERSION,
        }

        initialize_response = await client.post(
            self._url,
            headers=base_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "oneshift-backend",
                        "version": "1.0.0",
                    },
                },
            },
        )

        _ensure_http_success(initialize_response)
        _raise_rpc_error(
            _decode_rpc_response(initialize_response)
        )

        session_id = initialize_response.headers.get(
            "mcp-session-id"
        )

        if not session_id:
            raise MCPClientError(
                "O MCP não retornou um identificador de sessão"
            )

        session_headers = {
            **base_headers,
            "Mcp-Session-Id": session_id,
        }

        try:
            initialized_response = await client.post(
                self._url,
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                },
            )

            _ensure_http_success(initialized_response)

            tool_response = await client.post(
                self._url,
                headers=session_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "get_all_prompts",
                        "arguments": {},
                    },
                },
            )

            _ensure_http_success(tool_response)

            rpc_response = _decode_rpc_response(
                tool_response
            )

            _raise_rpc_error(rpc_response)

            return _extract_prompt_bundle(rpc_response)
        finally:
            try:
                await client.delete(
                    self._url,
                    headers=session_headers,
                )
            except httpx.HTTPError:
                logger.debug(
                    "mcp_session_close_failed",
                    exc_info=True,
                )


@lru_cache(maxsize=1)
def get_mcp_prompt_client() -> OneShiftMCPClient:
    """Retorna um cliente compartilhado com cache em memória."""
    return OneShiftMCPClient()


def _ensure_http_success(
    response: httpx.Response,
) -> None:
    if response.is_error:
        raise MCPClientError(
            f"O MCP respondeu com HTTP {response.status_code}"
        )


def _decode_rpc_response(
    response: httpx.Response,
) -> dict[str, Any]:
    body = response.text.strip()

    if not body:
        raise MCPClientError("O MCP retornou uma resposta vazia")

    content_type = response.headers.get(
        "content-type",
        "",
    )

    try:
        if "text/event-stream" in content_type:
            messages: list[dict[str, Any]] = []

            for line in body.splitlines():
                if not line.startswith("data:"):
                    continue

                data = line.removeprefix("data:").strip()

                if not data or data == "[DONE]":
                    continue

                parsed = json.loads(data)

                if isinstance(parsed, dict):
                    messages.append(parsed)

            if not messages:
                raise MCPClientError(
                    "O MCP não retornou dados no evento"
                )

            return messages[-1]

        parsed = response.json()
    except json.JSONDecodeError as error:
        raise MCPClientError(
            "O MCP retornou um JSON inválido"
        ) from error

    if not isinstance(parsed, dict):
        raise MCPClientError(
            "O MCP retornou um formato inesperado"
        )

    return parsed


def _raise_rpc_error(
    rpc_response: dict[str, Any],
) -> None:
    error = rpc_response.get("error")

    if error is None:
        return

    if isinstance(error, dict):
        message = str(
            error.get("message", "Erro desconhecido")
        )
    else:
        message = str(error)

    raise MCPClientError(
        f"O MCP retornou um erro: {message}"
    )


def _extract_prompt_bundle(
    rpc_response: dict[str, Any],
) -> MCPPromptBundle:
    result = rpc_response.get("result")

    if not isinstance(result, dict):
        raise MCPClientError(
            "O resultado da tool MCP não foi encontrado"
        )

    if result.get("isError") is True:
        raise MCPClientError(
            "A tool get_all_prompts retornou erro"
        )

    content = result.get("content")

    if not isinstance(content, list):
        raise MCPClientError(
            "A tool MCP não retornou conteúdo"
        )

    text: str | None = None

    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            text = item["text"]
            break

    if text is None:
        raise MCPClientError(
            "A tool MCP não retornou conteúdo de texto"
        )

    try:
        payload = json.loads(text)
        bundle = MCPPromptBundle.model_validate(payload)
    except (
        json.JSONDecodeError,
        ValidationError,
    ) as error:
        raise MCPClientError(
            "Os prompts retornados pelo MCP são inválidos"
        ) from error

    if not bundle.prompts:
        raise MCPClientError(
            "O MCP retornou uma lista de prompts vazia"
        )

    return bundle