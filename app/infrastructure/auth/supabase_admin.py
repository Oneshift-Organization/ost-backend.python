"""Admin API do Supabase Auth.

Usa a `service_role`, que ignora RLS e administra usuários: só existe no
servidor e nunca sai dele.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from app.core.exceptions import ExternalServiceError
from app.domain.interfaces.i_auth_admin import IAuthAdmin


class SupabaseAuthAdmin(IAuthAdmin):
    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._key = service_role_key
        self._transport = transport
        self._timeout = timeout

    async def delete_user(self, user_id: UUID) -> None:
        if not (self._url and self._key):
            raise ExternalServiceError("Supabase admin não configurado")
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout
            ) as client:
                resposta = await client.delete(
                    f"{self._url}/auth/v1/admin/users/{user_id}",
                    headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
                )
        except httpx.HTTPError as e:
            raise ExternalServiceError("Supabase admin inacessível") from e
        # 404 = usuário já removido. Repetir a exclusão tem que dar certo.
        if resposta.status_code == httpx.codes.NOT_FOUND:
            return
        if resposta.is_error:
            raise ExternalServiceError(f"Supabase admin respondeu {resposta.status_code}")
