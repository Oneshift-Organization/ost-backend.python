from functools import lru_cache

import jwt
from jwt import PyJWKClient, PyJWTError

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.schemas.auth import UserClaims


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    settings = get_settings()
    if not settings.supabase_url:
        raise UnauthorizedError("Supabase URL not configured.")
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_keys=True)


def verify_jwt(token: str) -> UserClaims:
    """Validate a Supabase-issued RS256 JWT. Raises UnauthorizedError on failure."""
    if not token:
        raise UnauthorizedError("missing token")
    settings = get_settings()
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
        decoded = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.supabase_jwt_audience,
            options={"require": ["sub", "exp", "aud"]},
        )
    except PyJWTError as e:
        raise UnauthorizedError(f"invalid or expired token: {e}") from e

    return UserClaims(
        sub=decoded["sub"],
        email=decoded.get("email"),
        role=decoded.get("role", "authenticated"),
    )
