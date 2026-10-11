from dataclasses import dataclass
from uuid import UUID

from app.domain.interfaces.i_auth_admin import IAuthAdmin
from app.domain.repositories.i_account_repo import IAccountRepository


@dataclass(frozen=True)
class DeleteAccountInput:
    user_id: UUID


class DeleteAccountUseCase:
    """Apaga os dados do usuário e depois o próprio usuário.

    A ordem importa. Os dados saem primeiro, ainda dentro da transação: se o
    provedor de identidade falhar, a exceção desfaz tudo e a conta continua
    inteira para uma nova tentativa. Na ordem inversa, uma falha no banco
    deixaria dados sem dono e sem ninguém capaz de pedir a exclusão deles.
    """

    def __init__(self, account_repo: IAccountRepository, auth_admin: IAuthAdmin) -> None:
        self._repo = account_repo
        self._auth = auth_admin

    async def execute(self, payload: DeleteAccountInput) -> None:
        await self._repo.delete_user_data(payload.user_id)
        await self._auth.delete_user(payload.user_id)
