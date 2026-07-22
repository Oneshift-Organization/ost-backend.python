from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.dev",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    secret_key: str = "changeme"  # noqa: S105 - default sentinel; overridden via env

    database_url: str = "postgresql+asyncpg://user:pass@localhost:5432/oneshift_dev"
    db_pool_size: int = 10
    db_echo: bool = False

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_audience: str = "authenticated"
    # Pre-fetched JWKS JSON string. When set, JWT validation uses local keys
    # (no network needed). Leave empty to fetch from supabase_url at startup.
    supabase_jwks_json: str = ""

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "https://dev.oneshift.com.br",
            "https://oneshift.com.br",
        ]
    )

    cloud_creds_fernet_key: str = ""

    max_upload_size_mb: int = 50

    # Toggle to bypass JWT verification in dev. Hard-blocked in production
    # by `get_current_user` even if this is set to False by mistake.
    auth_enabled: bool = True

    llm_provider: Literal["gemini", "openrouter", "anthropic", "mock"] = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-flash:free"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    llm_timeout_seconds: int = 90
    llm_max_retries: int = 1

    gcp_service_account_path: str = Field(
        default="", alias="GOOGLE_APPLICATION_CREDENTIALS"
    )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
