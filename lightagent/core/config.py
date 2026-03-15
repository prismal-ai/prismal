"""
Core configuration module using Pydantic Settings.

Loads from environment variables (prefix: LIGHTAGENT_) and optional .env file. All
configuration is validated at startup.
"""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    LightAgent application settings.

    All fields can be overridden via environment variables using the LIGHTAGENT_ prefix
    (e.g. LIGHTAGENT_DEFAULT_MODEL=gpt-4o).
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
        default=1,
        ge=0,
        description="Number of retry attempts on transient LLM API errors",
    )

    # ── API Keys ──────────────────────────────────────────────────────
    anthropic_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "LIGHTAGENT_ANTHROPIC_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        description=(
            "Anthropic API key"
            " (LIGHTAGENT_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY)"
        ),
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "LIGHTAGENT_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        ),
        description="OpenAI API key (LIGHTAGENT_OPENAI_API_KEY or OPENAI_API_KEY)",
    )
    google_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "LIGHTAGENT_GOOGLE_API_KEY",
            "GOOGLE_API_KEY",
        ),
        description="Google AI API key (LIGHTAGENT_GOOGLE_API_KEY or GOOGLE_API_KEY)",
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

    # ── Skills ────────────────────────────────────────────────────────
    external_skills_dirs: list[str] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for skills. "
            "Each entry is a filesystem path that contains skill subdirectories "
            "(same layout as lightagent/skills/available/). "
            "Set via env var as a JSON array: "
            'LIGHTAGENT_EXTERNAL_SKILLS_DIRS=\'["/home/user/.agents/skills"]\''
        ),
    )

    # ── Sandbox multi-lenguaje ────────────────────────────────────────
    sandbox_path: str = Field(
        default="sandbox",
        description="Ruta raíz de la sandbox de desarrollo multi-lenguaje",
    )
    sandbox_node_version: str = Field(
        default="20.11.0",
        description="Versión de Node.js a instalar en la sandbox",
    )
    sandbox_go_version: str = Field(
        default="1.22.0",
        description="Versión de Go a instalar en la sandbox",
    )
    sandbox_exec_timeout: int = Field(
        default=60,
        ge=5,
        le=300,
        description="Timeout en segundos para ejecuciones en sandbox",
    )
    sandbox_max_output_chars: int = Field(
        default=8_000,
        ge=500,
        description="Máximo de caracteres de output retornados por la sandbox",
    )

    nemo_guardrails_enabled: bool = Field(
        default=False,
        description="Enable NVIDIA NeMo Guardrails (requires config/nemo_rails/)",
    )

    # ── Database ──────────────────────────────────────────────────────
    db_url: str = Field(
        default="sqlite+aiosqlite:///data/db/lightagent.db",
        description="SQLAlchemy async database URL",
    )
    chroma_path: str = Field(
        default="data/db/chroma",
        description="ChromaDB persistence directory",
    )
    mongodb_url: str = Field(
        default="",
        description=(
            "MongoDB connection URL (optional — enables MongoDBMemoryStore). "
            "Example: mongodb://user:pass@localhost:27017/lightagent"
        ),
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

    # ── Monitoring — Langfuse ────────────────────────────────────
    langfuse_enabled: bool = Field(
        default=True,
        description="Enable Langfuse LLM observability tracing",
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices(
            "LIGHTAGENT_LANGFUSE_HOST",
            "LANGFUSE_HOST",
        ),
        description="Langfuse server URL",
    )
    langfuse_public_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "LIGHTAGENT_LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_PUBLIC_KEY",
        ),
        description="Langfuse public key",
    )
    langfuse_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "LIGHTAGENT_LANGFUSE_SECRET_KEY",
            "LANGFUSE_SECRET_KEY",
        ),
        description="Langfuse secret key",
    )
    langfuse_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of traces sent to Langfuse (1.0 = all)",
    )

    # ── Monitoring — OpenTelemetry ───────────────────────────────
    otel_enabled: bool = Field(
        default=False,
        description=(
            "Enable OpenTelemetry distributed tracing and metrics"
            " (set LIGHTAGENT_OTEL_ENABLED=true in production)"
        ),
    )
    otel_exporter: Literal["otlp", "jaeger", "zipkin", "console"] = Field(
        default="console",
        description="OTEL exporter backend (use 'otlp' in production with a collector)",
    )
    otel_endpoint: str = Field(
        default="http://localhost:4318",
        description="OTLP exporter endpoint (HTTP)",
    )
    otel_metrics_enabled: bool = Field(
        default=False,
        description=(
            "Enable OTLP metric export.  Requires a metrics-capable backend"
            " (e.g. Prometheus + OTEL Collector).  Defaults to False because"
            " common trace backends like Jaeger return 404 on /v1/metrics."
        ),
    )
    otel_service_name: str = Field(
        default="lightagent",
        description="Service name for OTEL resource attributes",
    )

    # ── Monitoring — Log file ────────────────────────────────────
    log_file_rotation: str = Field(
        default="500 MB",
        description="Loguru file rotation policy",
    )
    log_file_retention: str = Field(
        default="30 days",
        description="Loguru file retention policy",
    )
    log_file_path: str = Field(
        default="data/logs/lightagent.log",
        description="Log file path for Loguru file sink",
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

    # ── API ───────────────────────────────────────────────────────────
    api_key: SecretStr = Field(
        default=SecretStr(""),
        description="X-API-Key for REST auth; empty string disables auth (dev mode)",
    )
    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed CORS origins for the REST API",
    )

    # ── Auth / RBAC ────────────────────────────────────────────────────
    rbac_enabled: bool = Field(
        default=False,
        description=(
            "Enable JWT-based multi-user RBAC. "
            "When False, the simple api_key auth is used."
        ),
    )
    jwt_secret_key: SecretStr = Field(
        default=SecretStr("change-me-in-production"),
        description="HMAC secret used to sign JWT tokens (HS256)",
    )
    access_token_expire_minutes: int = Field(
        default=60,
        ge=1,
        description="JWT access token lifetime in minutes (AC-018-10)",
    )
    refresh_token_expire_days: int = Field(
        default=30,
        ge=1,
        description="JWT refresh token lifetime in days (AC-018-10)",
    )
    users_db_path: str = Field(
        default="data/db/users.db",
        description="SQLite file path for the user store",
    )
    budget_alert_usd: float = Field(
        default=10.0,
        ge=0.0,
        description="Per-user cost budget threshold in USD before an alert is raised",
    )

    # ── Voice Interface ────────────────────────────────────────────────
    stt_provider: str = Field(
        default="openai",
        description="STT backend: 'openai' (Whisper API) or 'local'",
    )
    tts_provider: str = Field(
        default="pyttsx3",
        description="TTS backend: 'pyttsx3', 'openai', or 'elevenlabs'",
    )
    elevenlabs_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="ElevenLabs API key for TTS (optional)",
    )
    voice_language: str = Field(
        default="",
        description="ISO-639-1 language hint for STT (empty = auto-detect)",
    )
    voice_record_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=60.0,
        description="Max recording duration per voice turn in seconds",
    )

    # ---------------------------------------------------------------------------
    # Channel gateway settings
    # ---------------------------------------------------------------------------

    # Telegram
    telegram_bot_token: SecretStr = Field(
        default=SecretStr(""),
        description="Telegram Bot API token",
    )

    # Slack
    slack_bot_token: SecretStr = Field(
        default=SecretStr(""),
        description="Slack Bot User OAuth token (xoxb-...)",
    )
    slack_app_token: SecretStr = Field(
        default=SecretStr(""),
        description="Slack App-level token for Socket Mode (xapp-...)",
    )

    # Discord
    discord_bot_token: SecretStr = Field(
        default=SecretStr(""),
        description="Discord Bot token",
    )

    # Microsoft Teams
    teams_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Teams HMAC webhook signing secret",
    )

    # WhatsApp (Meta Business)
    whatsapp_access_token: SecretStr = Field(
        default=SecretStr(""),
        description="Meta WhatsApp Cloud API access token",
    )
    whatsapp_phone_number_id: str = Field(
        default="",
        description="Meta WhatsApp phone number ID",
    )
    whatsapp_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        description="WhatsApp webhook verification token and hub secret",
    )

    # Signal (via signal-cli-rest-api)
    signal_api_url: str = Field(
        default="http://localhost:8080",
        description="signal-cli-rest-api base URL",
    )
    signal_phone_number: str = Field(
        default="",
        description="Signal account phone number (E.164 format)",
    )

    # Channel security
    channel_security_enabled: bool = Field(
        default=True,
        description="Enable/disable the full 8-guard channel security pipeline",
    )

    # ── Cron notifications ────────────────────────────────────────────
    cron_notify_telegram_chat_id: str = Field(
        default="",
        description=(
            "Telegram chat ID to send cron failure alerts to. "
            "Uses telegram_bot_token. Empty = disabled."
        ),
    )
    cron_notify_slack_channel: str = Field(
        default="",
        description=(
            "Slack channel name or ID for cron failure alerts (e.g. '#alerts'). "
            "Uses slack_bot_token. Empty = disabled."
        ),
    )

    # ── Preferences auto-extraction ───────────────────────────────────
    preferences_auto_extract: bool = Field(
        default=True,
        description=(
            "Enable automatic preference extraction from conversation history. "
            "When True, the system analyses conversations in the background and "
            "updates PREFERENCES.md without blocking the chat response."
        ),
    )
    preferences_extract_cooldown_minutes: int = Field(
        default=30,
        ge=1,
        description=(
            "Minimum minutes between automatic preference extractions per session. "
            "Extraction also triggers after every 5 new messages."
        ),
    )

    # ── Filesystem access ─────────────────────────────────────────────
    fs_workspace_root: str = Field(
        default="",
        description=(
            "Confine agent filesystem access to this directory. Empty = unrestricted."
        ),
    )
    fs_allow_outside_workspace: bool = Field(
        default=False,
        description="If True, agent may access paths outside fs_workspace_root.",
    )
    fs_delete_enabled: bool = Field(
        default=False,
        description="Allow the delete_path tool to remove files/directories.",
    )

    # ── Heartbeat — SMTP email delivery ───────────────────────────────
    heartbeat_smtp_host: str = Field(
        default="",
        description="SMTP server hostname for heartbeat email delivery.",
    )
    heartbeat_smtp_port: int = Field(
        default=587,
        ge=1,
        le=65535,
        description="SMTP server port (default 587 for STARTTLS).",
    )
    heartbeat_smtp_user: str = Field(
        default="",
        description="SMTP authentication username.",
    )
    heartbeat_smtp_password: SecretStr = Field(
        default=SecretStr(""),
        description="SMTP authentication password.",
    )
    heartbeat_smtp_from: str = Field(
        default="",
        description="Sender address for heartbeat email reports.",
    )

    # Dynamic Subgraphs (Phase 24)
    enable_subgraphs: bool = Field(
        default=False,
        description="Enable dynamic sub-agent orchestration (Phase 24).",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings instance.

    Uses lru_cache to ensure a single Settings object is created
    and reused across the application lifetime.

    Returns:
        The singleton Settings instance loaded from env / .env file.
    """
    return Settings()
