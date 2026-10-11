from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger
from app.infrastructure.auth.supabase_admin import SupabaseAuthAdmin
from app.infrastructure.repositories.pg_account_repo import PgAccountRepository
from app.use_cases.account.delete_account import DeleteAccountInput, DeleteAccountUseCase

router = APIRouter(prefix="/account", tags=["account"])

_log = get_logger(__name__)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Apaga a conta do usuário autenticado e todos os seus dados",
)
async def apagar_conta(user: CurrentUser, session: DbSession) -> Response:
    settings = get_settings()
    use_case = DeleteAccountUseCase(
        PgAccountRepository(session),
        SupabaseAuthAdmin(settings.supabase_url, settings.supabase_service_role_key),
    )
    try:
        await use_case.execute(DeleteAccountInput(user_id=UUID(user.sub)))
    except ExternalServiceError as e:
        # Nada foi apagado: a exceção desfaz a transação da sessão.
        _log.error("account_deletion_failed", user_id=user.sub, error=str(e))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "codigo": "EXCLUSAO_DE_CONTA_FALHOU",
                "mensagem": "Não foi possível excluir a conta agora. Nenhum dado foi apagado.",
                "retryable": True,
            },
        ) from e
    _log.info("account_deleted", user_id=user.sub)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
