from decimal import Decimal
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

    app_env: Literal[
        "development",
        "staging",
        "production",
    ] = "development"

    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
    ] = "INFO"

    secret_key: str = "changeme"  # noqa: S105

    database_url: str = (
        "postgresql+asyncpg://user:pass@localhost:5432/"
        "oneshift_dev"
    )
    db_pool_size: int = 10
    db_echo: bool = False

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwks_json: str = ""

    docs_enabled: bool = True

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "https://dev.oneshift.com.br",
            "https://oneshift.com.br",
        ]
    )

    gcp_billing_api_key: str = ""

    agent_flow_enabled: bool = False

    # O MESMO INTERRUPTOR, para a geração de Terraform. Com `false` (default) as
    # rotas `/v1/terraform-generations` respondem 404. Desligar não perde dado:
    # as gerações já feitas continuam na tabela.
    terraform_generation_enabled: bool = False

    cloud_creds_fernet_key: str = ""

    max_upload_size_mb: int = 50

    auth_enabled: bool = True

    # Provedor principal das análises.
    llm_provider: Literal[
        "gemini",
        "openrouter",
        "anthropic",
        "nvidia",
        "mock",
    ] = "gemini"

    # Provedor usado somente quando o principal estiver indisponível.
    # "none" preserva o comportamento anterior.
    llm_fallback_provider: Literal[
        "none",
        "nvidia",
    ] = "none"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    openrouter_api_key: str = ""
    openrouter_model: str = (
        "deepseek/deepseek-v4-flash:free"
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    nvidia_api_key: str = ""
    nvidia_base_url: str = (
        "https://integrate.api.nvidia.com/v1"
    )
    nvidia_model: str = (
        "nvidia/nemotron-3.5-lightning-30b-a3b"
    )
    nvidia_timeout_seconds: float = 300.0

    # Servidor MCP usado pela análise NVIDIA.
    mcp_prompts_enabled: bool = False
    mcp_url: str = ""
    mcp_token: str = ""
    mcp_timeout_seconds: float = 30.0
    mcp_prompt_cache_seconds: int = 300

    analysis_job_timeout_minutes: int = 30

    fx_brl_usd: Decimal = Decimal("5.70")

    gcp_service_account_path: str = Field(
        default="",
        alias="GOOGLE_APPLICATION_CREDENTIALS",
    )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()