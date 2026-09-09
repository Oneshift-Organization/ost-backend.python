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

    # Swagger/ReDoc/openapi.json. Ligado inclusive em produção — ver comentário
    # em `create_app`. Desligue com DOCS_ENABLED=false se a superfície da API
    # precisar deixar de ser pública.
    docs_enabled: bool = True

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "https://dev.oneshift.com.br",
            "https://oneshift.com.br",
        ]
    )

    # API key de projeto GCP com a Cloud Billing API habilitada. Só o GCP
    # precisa: Azure, OCI e AWS publicam preço sem autenticação. Sem esta key o
    # sync de preços do GCP devolve vazio e registra o motivo — os demais
    # provedores continuam funcionando.
    gcp_billing_api_key: str = ""

    # O INTERRUPTOR. Com `false` (default), as rotas `/v1/agent/*` respondem 404
    # como se não existissem. Ligar NÃO desliga o legado — os dois convivem, e a
    # virada real é o consumidor parar de chamar o caminho antigo. Reverter é
    # mudar a variável e reiniciar: sem migration, sem deploy de código.
    agent_flow_enabled: bool = False

    cloud_creds_fernet_key: str = ""

    max_upload_size_mb: int = 50

    # Toggle to bypass JWT verification in dev. Hard-blocked in production
    # by `get_current_user` even if this is set to False by mistake.
    auth_enabled: bool = True

    llm_provider: Literal[
        "gemini",
        "openrouter",
        "anthropic",
        "nvidia",
        "mock",
    ] = "gemini"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-v4-flash:free"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "nvidia/nemotron-3.5-lightning-30b-a3b"
    nvidia_timeout_seconds: float = 300.0

    # Servidor MCP que fornece os prompts dos agentes OneShift.
    mcp_prompts_enabled: bool = False
    mcp_url: str = ""
    mcp_token: str = ""
    mcp_timeout_seconds: float = 30.0
    mcp_prompt_cache_seconds: int = 300

    # Tempo além do qual um job de análise ativo é considerado perdido. A
    # análise roda em BackgroundTasks no processo web, então um restart deixa
    # órfãos; o reaper do startup os encerra usando este limite.
    analysis_job_timeout_minutes: int = 30

    # ── Precision Engine v3 — câmbio ────────────────────────────────────
    # Fallback usado quando `exchange_rates` não tem taxa para o par.
    # Formato: unidades da moeda de origem por 1 USD (BRL/USD = 5.70).
    fx_brl_usd: Decimal = Decimal("5.70")

    gcp_service_account_path: str = Field(default="", alias="GOOGLE_APPLICATION_CREDENTIALS")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
