"""Core configuration module using Pydantic Settings.

Loads from environment variables (prefix: LIGHTAGENT_) and optional .env file.
All configuration is validated at startup.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """LightAgent application settings.

    All fields can be overridden via environment variables using the
    LIGHTAGENT_ prefix (e.g. LIGHTAGENT_DEFAULT_MODEL=gpt-4o).
    """

    model_config = SettingsConfigDict(
        env_prefix="LIGHTAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────
    default_model: str = Field(
        default="claude-sonnet-4-5",
        description="Default LLM model (provider/model-id format for LiteLLM)",
    )
    fallback_model: str = Field(
        default="gpt-4o-mini",
        description="Fallback model if primary is unavailable",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="LLM sampling temperature (0.0 = deterministic, 2.0 = most random)",
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        description="Maximum number of tokens in the LLM response",
    )
    timeout_seconds: int = Field(
        default=60,
        ge=1,
        description="Timeout in seconds for a single LLM API call",
    )
    retry_attempts: int = Field(
        default=3,
        ge=0,
        description="Number of retry attempts on transient LLM API errors",
    )

    # ── API Keys ──────────────────────────────────────────────────────
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Anthropic API key",
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="OpenAI API key",
    )
    google_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Google AI API key",
    )

    # ── Security ──────────────────────────────────────────────────────
    security_mode: Literal["strict", "permissive", "audit-only"] = Field(
        default="strict",
        description="Guardrails enforcement mode",
    )
    risk_threshold: int = Field(
        default=30,
        ge=0,
        le=100,
        description="Risk score threshold (0-100) to block inputs in strict mode",
    )
    shell_enabled: bool = Field(
        default=False,
        description="Allow shell execution via ActionInterceptor (dangerous)",
    )
    nemo_guardrails_enabled: bool = Field(
        default=False,
        description="Enable NVIDIA NeMo Guardrails (requires config/nemo/)",
    )  # Wired up in T-022 (GuardrailsEngine)

    # ── Database ──────────────────────────────────────────────────────
    db_url: str = Field(
        default="sqlite+aiosqlite:///data/db/lightagent.db",
        description="SQLAlchemy async database URL",
    )
    chroma_path: str = Field(
        default="data/db/chroma",
        description="ChromaDB persistence directory",
    )

    # ── Embeddings ────────────────────────────────────────────────────
    embeddings_model: Literal["openai", "huggingface", "ollama"] = Field(
        default="huggingface",
        description="Embeddings provider for RAG",
    )

    # ── Server ────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", description="API server bind address")  # noqa: S104
    port: int = Field(default=8000, ge=1, le=65535, description="API server port")
    dashboard_port: int = Field(
        default=3000,
        ge=1,
        le=65535,
        description="Reflex dashboard port",
    )
    debug: bool = Field(default=False, description="Enable debug mode")

    # ── Logging ───────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Structured logging verbosity level",
    )
    log_format: Literal["json", "pretty"] = Field(
        default="pretty",
        description="Log output format (json for production)",
    )

    # ── Agent ─────────────────────────────────────────────────────────
    max_concurrent_agents: int = Field(
        default=5,
        ge=1,
        description="Maximum parallel sub-agent tasks",
    )
    agent_timeout_seconds: int = Field(
        default=300,
        ge=1,
        description="Maximum seconds for a single agent run",
    )

    # ── Memory ────────────────────────────────────────────────────────
    memory_retention_days: int = Field(
        default=30,
        ge=1,
        description="Days before long-term memory entries expire (AC-011-6)",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings instance.

    Uses lru_cache to ensure a single Settings object is created
    and reused across the application lifetime.

    Returns:
        The singleton Settings instance loaded from env / .env file.
    """
    return Settings()
