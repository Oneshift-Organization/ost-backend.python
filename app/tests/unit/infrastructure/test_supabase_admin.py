"""SupabaseAuthAdmin: contrato HTTP da exclusão de usuário, sem rede."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from app.core.exceptions import ExternalServiceError
from app.infrastructure.auth.supabase_admin import SupabaseAuthAdmin

_URL = "https://projeto.supabase.co"
_KEY = "chave-de-teste"


def _admin(handler) -> SupabaseAuthAdmin:
    return SupabaseAuthAdmin(_URL, _KEY, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_chama_a_rota_de_admin_com_a_service_role():
    user_id = uuid4()
    vistos: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request)
        return httpx.Response(200, json={})

    await _admin(handler).delete_user(user_id)

    (request,) = vistos
    assert request.method == "DELETE"
    assert str(request.url) == f"{_URL}/auth/v1/admin/users/{user_id}"
    assert request.headers["apikey"] == _KEY
    assert request.headers["Authorization"] == f"Bearer {_KEY}"


@pytest.mark.asyncio
async def test_usuario_ja_removido_nao_e_erro():
    await _admin(lambda _: httpx.Response(404, json={})).delete_user(uuid4())


@pytest.mark.asyncio
async def test_erro_do_supabase_vira_external_service_error():
    with pytest.raises(ExternalServiceError):
        await _admin(lambda _: httpx.Response(500)).delete_user(uuid4())


@pytest.mark.asyncio
async def test_falha_de_rede_vira_external_service_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota", request=request)

    with pytest.raises(ExternalServiceError):
        await _admin(handler).delete_user(uuid4())


@pytest.mark.asyncio
@pytest.mark.parametrize(("url", "key"), [("", _KEY), (_URL, "")])
async def test_sem_configuracao_nao_faz_chamada(url: str, key: str):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("não deveria chamar o Supabase")

    admin = SupabaseAuthAdmin(url, key, transport=httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError):
        await admin.delete_user(uuid4())
