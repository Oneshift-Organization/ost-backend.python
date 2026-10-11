"""DeleteAccountUseCase: dados antes do usuário, e nada pela metade."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ExternalServiceError
from app.use_cases.account.delete_account import DeleteAccountInput, DeleteAccountUseCase


class _FakeRepo:
    def __init__(self, chamadas: list[tuple[str, UUID]]) -> None:
        self._chamadas = chamadas

    async def delete_user_data(self, user_id: UUID) -> None:
        self._chamadas.append(("dados", user_id))


class _FakeAuth:
    def __init__(self, chamadas: list[tuple[str, UUID]], falha: Exception | None = None) -> None:
        self._chamadas = chamadas
        self._falha = falha

    async def delete_user(self, user_id: UUID) -> None:
        if self._falha:
            raise self._falha
        self._chamadas.append(("usuario", user_id))


@pytest.mark.asyncio
async def test_apaga_os_dados_antes_do_usuario():
    user_id = uuid4()
    chamadas: list[tuple[str, UUID]] = []
    uc = DeleteAccountUseCase(_FakeRepo(chamadas), _FakeAuth(chamadas))

    await uc.execute(DeleteAccountInput(user_id=user_id))

    assert chamadas == [("dados", user_id), ("usuario", user_id)]


@pytest.mark.asyncio
async def test_falha_no_provedor_de_identidade_propaga():
    """A rota depende da exceção para desfazer a transação dos dados."""
    chamadas: list[tuple[str, UUID]] = []
    uc = DeleteAccountUseCase(
        _FakeRepo(chamadas), _FakeAuth(chamadas, falha=ExternalServiceError("fora do ar"))
    )

    with pytest.raises(ExternalServiceError):
        await uc.execute(DeleteAccountInput(user_id=uuid4()))
