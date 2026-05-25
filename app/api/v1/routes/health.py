from fastapi import APIRouter, status
from sqlalchemy import text

from app.api.deps import DbSession

router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health(session: DbSession) -> dict[str, str]:
    """Liveness + DB connectivity probe."""
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception:
        return {"status": "degraded", "db": "down"}
