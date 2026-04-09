"""
Core configuration module using Pydantic Settings.

Loads from environment variables (prefix: LIGHTAGENT_) and optional .env file. All
configuration is validated at startup.
"""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MaintenanceSettings(BaseSettings):
    """Package security and maintenance configuration (SPEC-031/032).

    All fields are readable from environment variables with the
    ``LIGHTAGENT_MAINTENANCE_`` prefix (e.g.
    ``LIGHTAGENT_MAINTENANCE_CONFIRM=false``).
    """

    model_config = SettingsConfigDict(
        env_prefix="LIGHTAGENT_MAINTENANCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    confirm: bool = Field(
        default=True,
        description=(
            "Require interactive confirmation before applying any package update. "
            "Set to false for CI/CD pipelines (LIGHTAGENT_MAINTENANCE_CONFIRM=false)."
        ),
    )
    backup_dir: str = Field(
        default="data/backups",
        description=(
            "Directory for pyproject.toml backups created before each write. "
            "Created on demand."
        ),
    )
    reports_dir: str = Field(
        default="data/logs",
        description=(
            "Directory where JSON audit reports are saved "
            "(``security_audit_YYYYMMDDHHMMSS.json``). Created on demand."
        ),
    )
    osv_concurrency: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum concurrent OSV API requests during a full package scan.",
    )
    uv_timeout: int = Field(
        default=120,
        ge=10,
        description="Timeout in seconds for each ``uv pip install`` subprocess.",
    )
    pypi_timeout: int = Field(
        default=10,
        ge=3,
        description="Timeout in seconds for PyPI JSON API HTTP requests.",
    )


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

    # ── Maintenance (read-only property — env prefix LIGHTAGENT_MAINTENANCE_) ──
    @property
    def maintenance(self) -> MaintenanceSettings:
        """Return the package maintenance sub-settings.

        Delegates to :func:`get_maintenance_settings` so that the same
        ``lru_cache``d instance is reused across the application lifetime.

        Returns:
            Cached :class:`MaintenanceSettings` loaded from env / .env file.
        """
        return get_maintenance_settings()

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
    memory_backend: str = Field(
        default="memory",
        description=(
            "Long-term memory store backend: 'memory' (InMemoryStore, dev), "
            "'sqlite' (aiosqlite-backed), or 'postgresql' (AsyncPostgresStore). "
            "SPEC-039 AC-039-1."
        ),
    )
    memory_extraction_enabled: bool = Field(
        default=True,
        description=(
            "Toggle LLM-based memory extraction at session end. "
            "When False, sessions complete without firing the extraction task."
        ),
    )
    memory_recall_limit: int = Field(
        default=5,
        ge=0,
        le=50,
        description="Max long-term facts retrieved per supervisor invocation.",
    )
    memory_default_ttl_days: int = Field(
        default=90,
        ge=0,
        description=(
            "Default TTL for stored facts in days. 0 disables expiration. "
            "SPEC-039 AC-039-6."
        ),
    )

    # ── CodeAct Agent (Phase 38 / SPEC-040) ───────────────────────────
    codeact_enabled: bool = Field(
        default=True,
        description=(
            "Toggle CodeAct agent. When False the supervisor never routes to "
            "'codeact' and falls back to the classic ReAct 'coder' node."
        ),
    )
    codeact_max_iterations: int = Field(
        default=6,
        ge=1,
        le=20,
        description="Max generate/correct cycles per CodeAct invocation.",
    )
    codeact_import_allowlist: str = Field(
        default=(
            "os,pathlib,subprocess,json,re,typing,datetime,collections,"
            "itertools,functools,math,statistics,random,hashlib,base64,"
            "csv,io,sys,pandas,numpy,polars,matplotlib,sklearn,torch,"
            "flaml,duckdb,requests,httpx"
        ),
        description=(
            "Comma-separated list of packages CodeAct code blocks may "
            "import. Any non-allowlisted import blocks execution."
        ),
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

    # Telegram — Webhook transport (optional, Phase 29)
    telegram_webhook_enabled: bool = Field(
        default=False,
        description="If True, use webhook instead of long-polling.",
    )
    telegram_webhook_url: str = Field(
        default="",
        description="Full HTTPS URL for Telegram webhook. Required when webhook_enabled=True.",
    )
    telegram_webhook_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Secret token for X-Telegram-Bot-Api-Secret-Token header validation.",
    )
    telegram_max_connections: int = Field(
        default=40,
        ge=1,
        le=100,
        description="Max concurrent connections for webhook (1-100).",
    )

    # Telegram — Session controls (Phase 29)
    telegram_session_inline_keyboard: bool = Field(
        default=True,
        description="Append [New Chat] [Reset] inline buttons to every agent response.",
    )
    telegram_max_sessions_per_user: int = Field(
        default=10,
        ge=1,
        description="Max archived sessions per (chat_id, user_id) pair.",
    )

    # Telegram — Message tracking (Phase 29)
    telegram_message_track: bool = Field(
        default=True,
        description="Record bot-sent message IDs in SQLite for later deletion.",
    )

    # Telegram — Formatting (Phase 29)
    telegram_parse_mode: str = Field(
        default="HTML",
        description="Telegram parse mode: 'HTML' or 'MarkdownV2'.",
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

    # ── Human-in-the-Loop (Phase 35) ──────────────────────────────────
    hitl_enabled: bool = Field(
        default=True,
        description=(
            "Global toggle for HITL approval gates. Set false in CI/CD to "
            "bypass all interrupt() calls and route directly to on_approve."
        ),
    )
    hitl_timeout_seconds: int = Field(
        default=86400,
        ge=0,
        description=(
            "Maximum seconds to wait before auto-rejecting a suspended "
            "workflow (0 = no timeout). Used by housekeeping jobs."
        ),
    )

    # ── Map-Reduce Parallel Execution (Phase 34) ──────────────────────
    parallel_enabled: bool = Field(
        default=True,
        description="Global toggle for parallel Send() fan-out execution.",
    )
    parallel_max_workers: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum concurrent Send() dispatches per fan-out node.",
    )

    # ── Reflection Loop Framework (Phase 33) ──────────────────────────
    reflection_enabled: bool = Field(
        default=True,
        description="Global toggle for the generate-critique-refine reflection loop.",
    )
    reflection_default_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Default score threshold that ends a reflection loop early.",
    )
    reflection_max_iterations: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Hard cap on reflection generate iterations.",
    )

    # Dynamic Subgraphs (Phase 24)
    enable_subgraphs: bool = Field(
        default=False,
        description="Enable dynamic sub-agent orchestration (Phase 24).",
    )

    # Webhooks (Phase 25)
    webhooks_enabled: bool = Field(
        default=True,
        description="Enable webhook delivery system.",
    )
    webhooks_signing_key: SecretStr = Field(
        default=SecretStr("change-me-in-production"),
        description="Default HMAC signing key for webhook payloads.",
    )

    # ML/DL Pipeline (Phase 26)
    ml_enabled: bool = Field(default=True, description="Enable ML/DL pipeline subgraph")
    ml_time_budget: int = Field(
        default=120, gt=0, description="FLAML AutoML time budget in seconds"
    )
    ml_quality_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum primary_score to pass model quality gate",
    )
    ml_max_iterations: int = Field(
        default=3, ge=1, description="Max retrain iterations on quality gate failure"
    )
    ml_workspace_root: str = Field(
        default="data/workspace/ml_models",
        description="Root directory for ML model outputs",
    )
    ml_max_rows: int = Field(
        default=1_000_000,
        gt=0,
        description="Maximum dataset rows allowed (safety limit)",
    )
    ml_random_seed: int = Field(
        default=42, description="Global random seed for reproducibility"
    )
    ml_shap_max_samples: int = Field(
        default=1000, gt=0, description="Max background samples for SHAP explainer"
    )

    # ---------------------------------------------------------------------------
    # Financial Analysis (Phase 27)
    # ---------------------------------------------------------------------------
    financial_default_provider: str = Field(
        default="yfinance",
        description="Primary market data provider (yfinance | openbb | ccxt)",
    )
    financial_cache_ttl_ticker: int = Field(
        default=30,
        ge=1,
        description="TTL in seconds for ticker/price cache",
    )
    financial_cache_ttl_ohlcv: int = Field(
        default=300,
        ge=1,
        description="TTL in seconds for OHLCV bar cache (5 minutes)",
    )
    financial_cache_ttl_fundamentals: int = Field(
        default=86400,
        ge=1,
        description="TTL in seconds for fundamentals cache (24 hours)",
    )
    financial_workspace_path: str = Field(
        default="data/workspace/financial",
        description="Root directory for financial output files",
    )
    financial_trade_execution_enabled: bool = Field(
        default=False,
        description="Phase 27 is read-only — trade execution must always be False",
    )

    # ── Phase 39 / SPEC-041 — Financial Pipeline Quality Gates ────────
    financial_min_confidence: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum MarketSnapshot.data_confidence required for the "
            "financial pipeline to continue past data collection. When "
            "missing_fields is non-empty AND data_confidence is below "
            "this threshold, the pipeline routes to END with an error "
            "FinancialReport. SPEC-041 AC-041-2."
        ),
    )
    financial_technical_min_confidence: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum TechnicalAnalysis.data_confidence required to run "
            "fundamental analysis. Below this threshold the pipeline "
            "skips fundamental_analyst and jumps straight to "
            "risk_sentiment_analyst, and the final report is tagged "
            "with a 'limited data' disclaimer. SPEC-041 AC-041-3."
        ),
    )
    financial_hitl_enabled: bool = Field(
        default=False,
        description=(
            "Enable a HITL approval gate before delivering financial "
            "reports to the user. When True the report_generator's "
            "output is surfaced through hitl_gate() for human review; "
            "when False the pipeline ends as soon as the report is "
            "produced. SPEC-041 AC-041-4."
        ),
    )

    # ── DateTime & Timezone (Phase 28) ────────────────────────────────
    timezone: str = Field(
        default="",
        description=(
            "IANA timezone for the process (e.g. 'America/Caracas'). "
            "Empty = auto-detect from OS via tzlocal."
        ),
    )
    cron_timezone: str = Field(
        default="",
        description=(
            "Default IANA timezone for all cron jobs. "
            "Empty = inherit from 'timezone' field."
        ),
    )
    ntp_enabled: bool = Field(
        default=False,
        description="Enable NTP clock drift check.",
    )
    ntp_server: str = Field(
        default="pool.ntp.org",
        description="NTP server hostname. Only used when ntp_enabled=True.",
    )
    ntp_sync_interval_seconds: int = Field(
        default=3600,
        ge=60,
        description="Seconds between NTP re-sync. Minimum 60.",
    )
    ntp_warn_threshold_seconds: int = Field(
        default=5,
        ge=1,
        description="Log WARNING if NTP offset exceeds this many seconds.",
    )

    @model_validator(mode="after")
    def _validate_telegram_webhook(self) -> "Settings":
        """Validate webhook config when telegram_webhook_enabled is True."""
        if self.telegram_webhook_enabled:
            if not self.telegram_webhook_url.startswith("https://"):
                raise ValueError(
                    "LIGHTAGENT_TELEGRAM_WEBHOOK_URL must be an HTTPS URL"
                )
            if not self.telegram_webhook_secret.get_secret_value():
                raise ValueError(
                    "LIGHTAGENT_TELEGRAM_WEBHOOK_SECRET must be set when webhook is enabled"
                )
        return self

    @model_validator(mode="after")
    def _validate_iana_timezones(self) -> "Settings":
        """Validate that timezone and cron_timezone are valid IANA names if set."""
        for field_name, value in (
            ("timezone", self.timezone),
            ("cron_timezone", self.cron_timezone),
        ):
            if value:
                try:
                    ZoneInfo(value)
                except (ZoneInfoNotFoundError, KeyError) as exc:
                    raise ValueError(
                        f"{value} is not a valid IANA timezone"
                        f" (field: LIGHTAGENT_{field_name.upper()})"
                    ) from exc
        return self


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


@lru_cache(maxsize=1)
def get_maintenance_settings() -> MaintenanceSettings:
    """Return the cached :class:`MaintenanceSettings` instance.

    Uses ``lru_cache`` so the same object is reused across the application
    lifetime.  Also exposed via :attr:`Settings.maintenance` for convenience.

    Returns:
        Singleton :class:`MaintenanceSettings` loaded from env / .env file.
    """
    return MaintenanceSettings()
