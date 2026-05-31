import json
from functools import lru_cache

import jwt
from jwt import PyJWKClient, PyJWTError
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.schemas.auth import UserClaims

# Supabase uses ES256 (EC P-256) for user JWTs.
# RS256 kept for compatibility in case the project rotates to RSA keys.
_SUPPORTED_ALGORITHMS = ["ES256", "RS256"]


@lru_cache(maxsize=1)
def _get_jwks_client() -> PyJWKClient:
    settings = get_settings()
    if not settings.supabase_url:
        raise UnauthorizedError("Supabase URL not configured.")
    jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_keys=True)


def _key_from_jwk(key_data: dict):
    kty = key_data.get("kty", "")
    if kty == "EC":
        return ECAlgorithm.from_jwk(key_data)
    if kty == "RSA":
        return RSAAlgorithm.from_jwk(key_data)
    raise UnauthorizedError(f"Unsupported JWKS key type: {kty}")


def _get_signing_key(token: str):
    """Return the signing key. Uses local JWKS if SUPABASE_JWKS_JSON is set,
    otherwise fetches from the Supabase JWKS endpoint (requires network)."""
    settings = get_settings()

    if settings.supabase_jwks_json:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        jwks_data = json.loads(settings.supabase_jwks_json)
        for key_data in jwks_data.get("keys", []):
            if key_data.get("kid") == kid:
                return _key_from_jwk(key_data)
        raise UnauthorizedError("No matching key found in local JWKS.")

    return _get_jwks_client().get_signing_key_from_jwt(token).key


def verify_jwt(token: str) -> UserClaims:
    """Validate a Supabase-issued JWT. Raises UnauthorizedError on failure."""
    if not token:
        raise UnauthorizedError("missing token")
    settings = get_settings()
    try:
        signing_key = _get_signing_key(token)
        decoded = jwt.decode(
            token,
            signing_key,
            algorithms=_SUPPORTED_ALGORITHMS,
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
