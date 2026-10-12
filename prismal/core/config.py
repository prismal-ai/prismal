"""
Core configuration module using Pydantic Settings.

Loads from environment variables (prefix: PRISMAL_) and optional .env file. All
configuration is validated at startup.
"""

import re
from collections.abc import Mapping
from contextvars import ContextVar
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource
from pydantic_settings.sources.providers.env import parse_env_vars  # type: ignore[attr-defined]

if TYPE_CHECKING:
    from prismal.core.config_source import ConfigSourcePort

# Per-construction config source, set by ``build_settings`` and read by
# ``Settings.settings_customise_sources``. A ContextVar (not a plain global) so
# concurrent per-tenant ``build_settings(source)`` calls never clobber each
# other (SPEC-CSI-008 isolation).
_active_source: "ContextVar[ConfigSourcePort | None]" = ContextVar(
    "_prismal_active_config_source", default=None
)

# One crontab field: numbers, ranges, steps, lists, ``*``/``?`` and 3-letter names.
_CRON_FIELD_RE = re.compile(r"[\d*?/,\-A-Za-z]+")


class _ConfigSourceSettingsSource(EnvSettingsSource):
    """Adapt a :class:`ConfigSourcePort` into a Pydantic settings source.

    Subclasses ``EnvSettingsSource`` and overrides only the raw key/value
    loading so Pydantic's prefix handling, ``validation_alias`` /
    ``AliasChoices`` resolution, case-insensitivity and JSON decoding of complex
    fields (``list[str]``, …) all keep working — the only thing that changes is
    *where the raw strings come from* (the injected port instead of
    ``os.environ``). ``SecretStr`` values from the source are unwrapped to their
    plaintext so the env decoder can coerce them; ``Settings`` re-wraps secret
    fields as ``SecretStr``.
    """

    def __init__(self, settings_cls: type[BaseSettings], mapping: Mapping[str, Any]) -> None:
        self._port_mapping: dict[str, str] = {
            key: (value.get_secret_value() if isinstance(value, SecretStr) else str(value))
            for key, value in mapping.items()
        }
        super().__init__(settings_cls)

    def _load_env_vars(self) -> Mapping[str, str | None]:
        return parse_env_vars(
            self._port_mapping,
            self.case_sensitive,
            self.env_ignore_empty,
            self.env_parse_none_str,
        )


class MaintenanceSettings(BaseSettings):
    """Package security and maintenance configuration (SPEC-031/032).

    All fields are readable from environment variables with the
    ``PRISMAL_MAINTENANCE_`` prefix (e.g.
    ``PRISMAL_MAINTENANCE_CONFIRM=false``).
    """

    model_config = SettingsConfigDict(
        env_prefix="PRISMAL_MAINTENANCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    confirm: bool = Field(
        default=True,
        description=(
            "Require interactive confirmation before applying any package update. "
            "Set to false for CI/CD pipelines (PRISMAL_MAINTENANCE_CONFIRM=false)."
        ),
    )
    backup_dir: str = Field(
        default="data/backups",
        description=(
            "Directory for pyproject.toml backups created before each write. Created on demand."
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


def is_loopback_url(url: str) -> bool:
    """True when *url*'s host is ``localhost`` or a loopback IP literal (no DNS lookup)."""
    import ipaddress
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Settings(BaseSettings):
    """
    Prismal application settings.

    All fields can be overridden via environment variables using the PRISMAL_ prefix
    (e.g. PRISMAL_DEFAULT_MODEL=gpt-4o).
    """

    model_config = SettingsConfigDict(
        env_prefix="PRISMAL_",
        # NOTE: ``env_file`` is intentionally NOT set (Phase W). All env / .env
        # reading is funnelled through the injected ``ConfigSourcePort`` (the
        # default ``EnvConfigSource`` reproduces the old ``.env`` behaviour).
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        # Let tests and callers pass either the field name (``default_model``)
        # or the declared alias (``PRISMAL_DEFAULT_MODEL``) as init kwargs.
        # Without this, adding a ``validation_alias`` silently breaks any
        # code that constructs ``Settings(default_model=...)`` directly.
        populate_by_name=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,  # noqa: ARG003 — replaced by the port
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003 — replaced by the port
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003 — replaced
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Plug the injected ``ConfigSourcePort`` as the configuration source (Phase W).

        Precedence: init kwargs > injected ``ConfigSourcePort`` > nothing. The
        native ``env_settings`` / ``dotenv_settings`` are dropped — all env / file
        reading is funnelled through the source (the default ``EnvConfigSource``
        preserves today's resolution). When no source is active (e.g. a bare
        ``Settings()`` not routed through ``build_settings``), the default source
        is used, keeping the ~151 existing call sites byte-for-byte compatible.
        """
        from prismal.core.config_source import get_config_source

        source = _active_source.get() or get_config_source()
        if source is None:
            from prismal.core.config_source import _default_source

            source = _default_source()
        mapping = source.load()
        return (init_settings, _ConfigSourceSettingsSource(settings_cls, mapping))

    # ── LLM ──────────────────────────────────────────────────────────
    llm_provider: str = Field(
        default="",
        description=(
            "High-level LLM provider selector. When non-empty, "
            "``default_model`` / ``fallback_model`` are auto-derived from "
            "the provider map unless the user also pinned a compatible "
            "explicit model.  Accepted values (case-insensitive): "
            "'' (legacy / no override), 'anthropic', 'openai', 'google', "
            "'ollama'.  Set via PRISMAL_LLM_PROVIDER."
        ),
    )
    default_model: str = Field(
        default="claude-sonnet-4-5",
        validation_alias=AliasChoices(
            "PRISMAL_DEFAULT_MODEL",
            "PRISMAL_MODEL",
        ),
        description=(
            "Default LLM model (provider/model-id format for LiteLLM). "
            "PRISMAL_MODEL is accepted as an alias for convenience."
        ),
    )
    fallback_model: str = Field(
        default="gpt-4o-mini",
        description=(
            "Fallback model if primary is unavailable.  Empty string "
            "disables the fallback wrapper entirely (use for single-"
            "provider setups like Ollama-only)."
        ),
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description=(
            "Base URL of the local Ollama server.  Exported to the "
            "environment as OLLAMA_API_BASE so LiteLLM picks it up when "
            "routing ``ollama/*`` model strings."
        ),
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
    llm_ollama_timeout_seconds: int = Field(
        default=300,
        ge=1,
        description=(
            "Timeout in seconds for calls routed to an Ollama model "
            "(``ollama/*`` or ``ollama_chat/*``).  Local models are "
            "typically far slower than cloud providers — 60 s is not "
            "enough for a 7B-parameter+ model on CPU — so Ollama gets a "
            "generous override that takes effect automatically."
        ),
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
            "PRISMAL_ANTHROPIC_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        description=("Anthropic API key (PRISMAL_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY)"),
    )
    openai_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "PRISMAL_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        ),
        description="OpenAI API key (PRISMAL_OPENAI_API_KEY or OPENAI_API_KEY)",
    )
    google_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "PRISMAL_GOOGLE_API_KEY",
            "GOOGLE_API_KEY",
        ),
        description="Google AI API key (PRISMAL_GOOGLE_API_KEY or GOOGLE_API_KEY)",
    )
    tavily_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "PRISMAL_TAVILY_API_KEY",
            "TAVILY_API_KEY",
        ),
        description=(
            "Tavily web-search API key (PRISMAL_TAVILY_API_KEY or TAVILY_API_KEY). "
            "Relocated from a direct os.environ read in agents/tools.py (Fase W)."
        ),
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

    # ── Maintenance (read-only property — env prefix PRISMAL_MAINTENANCE_) ──
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
            "(same layout as prismal/skills/available/). "
            "Set via env var as a JSON array: "
            "PRISMAL_EXTERNAL_SKILLS_DIRS='[\"/home/user/.agents/skills\"]'"
        ),
    )

    # ── Souls / Kokoro (Fase K, opt-in — SPEC-KOK-CFG-001) ───────────
    kokoro_enabled: bool = Field(
        default=False,
        description="Master opt-in toggle for the Kokoro deliberation layer.",
    )
    souls_dir: str = Field(
        default="",
        description=(
            "Root of the souls tiers (available/active/custom). "
            "Empty string uses the packaged prismal/souls directory."
        ),
    )
    kokoro_souls: list[str] = Field(
        default_factory=lambda: ["spirit", "mind", "heart"],
        min_length=3,
        max_length=3,
        description=(
            "The three soul ids the Kokoro judge convenes. "
            "Set via env var as a JSON array: "
            'PRISMAL_KOKORO_SOULS=\'["spirit", "mind", "heart"]\''
        ),
    )
    kokoro_max_rounds: int = Field(
        default=2,
        ge=1,
        description="Hard cap on Kokoro deliberation rounds.",
    )
    kokoro_agreement_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Early-stop agreement score (0-1) for the deliberation.",
    )
    kokoro_execute_actions: bool = Field(
        default=False,
        description=(
            "Allow the Kokoro judge to execute one tool action (gated by ActionInterceptor)."
        ),
    )
    kokoro_judge_model: str = Field(
        default="",
        description="Optional judge model override (empty = default_model).",
    )
    soul_max_body_chars: int = Field(
        default=20_000,
        ge=1,
        description="Max SOUL.md body length in characters (sanitizer cap).",
    )

    # ── Skynet swarm supervisor (Fase S, opt-in — SPEC-SKY-CFG-001) ───
    skynet_enabled: bool = Field(
        default=False,
        description="Master opt-in toggle for the Skynet swarm supervisor.",
    )
    skynet_swarm_size: int = Field(
        default=0,
        ge=0,
        description=(
            "Swarm sizing mode: 0 = dynamic (the supervisor chooses N from "
            "the goal); >0 = fixed N sub-orders (load-balanced)."
        ),
    )
    skynet_max_swarm: int = Field(
        default=8,
        ge=1,
        description=(
            "Hard cap on workers per round. Clamped to parallel_max_workers "
            "(the operator-visible ceiling always wins)."
        ),
    )
    skynet_max_rounds: int = Field(
        default=3,
        ge=1,
        description="Hard cap on plan→dispatch→evaluate iterations.",
    )
    skynet_reduce_strategy: Literal["synthesis", "concat", "first_success"] = Field(
        default="synthesis",
        description=(
            "How worker outputs merge: synthesis (LLM combine), concat "
            "(deterministic join), or first_success (earliest success)."
        ),
    )
    skynet_worker_model: str = Field(
        default="",
        description="Optional worker model override (empty = default_model).",
    )
    skynet_planner_model: str = Field(
        default="",
        description="Optional planner/evaluator model override (empty = default_model).",
    )
    skynet_token_budget: int = Field(
        default=0,
        ge=0,
        description="0 = unlimited; >0 = soft per-run token budget.",
    )

    # ── Skynet S+ (Fase S+, opt-in — SPEC-SP-CFG-001) ─────────────────
    skynet_specialists_enabled: bool = Field(
        default=False,
        description=(
            "S+1: assign roles + per-role model/persona/tools to workers "
            "(off = homogeneous Phase-S swarm)."
        ),
    )
    skynet_roles_path: str = Field(
        default="config/skynet_roles.yaml",
        description="Path to the specialist RoleRegistry YAML file.",
    )
    skynet_remote_workers_enabled: bool = Field(
        default=False,
        description=(
            "S+3: allow a role bound to a remote A2A agent to run remotely "
            "(also requires a2a_enabled)."
        ),
    )
    skynet_remote_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "fnmatch allowlist of A2A card URLs for remote workers (empty + strict = deny-all)."
        ),
    )

    # ── Cost & Budget Governance (Phase C — SPEC-CST-CFG-001) ─────────
    budget_enabled: bool = Field(
        default=False,
        description="Master opt-in for the cost/budget governance layer.",
    )
    budget_max_tokens: int = Field(
        default=0,
        ge=0,
        description="Per-run token ceiling (0 = unlimited).",
    )
    budget_max_cost_usd: float = Field(
        default=0.0,
        ge=0.0,
        description="Per-run estimated USD ceiling (0 = unlimited).",
    )
    budget_max_calls: int = Field(
        default=0,
        ge=0,
        description="Per-run LLM call ceiling (0 = unlimited).",
    )
    budget_max_wall_clock_s: float = Field(
        default=0.0,
        ge=0.0,
        description="Per-run wall-clock ceiling in seconds (0 = unlimited).",
    )
    budget_scope: str = Field(
        default="turn",
        description="Budget accounting scope: turn | session | tenant.",
    )
    budget_soft_ratio: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Fraction of a limit at which a soft cap (degrade) fires.",
    )
    budget_hard_cap: bool = Field(
        default=True,
        description="True = abort (raise) on a hard cap; False = soft-only (warn).",
    )
    budget_tenant_window: str = Field(
        default="month",
        description=(
            "UTC calendar window of a tenant-scoped budget (budget_scope=tenant): month | day."
        ),
    )
    budget_tenant_store_path: str = Field(
        default="",
        description=(
            "SQLite file shared by every process for tenant quota totals "
            '(budget_scope=tenant); "" keeps the totals in process memory.'
        ),
    )
    budget_pricing: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description=(
            "Fallback per-model pricing for models LiteLLM cannot price: "
            '{"model": {"input": usd_per_1k, "output": usd_per_1k}}.'
        ),
    )

    # ── Evaluation harness (Phase V — SPEC-EVL-CFG-001) ───────────────
    eval_default_mode: str = Field(
        default="fakes",
        description="Default eval execution mode: fakes | live_api.",
    )
    eval_judge_model: str = Field(
        default="",
        description="Optional LLM-judge model override ('' = provider default).",
    )
    eval_regression_tolerance: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description="Per-metric regression tolerance for the gate.",
    )
    eval_seed: int = Field(
        default=0,
        ge=0,
        description="Global seed for reproducible eval runs.",
    )
    eval_langfuse_export: bool = Field(
        default=False,
        description="Export scorecards to Langfuse evals.",
    )

    # ── Runtime Hardening (Phase H — SPEC-HRD-CFG-001) ────────────────
    hardening_enabled: bool = Field(
        default=False,
        description="Master opt-in for the runtime-hardening layer.",
    )
    hardening_mode: str = Field(
        default="warn",
        description="Global default control mode: off | warn | enforce.",
    )
    taint_tracking_enabled: bool = Field(
        default=True,
        description="Mark untrusted content (only effective if hardening_enabled).",
    )
    hardening_injection_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Risk threshold (0-1) to flag/block indirect injection.",
    )
    hardening_injection_classifier: bool = Field(
        default=False,
        description="Use the optional LLM injection classifier (metered via Budget).",
    )
    output_validation_enabled: bool = Field(
        default=True,
        description="Validate/escape model outputs before use (if hardening_enabled).",
    )
    tool_policy_path: str = Field(
        default="config/tool_policies.yaml",
        description="Path to the declarative tool-policy YAML file.",
    )
    hardening_tool_policy_default: str = Field(
        default="allow",
        description="Default effect when no policy rule matches: allow | deny.",
    )
    hardening_runaway_max_steps: int = Field(
        default=40,
        ge=0,
        description="Hard step cap per run (0 = unlimited).",
    )
    hardening_runaway_stagnation_window: int = Field(
        default=4,
        ge=0,
        description="Identical-signature window that triggers a runaway stop.",
    )
    hardening_pii_output: bool = Field(
        default=False,
        description="Redact PII from agent outputs.",
    )

    # ── Node I/O Type-Safety (Phase NTS — SPEC-NTS-CFG-001) ────────────
    node_typesafety_enabled: bool = Field(
        default=False,
        description="Master opt-in for per-node I/O schema validation (Phase NTS). "
        "When False, node_io_validation_middleware is a pure passthrough and the "
        "compiled supervisor graph is byte-for-byte unchanged.",
    )
    node_typesafety_mode: str = Field(
        default="warn",
        description="Global default control mode: off | warn | enforce.",
    )

    # ── Observability integration (Phase OBS — SPEC-OBS-CFG-001) ──────
    observability_enabled: bool = Field(
        default=False,
        description="Master opt-in for the observability-integration layer (Phase OBS). "
        "When False, RuntimeContext.observability is None and every existing "
        "OTel/Langfuse call site is byte-for-byte unchanged.",
    )
    observability_run_buffer_size: int = Field(
        default=200,
        gt=0,
        description="Max spans/tool-calls retained per run in the default adapter's ring buffer.",
    )
    observability_max_runs: int = Field(
        default=500,
        gt=0,
        description="Max concurrent run entries retained by the default adapter "
        "before LRU eviction.",
    )
    observability_score_source_default: str = Field(
        default="system",
        description="Default source for record_score when omitted: human | llm_judge | system.",
    )
    observability_dataset_export_format: str = Field(
        default="langsmith",
        description="Default fmt for export_dataset when omitted: langsmith | langfuse.",
    )

    # ── Agent identity & access governance (Phase IDN — SPEC-IDN-CFG-001) ──
    identity_enabled: bool = Field(
        default=False,
        description="Master opt-in for the identity & access governance layer.",
    )
    identity_mode: str = Field(
        default="warn",
        description="Policy default control mode: off | warn | enforce.",
    )
    identity_provider: str = Field(
        default="local",
        description="Identity backend: local (did:key) | oidc (Entra/Okta).",
    )
    identity_did_method: str = Field(
        default="key",
        description="DID method: key (internal, offline) | web (A2A-exposed).",
    )
    identity_did_web_domain: str = Field(
        default="",
        description="Domain used to issue did:web identifiers (A2A).",
    )
    identity_vault: str = Field(
        default="env",
        description="Credential vault backend: env (ConfigSourcePort) | file.",
    )
    identity_policy_path: str = Field(
        default="config/identity_policies.yaml",
        description="Path to the declarative identity-policy YAML file.",
    )
    identity_on_behalf_enabled: bool = Field(
        default=False,
        description="Enable OAuth on-behalf-of delegation tokens.",
    )
    identity_on_behalf_ttl_s: int = Field(
        default=900,
        ge=1,
        description="On-behalf token time-to-live in seconds.",
    )
    oidc_issuer: str = Field(
        default="",
        description="OIDC issuer URL (required when identity_provider=oidc).",
    )
    oidc_client_id: str = Field(
        default="",
        description="OIDC client id for the on-behalf adapter (host-supplied).",
    )

    # ── A2A interop (Phase I, opt-in — SPEC-A2A-007) ──────────────────
    a2a_enabled: bool = Field(
        default=False,
        description="Master opt-in for the A2A (Agent2Agent) interop layer.",
    )
    a2a_inbound_enabled: bool = Field(
        default=False,
        description="Expose prismal as an A2A agent (Agent Card + JSON-RPC/SSE handler).",
    )
    a2a_outbound_enabled: bool = Field(
        default=False,
        description="Allow prismal to delegate to remote A2A agents (client/node/tools).",
    )
    a2a_base_url: str | None = Field(
        default=None,
        description="Public A2A endpoint advertised in the Agent Card (inbound).",
    )
    a2a_published_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Allowlist of skill/subgraph ids to publish on the Agent Card "
            "(empty = a high-level default subset)."
        ),
    )
    a2a_outbound_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "Allowed remote agent hosts/patterns for outbound delegation "
            "(fnmatch wildcards, e.g. '*.trusted.org'). Empty + strict = deny-all."
        ),
    )
    a2a_strict: bool = Field(
        default=True,
        description=(
            "Strict mode: require declared auth and enforce a deny-all allowlist "
            "when a2a_outbound_allowlist is empty."
        ),
    )

    # ── Guardrails Modernization (Phase GRD — SPEC-GRD-CFG-001) ───────
    nemo_classifier_enabled: bool = Field(
        default=False,
        description="Master opt-in for the reasoning safety-classifier rail.",
    )
    nemo_classifier_model: str | None = Field(
        default=None,
        description="Optional model override for the classifier judgment call.",
    )
    nemo_classifier_categories: list[str] = Field(
        default_factory=lambda: [
            "violence",
            "self_harm",
            "illegal_activities",
            "pii_request",
            "competitor_disparagement",
        ],
        description="Configurable safety-classifier category set.",
    )
    nemo_classifier_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Confidence threshold to treat a classifier verdict as a block.",
    )
    nemo_classifier_timeout_seconds: float = Field(
        default=3.0,
        ge=0.0,
        description=(
            "Independent timeout for the classifier action only (DD-GRD-003) — "
            "never affects the separate 450ms dialog-rail timeout."
        ),
    )
    structured_output_guard_enabled: bool = Field(
        default=False,
        description="Master opt-in for StructuredOutputGuard.",
    )
    structured_output_guard_max_reasks: int = Field(
        default=2,
        ge=0,
        description="Bound on automatic re-ask attempts (0 = validate once, no re-ask).",
    )
    structured_output_guard_hub_validators_enabled: bool = Field(
        default=False,
        description="Master gate for Guardrails Hub validators.",
    )

    # ── Loop Hardening (Phase LH — SPEC-LH-CFG-001) ───────────────────
    context_compaction_enabled: bool = Field(
        default=False,
        description="Master opt-in for persisted-context compaction.",
    )
    context_compaction_strategy: str = Field(
        default="truncate",
        description="Compaction strategy: truncate (no LLM call) | summarize (opt-in).",
    )
    context_compaction_max_messages: int = Field(
        default=60,
        ge=0,
        description="Raw message-count trigger threshold.",
    )
    context_compaction_token_threshold: int = Field(
        default=0,
        ge=0,
        description="Cumulative-token trigger via Budget's CostMeter (0 = disabled).",
    )
    context_compaction_keep_recent: int = Field(
        default=10,
        ge=0,
        description="Number of most-recent messages always kept verbatim.",
    )
    context_compaction_summarizer_model: str | None = Field(
        default=None,
        description="Model id for the summarize strategy; None uses the provider default.",
    )
    context_compaction_min_interval_messages: int = Field(
        default=20,
        ge=0,
        description="Minimum new messages since the last compaction before compacting again.",
    )
    tool_gating_enabled: bool = Field(
        default=False,
        description="Master opt-in for dynamic tool gating by task phase.",
    )
    tool_gating_phase_map_path: str = Field(
        default="config/tool_gating_phases.yaml",
        description="Phase -> capability-override table path.",
    )

    # --- TKE --- Token Efficiency (Phase TKE — SPEC-TKE-CFG-001) ────────
    # Every sub-flag is effective only when token_efficiency_enabled=True;
    # master off ⇒ every pre-TKE behaviour is byte-for-byte unchanged.
    token_efficiency_enabled: bool = Field(
        default=False,
        description="Master opt-in for the token-efficiency layer (Phase TKE).",
    )
    prompt_cache_enabled: bool = Field(
        default=False,
        description="Apply provider prompt-cache policy (cache_control / prompt_cache_key).",
    )
    prompt_cache_ttl: str = Field(default="5m", description="Prompt-cache TTL: 5m | 1h.")
    prompt_cache_scope: str = Field(
        default="tenant",
        description="Prompt-cache prefix scope: tenant (tenant id in prefix hash) | global.",
    )
    prompt_cache_policy_path: str = Field(
        default="config/prompt_cache.yaml",
        description="Volatile provider cache facts (breakpoints, lookback, min prefix).",
    )
    prompt_canary_secret: SecretStr = Field(
        default=SecretStr(""),
        description="HMAC secret for the cache-stable prefix canary (never logged/mirrored).",
    )
    prompt_layout: str = Field(
        default="legacy",
        description="SecurePromptBuilder default layout: legacy | cache_stable.",
    )
    tool_gating_mode: str = Field(
        default="remove",
        description="LH tool gating mode: remove (LH2 exactly) | mask (cache-safe allow-set).",
    )
    tool_defer_enabled: bool = Field(default=False, description="Deferred tool loading.")
    tool_defer_threshold: int = Field(
        default=30, ge=1, description="Tool count above which non-core tools are deferred."
    )
    tool_defer_core: list[str] = Field(
        default_factory=list, description="Tool names always exposed when deferring."
    )
    context_mask_keep_turns: int = Field(
        default=10, ge=0, description="Recent human/AI turns whose observations stay unmasked."
    )
    context_mask_relevance_keep: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Observations scoring >= this (relevance_fn) stay unmasked.",
    )
    context_compaction_high_water: int = Field(
        default=0, ge=0, description="Hysteresis high-water token mark (0 = LH trigger)."
    )
    context_compaction_low_water: int = Field(
        default=0, ge=0, description="Hysteresis low-water token mark."
    )
    artifact_store_backend: str = Field(
        default="memory", description="Artifact store backend: memory | sqlite."
    )
    artifact_store_path: str = Field(
        default="data/artifacts.db", description="SQLite artifact store path."
    )
    artifact_ttl_s: int = Field(default=86400, ge=0, description="Artifact TTL in seconds.")
    tool_output_budget_enabled: bool = Field(
        default=False, description="Cap tool outputs reaching messages (head+tail excerpt)."
    )
    tool_output_max_tokens_default: int = Field(
        default=2000, ge=0, description="Default per-tool output budget (estimated tokens)."
    )
    tool_output_max_tokens_by_tool: dict[str, int] = Field(
        default_factory=dict, description="Per-tool output budget overrides."
    )
    tool_output_toon_enabled: bool = Field(
        default=False, description="Encode uniform tabular tool/RAG results as TOON."
    )
    llm_effort_enabled: bool = Field(
        default=False, description="Per-agent reasoning effort / max_tokens."
    )
    llm_effort_profile: str = Field(
        default="none", description="Effort profile: none | recommended."
    )
    llm_effort_by_agent: dict[str, str] = Field(
        default_factory=dict, description="Agent -> reasoning effort (low|medium|high)."
    )
    llm_max_tokens_by_agent: dict[str, int] = Field(
        default_factory=dict, description="Agent -> max output tokens."
    )
    handoff_envelope_enabled: bool = Field(
        default=False, description="Multi-agent handoff envelopes (summary + artifact refs)."
    )
    handoff_summary_max_tokens: int = Field(
        default=1500, ge=0, description="Envelope summary cap (estimated tokens)."
    )
    rag_context_budget_enabled: bool = Field(
        default=False, description="Fit retrieved RAG context to a token budget."
    )
    rag_context_budget_tokens: int = Field(
        default=3000, ge=0, description="RAG context budget (estimated tokens)."
    )
    rag_retrieve_k: int = Field(default=20, ge=1, description="Chunks retrieved before budget.")
    rag_rerank_enabled: bool = Field(
        default=False, description="Cross-encoder rerank ([rerank] extra)."
    )
    rag_passage_compression_enabled: bool = Field(
        default=False, description="LLMLingua-2 prose compression ([compression] extra)."
    )
    codeact_mcp_code_mode_enabled: bool = Field(
        default=False, description="Code-mode for MCP chains (TKE10, optional P3)."
    )

    # --- GW --- LLM Gateway Providers (Phase GW — SPEC-GW-CFG-001) ───────
    # Every gateway is effective only when llm_gateways_enabled=True plus its own
    # *_enabled flag; master off ⇒ get_llm() kwargs and the graph equal v3.13.0.
    llm_gateways_enabled: bool = Field(
        default=False, description="Master opt-in for LLM gateway providers (Phase GW)."
    )
    llm_gateway_agent_map: dict[str, str] = Field(
        default_factory=dict, description="Agent name → 'gw:<gateway>/<model>' reference."
    )
    llm_gateway_openai_compatible: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Generic gateways: name → {base_url, auth_ref?, headers?, "
        "owns_model_fallback?}.",
    )
    llm_gateway_tenant_key_fallback: bool = Field(
        default=False,
        description="Allow the global key when a tenant key is missing (bills the operator).",
    )
    openrouter_enabled: bool = Field(default=False, description="Enable OpenRouter.")
    openrouter_api_key: SecretStr = Field(default=SecretStr(""), description="OpenRouter key.")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", description="OpenRouter API base URL."
    )
    openrouter_app_url: str = Field(default="", description="HTTP-Referer attribution header.")
    openrouter_app_title: str = Field(
        default="prismal", description="X-OpenRouter-Title attribution header."
    )
    openrouter_app_categories: list[str] = Field(
        default_factory=list, description="X-OpenRouter-Categories attribution header."
    )
    openrouter_session_stickiness: bool = Field(
        default=True, description="Send a hashed session_id for sticky provider routing."
    )
    openrouter_plugins: list[str] = Field(
        default_factory=list, description="Allowed: response-healing, context-compression, pdf."
    )
    openrouter_auto_allowed_models: list[str] = Field(
        default_factory=list, description="openrouter/auto model wildcards (e.g. anthropic/*)."
    )
    openrouter_auto_tier: str | None = Field(
        default=None, description="openrouter/auto cost tier: low|medium|high|xhigh|max."
    )
    openrouter_require_zdr: bool = Field(
        default=True, description="provider.data_collection='deny' + provider.zdr=true."
    )
    openrouter_provider_sort: str | None = Field(
        default=None, description="provider.sort: price | throughput | latency."
    )
    openrouter_max_price: dict[str, float] = Field(
        default_factory=dict, description="provider.max_price {prompt, completion} USD/M."
    )
    openrouter_only: list[str] = Field(default_factory=list, description="provider.only.")
    openrouter_ignore: list[str] = Field(default_factory=list, description="provider.ignore.")
    openrouter_fallback_models: list[str] = Field(
        default_factory=list, description="Gateway-owned model fallback (extra_body.models)."
    )
    openrouter_allowed_variants: list[str] = Field(
        default_factory=lambda: ["floor", "nitro", "exacto"],
        description="Allowed ':<variant>' suffixes (free/batch are opt-in).",
    )
    openrouter_allow_auto: bool = Field(
        default=False, description="Allow openrouter/auto (hands model choice to the gateway)."
    )
    openrouter_systemone_base_url: str | None = Field(
        default=None, description="Forward reference for DEC (SystemOne endpoint)."
    )
    opencode_zen_enabled: bool = Field(default=False, description="Enable OpenCode Zen.")
    opencode_go_enabled: bool = Field(default=False, description="Enable OpenCode Go.")
    opencode_api_key: SecretStr = Field(default=SecretStr(""), description="OpenCode key.")
    opencode_zen_base_url: str = Field(
        default="https://opencode.ai/inference/v1", description="OpenCode Zen API base URL."
    )
    opencode_go_base_url: str = Field(
        default="https://opencode.ai/inference/go/v1", description="OpenCode Go API base URL."
    )
    opencode_model_protocols_path: str = Field(
        default="config/opencode_models.yaml", description="Per-model wire-protocol map."
    )
    opencode_refresh_models: bool = Field(
        default=False, description="Refresh the protocol map from /v1/models at startup."
    )
    opencode_go_allowed_agents: list[str] = Field(
        default_factory=lambda: ["coder", "codeact_agent", "dev_pipeline", "code_review"],
        description="Agents allowed on OpenCode Go (ToS: coding agents only).",
    )
    opencode_denied_models: list[str] = Field(
        default_factory=lambda: [
            "grok*",
            "muse-spark*",
            "deepseek-v4-flash*",
            # Free tiers whose data may train the upstream model, or NVIDIA
            # "trial use only" endpoints (VERIFY.md GW0-06, 2026-10-12).
            "big-pickle",
            "exo-free",
            "mimo-*-free",
            "ling-*-free",
            "nemotron-*-free",
        ],
        description="Retention/training deny-list (fnmatch, case-insensitive) — VERIFY.md GW0-06.",
    )
    opencode_go_monthly_cap_usd: float = Field(
        default=0.0, ge=0.0, description="Go monthly allowance in USD; 0 disables the ledger."
    )
    opencode_go_window_5h_ratio: float = Field(default=0.20, description="5 h window ratio.")
    opencode_go_window_week_ratio: float = Field(default=0.50, description="Weekly ratio.")
    opencode_go_quota_action: str = Field(
        default="refuse", description="On an exhausted window: refuse | downgrade."
    )
    opencode_go_downgrade_model: str | None = Field(
        default=None, description="gw: ref used when the action is downgrade."
    )
    omniroute_enabled: bool = Field(default=False, description="Enable OmniRoute.")
    omniroute_api_key: SecretStr = Field(default=SecretStr(""), description="OmniRoute key.")
    omniroute_base_url: str = Field(
        default="http://localhost:20128/v1", description="OmniRoute API base URL."
    )
    omniroute_min_version: str = Field(
        default="3.8.51", description="CVE-2026-88062 floor (VERIFY.md GW0-07)."
    )
    omniroute_allow_unknown_version: bool = Field(
        default=False, description="Allow an OmniRoute whose version cannot be determined."
    )
    omniroute_declared_version: str | None = Field(
        default=None, description="Operator-declared version when detection finds none."
    )
    omniroute_upstream_attestation: str | None = Field(
        default=None, description="Mandatory: api_keys_only | subscription_oauth."
    )
    omniroute_allow_subscription_upstreams: bool = Field(
        default=False, description="Allow subscription-OAuth upstreams (ToS-violating)."
    )
    omniroute_denied_combos: list[str] = Field(
        default_factory=list, description="Model/combo deny-list (fnmatch)."
    )
    gateway_reconcile_enabled: bool = Field(
        default=False, description="Reconcile reported cost against the generation API."
    )
    gateway_reconcile_delay_s: float = Field(
        default=2.0,
        ge=0.0,
        description="Seconds the CostReconciler waits before fetching billed cost.",
    )
    gateway_quota_poll_interval_s: int = Field(
        default=300, ge=1, description="Interval of the GatewayQuotaPoller, in seconds."
    )

    # --- DEC --- Typed Decision Models (Phase DEC — SPEC-DEC-CFG-001) ────
    # Every integration is effective only with decisions_enabled=True plus its own
    # decision_<integration>_enabled flag; enabled without a mode ⇒ shadow.
    # Volatile vendor facts ([verify]) live here, never in code.
    decisions_enabled: bool = Field(
        default=False, description="Master opt-in for typed decision models (Phase DEC)."
    )
    decision_routing_enabled: bool = Field(default=False, description="DEC-ROUTE (INT-001).")
    decision_eval_judge_enabled: bool = Field(default=False, description="DEC-EVAL (INT-002).")
    decision_hitl_enabled: bool = Field(default=False, description="DEC-HITL (INT-003).")
    decision_tool_proposal_enabled: bool = Field(default=False, description="DEC-TOOL (INT-004).")
    decision_rag_relevance_enabled: bool = Field(default=False, description="DEC-RAG (INT-005).")
    decision_ctx_relevance_enabled: bool = Field(default=False, description="DEC-CTX (INT-006).")
    decision_swarm_eval_enabled: bool = Field(default=False, description="DEC-SWARM (INT-007).")
    decision_safety_prescreen_enabled: bool = Field(
        default=False, description="DEC-SAFE (INT-008)."
    )
    decision_backend_order: list[str] = Field(
        default_factory=lambda: ["rule", "ollama", "jev", "llm"],
        description="Cascade tiers, tried in order (rule | ollama | laya | jev | llm).",
    )
    decision_modes: dict[str, str] = Field(
        default_factory=dict,
        description="Integration → off | shadow | enforce (enabled without entry ⇒ shadow).",
    )
    jev_base_url: str = Field(
        default="https://api.typesafe.ai/v1",
        description="Jev-compatible base URL: TypeSafe, OpenRouter or laya-serve.",
    )
    jev_api_key: SecretStr | None = Field(default=None, description="Jev bearer key.")
    jev_model: str = Field(default="jev-1.13.0", description="Pinned Jev model (no *latest*).")
    jev_transport: str = Field(default="http", description="http | sdk.")
    jev_max_choice_options: int = Field(default=255, ge=2, description="Jev Choice option cap.")
    jev_max_request_tokens: int = Field(default=64_000, ge=1, description="Jev request limit.")
    laya_mode: str = Field(default="local", description="local (in-process) | http (laya-serve).")
    laya_checkpoint: str = Field(default="laya-multilingual", description="Laya checkpoint.")
    laya_base_url: str | None = Field(default=None, description="laya-serve base URL.")
    laya_api_key: SecretStr | None = Field(default=None, description="LAYA_API_KEY bearer.")
    laya_preload: bool = Field(default=False, description="Load Laya at startup (servers).")
    laya_max_loaded: int = Field(default=2, ge=1, description="Max checkpoints kept loaded.")
    laya_max_len: int = Field(default=1024, ge=1, description="Laya checkpoint context tokens.")
    laya_max_choice_options: int = Field(
        default=20, ge=2, description="Laya quality cap on Choice options."
    )
    laya_cuda_amp: str | None = Field(
        default=None, description="Mirror of the host's LAYA_CUDA_AMP (fp16 required on GPU)."
    )
    decision_timeout_ms: int = Field(default=800, ge=1, description="Non-LLM tier budget (ms).")
    decision_calibration_dir: str = Field(
        default="config/decision_calibration", description="<decision_id>@<model>.json files."
    )
    decision_languages: list[str] = Field(
        default_factory=lambda: ["en", "es"], description="Languages allowed to enforce."
    )
    decision_min_calibration_examples: int = Field(
        default=300,
        ge=1,
        description="Minimum calibration examples per language slice to allow enforce.",
    )
    decision_max_ece: float = Field(
        default=0.05,
        gt=0.0,
        le=1.0,
        description="Maximum expected calibration error for an enforce-mode record.",
    )
    decision_tau_overrides: dict[str, float] = Field(
        default_factory=dict, description="Raise-only τ per risk tier (low|medium|high)."
    )
    decision_min_margin: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Minimum top-1 vs top-2 margin a decision needs to pass the gate.",
    )
    decision_shadow_sample_rate: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Fraction of shadow-mode calls compared against the legacy path.",
    )
    decision_eval_audit_rate: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Fraction of typed eval-judge decisions re-judged by the LLM.",
    )
    decision_auto_demote: bool = Field(default=True, description="SHD-002 drift auto-demote.")
    decision_demote_floor: float = Field(
        default=0.95, ge=0.0, le=1.0, description="Rolling audited agreement floor (SHD-002)."
    )
    decision_llm_fallback_model: str | None = Field(
        default=None, description="Model for the terminal llm tier (None = provider default)."
    )
    # Ollama /v1/systemone backend (DEC delta — SPEC-DEC-OLL-001..011, CFG-001).
    decision_ollama_base_url: str | None = Field(
        default=None, description="Ollama base URL (None ⇒ ollama_base_url)."
    )
    decision_ollama_models: dict[str, str] = Field(
        default_factory=lambda: {"fast": "tev1:4b", "accurate": "nimble:9b-q4_K_M"},
        description="Sub-tier → Ollama decision model (fast | accurate; either may be omitted).",
    )
    decision_ollama_keep_alive: str = Field(
        default="30m", description="keep_alive sent on every request (OLL-006)."
    )
    decision_ollama_warmup: bool = Field(
        default=False, description="One warm-up request per model at runtime start (OLL-006)."
    )
    decision_ollama_allow_remote: bool = Field(
        default=False, description="Allow a non-loopback Ollama (no auth on Ollama, OLL-010)."
    )
    decision_ollama_accurate_max_p95_ms: int = Field(
        default=1000, ge=1, description="Per-turn use of the accurate tier above this p95: never."
    )
    decision_ollama_timeout_ms: int = Field(
        default=2000, ge=1, description="Own per-sub-tier budget (not part of decision_timeout_ms)."
    )
    decision_ollama_max_batch: int = Field(
        default=4, ge=1, le=64, description="Max questions per request on the accurate tier."
    )
    decision_ollama_min_version: str = Field(
        default="0.35.0", description="Minimum Ollama version serving /v1/systemone (OLL-002)."
    )
    # Concrete human-review questions (DEC delta — SPEC-DEC-OLL-012/014).
    decision_hitl_raise_threshold: float = Field(
        default=0.5, gt=0.0, le=1.0, description="P(true) at which a concrete question raises."
    )
    decision_hitl_raise_thresholds: dict[str, float] = Field(
        default_factory=dict, description="Per-question raise threshold (question id → P)."
    )
    decision_hitl_money_limit: float = Field(
        default=0.0, ge=0.0, description="Money moved above this needs review (0 ⇒ any amount)."
    )

    # --- AMR --- Adaptive Model Routing (Phase AMR — SPEC-AMR-CFG-001) ────
    # Every sub-feature is effective only with adaptive_routing_enabled=True;
    # flag off ⇒ get_llm() kwargs and the compiled graph equal 3.15.0.
    adaptive_routing_enabled: bool = Field(
        default=False, description="Master opt-in for adaptive model routing (Phase AMR)."
    )
    routing_mode: Literal["shadow", "enforce"] = Field(
        default="shadow",
        description="shadow computes choices + counterfactual savings but keeps the static model.",
    )
    routing_policy: Literal["static", "cascade", "budget_aware", "composite", "batch"] = Field(
        default="static", description="Top-level routing policy."
    )
    routing_tier_models: str = Field(
        default="config/routing_tiers.yaml",
        description="YAML with tier → model ref, agent → tier and the risk floor.",
    )
    routing_risk_floor: dict[str, str] = Field(
        default_factory=lambda: {"high": "medium", "critical": "frontier"},
        description="Minimum tier per risk tier (fail-closed, SPEC-AMR-SEC-001).",
    )
    routing_tier_capabilities: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "nano": [],
            "small": ["tools", "structured_output"],
            "medium": ["tools", "structured_output"],
            "frontier": ["tools", "structured_output"],
        },
        description="Capabilities per tier (tools | structured_output) used by the cascade.",
    )
    routing_cascade_start_tier: str = Field(default="small", description="Cascade entry tier.")
    routing_cascade_max_escalations: int = Field(default=2, ge=0, description="Escalation cap.")
    routing_cascade_tool_error_threshold: int = Field(
        default=2, ge=1, description="Tool errors in one call before escalating."
    )
    routing_adequacy_tau: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Adequacy Noul P(true) threshold."
    )
    routing_adequacy_timeout_ms: int = Field(
        default=800, ge=1, description="Adequacy check budget (ms); timeout ⇒ abstain."
    )
    routing_cache_affinity_enabled: bool = Field(
        default=True, description="Sticky model per (session, agent) after escalation."
    )
    routing_batch_alpha: float = Field(
        default=0.8, description="Mean predicted accuracy floor (alpha)."
    )
    routing_model_concurrency: dict[str, int] = Field(
        default_factory=dict,
        description="Per-model concurrency cap C_j (missing ⇒ parallel_max_workers).",
    )
    routing_batch_max_iter: int = Field(default=200, ge=1, description="Dual iterations.")
    routing_batch_eps: float = Field(
        default=0.01, gt=0.0, le=1.0, description="Relative duality-gap stop criterion."
    )
    routing_predictor_path: str | None = Field(
        default=None, description="Versioned predictor table (config/routing_predictor/*.json)."
    )
    plan_cache_enabled: bool = Field(default=False, description="Intent-keyed plan cache.")
    plan_cache_ttl_s: int = Field(default=86_400, ge=1, description="Plan cache TTL (s).")
    plan_cache_intents: list[str] = Field(
        default_factory=list, description="Read-only intent allowlist for the plan cache."
    )
    plan_cache_backend: Literal["memory", "sqlite"] = Field(
        default="memory", description="Plan cache backend."
    )
    plan_cache_path: str = Field(
        default="data/plan_cache.sqlite", description="SQLite plan cache file."
    )
    plan_cache_max_entries: int = Field(default=1024, ge=1, description="In-memory LRU size.")
    response_cache_enabled: bool = Field(default=False, description="Exact response cache.")
    batch_api_enabled: bool = Field(default=False, description="BatchExecutor (provider batches).")
    batch_poll_initial_s: float = Field(default=10.0, gt=0.0, description="First poll delay (s).")
    batch_poll_max_s: float = Field(default=300.0, gt=0.0, description="Poll delay cap (s).")
    batch_price_ratio: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        description="Batch price / on-demand price (verified in AMR0-03; ADR-WAVE-001 §2.3).",
    )

    # --- PRT --- Protocol Upgrades 2026 + Host Read Surface (SPEC-PRT-CFG-001, HRS) ──
    # Defaults keep A2A v0.3, the legacy MCP session path and no AG-UI: the graph,
    # the v0.3 Agent Card and the MCP request sequence are unchanged (GRD-001).
    a2a_protocol_version: Literal["0.3", "1.0"] = Field(
        default="0.3", description="Agent Card version + default inbound A2A stack."
    )
    a2a_accept_legacy: bool = Field(
        default=True, description="Accept v0.3 requests when v1 is the primary stack."
    )
    a2a_tasks_list_max_page_size: int = Field(
        default=200, ge=1, description="Upper bound for v1 tasks/list page size."
    )
    a2a_inbound_auth_schemes: list[str] = Field(
        default_factory=list, description="Security schemes advertised in the v1 Agent Card."
    )
    a2a_oauth_redirect_uri: str | None = Field(
        default=None, description="PKCE redirect URI (host callback); secrets live in the vault."
    )
    mcp_protocol_mode: Literal["auto", "legacy", "stateless"] = Field(
        default="legacy", description="MCP adapter selection (legacy = today's session path)."
    )
    mcp_stateless_protocol_version: str = Field(
        default="2026-07-28", description="Protocol version sent by the stateless MCP adapter."
    )
    mcp_tool_cache_enabled: bool = Field(default=False, description="TTL MCP tool-list cache.")
    mcp_tool_cache_default_ttl_s: int = Field(
        default=300, ge=1, description="Tool-list TTL when the server sends no ttlMs."
    )
    mcp_tool_cache_max_ttl_s: int = Field(default=86_400, ge=1, description="TTL ceiling.")
    mcp_tool_cache_max_entries: int = Field(default=256, ge=1, description="LRU bound.")
    mcp_tasks_enabled: bool = Field(default=False, description="MCP tasks extension.")
    mcp_task_poll_interval_s: float = Field(default=1.0, gt=0.0, description="First poll delay.")
    mcp_task_poll_max_interval_s: float = Field(
        default=15.0, gt=0.0, description="Poll delay ceiling."
    )
    mcp_task_timeout_s: float = Field(default=900.0, gt=0.0, description="Task wall-clock limit.")
    agui_events_enabled: bool = Field(
        default=False, description="Expose the AG-UI event translator to the host."
    )
    agui_include_state_deltas: bool = Field(default=True, description="Emit STATE_DELTA.")
    agui_include_usage: bool = Field(default=True, description="Emit prismal.usage on finish.")
    agui_include_metadata: bool = Field(
        default=True, description="Emit prismal.routing / prismal.decision CUSTOM events."
    )
    host_surface_stats_retention_h: int = Field(
        default=168, ge=1, description="Retention of the host-surface report recorders (h)."
    )

    # --- MEM --- Agentic Memory (Phase MEM — SPEC-MEM-CFG-001) ─────────────
    # Off ⇒ legacy memory paths, the graph and the scheduler are unchanged (SNP-001).
    # ``memory_v2_backend`` (not ``memory_backend``: the legacy LongTermMemoryStore owns
    # that key — MEM0-04).
    memory_v2_enabled: bool = Field(default=False, description="Master flag for memory v2.")
    memory_mode: Literal["shadow", "enforce"] = Field(
        default="shadow", description="shadow logs gate verdicts and stores as gate 'off'."
    )
    memory_v2_backend: Literal["legacy_long_term", "langgraph_store", "memory"] = Field(
        default="legacy_long_term", description="Storage adapter for memory v2."
    )
    memory_v2_data_dir: str = Field(
        default="",
        description=(
            "Directory of the per-tenant memory v2 SQLite files (backend + graph); empty = "
            "<legacy data dir>/memory_v2. Never the legacy memory.db (its expire/clear would "
            "bypass ForgetPolicy)."
        ),
    )
    memory_write_gate: Literal["off", "decision", "llm"] = Field(
        default="decision", description="Write gate mode."
    )
    memory_write_low_confidence: Literal["skip", "llm"] = Field(
        default="skip", description="Policy when the write decision abstains or is below tau."
    )
    memory_relation_k: int = Field(default=5, ge=1, description="Neighbors for relation typing.")
    memory_recall_budget_tokens: int = Field(default=1200, description="Hard recall token cap.")
    memory_recall_max_hops: int = Field(default=3, ge=1, description="Recall hop cap.")
    memory_sufficiency_tau: float = Field(default=0.8, description="Sufficiency threshold.")
    memory_consolidation_cron: str = Field(
        default="0 3 * * *", description="Consolidation schedule (crontab, per org)."
    )
    memory_consolidation_use_batch: bool = Field(
        default=True, description="Summarize through the AMR BatchExecutor when available."
    )
    memory_consolidation_max_items: int = Field(
        default=5000, ge=1, description="Records per consolidation run."
    )
    memory_retention_days_by_type: dict[str, int] = Field(
        default_factory=lambda: {"episodic": 90},
        description="Rule-based retention per memory type in days (0 = keep).",
    )
    memory_superseded_retention_days: int = Field(
        default=30, ge=0, description="Purge superseded records after N days (0 = keep)."
    )
    memory_graph_backend: Literal["none", "sqlite", "graphiti"] = Field(
        default="none", description="Temporal graph adapter."
    )
    memory_playbooks_enabled: bool = Field(default=False, description="Procedural memory.")
    memory_playbook_budget_tokens: int = Field(default=600, description="Playbook token cap.")
    memory_playbook_max_deltas: int = Field(default=10, ge=1, description="Deltas per cycle.")
    memory_playbook_min_stable_runs: int = Field(
        default=5, ge=1, description="Stable runs before the playbook enters the cached prefix."
    )
    memory_playbook_keep_versions: int = Field(
        default=10, ge=1, description="Playbook versions kept for rollback."
    )

    # ── Blind Review Pipeline (Phase BRP — SPEC-BRP-CFG-001) ──────────
    # Opt-in; False ⇒ graph byte-for-byte unchanged (mirrors kokoro_enabled /
    # skynet_enabled / budget_enabled).
    blind_review_pipeline_enabled: bool = Field(
        default=False,
        description="Master opt-in for the blind_review_pipeline subgraph (Phase BRP).",
    )
    # Per-role model overrides. None ⇒ ProviderRegistry default model.
    blind_review_spec_model: str | None = Field(
        default=None,
        description="Model id for the spec agent; None uses the provider default.",
    )
    blind_review_implementer_model: str | None = Field(
        default=None,
        description="Model id for the implementer agent; None uses the provider default.",
    )
    blind_review_reviewer_a_model: str | None = Field(
        default=None,
        description="Model id for blind reviewer A; None uses the provider default.",
    )
    blind_review_reviewer_b_model: str | None = Field(
        default=None,
        description="Model id for blind reviewer B; None uses the provider default.",
    )
    # Per-role tool capability filters, passed to ToolProviderPort.get_tools().
    blind_review_spec_capabilities: list[str] = Field(
        default_factory=lambda: ["docs", "requirements"],
        description="Capability filter for the spec agent's tools.",
    )
    blind_review_implementer_capabilities: list[str] = Field(
        default_factory=lambda: ["code", "sandbox"],
        description="Capability filter for the implementer agent's tools.",
    )
    blind_review_reviewer_a_capabilities: list[str] = Field(
        default_factory=lambda: ["code_review", "testing"],
        description="Capability filter for blind reviewer A's tools.",
    )
    blind_review_reviewer_b_capabilities: list[str] = Field(
        default_factory=lambda: ["security", "style"],
        description="Capability filter for blind reviewer B's tools.",
    )
    # Loop control (mirrors code_review approval_threshold + score_gate max_iterations).
    blind_review_approval_threshold: float = Field(
        default=0.8,
        description="Synthesis score at/above which the artifact is approved.",
    )
    blind_review_max_iterations: int = Field(
        default=3,
        description="Correction-loop bound before the score gate force-passes.",
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
        default="sqlite+aiosqlite:///data/db/prismal.db",
        description="SQLAlchemy async database URL",
    )
    chroma_path: str = Field(
        default="data/db/chroma",
        description=(
            "ChromaDB persistence directory. Backward-compatible alias: when "
            "vector_store_backend == 'chroma' and vector_store_path was left at "
            "its default, this value feeds the embedded store path (SPEC-VS-010)."
        ),
    )

    # ── Vector store (Phase Z — interchangeable backend) ──────────────
    vector_store_backend: Literal["chroma", "lancedb", "sqlite_vec", "qdrant", "pgvector"] = Field(
        default="chroma",
        description=(
            "Vector store backend selected by VectorStoreFactory. 'chroma' (default) "
            "and embedded 'lancedb'/'sqlite_vec' need no server; 'qdrant'/'pgvector' "
            "are server backends needing vector_store_url. Non-default backends "
            "require their optional extra (pip install 'prismal-ai[<backend>]')."
        ),
    )
    vector_store_path: str = Field(
        default="data/db/vectors",
        description="Persistence directory for embedded backends (lancedb, sqlite_vec).",
    )
    vector_store_url: str | None = Field(
        default=None,
        description="Connection URL/DSN for server backends (qdrant, pgvector).",
    )
    vector_store_api_key: SecretStr | None = Field(
        default=None,
        description="API key for server backends (e.g. Qdrant). Treated as a secret.",
    )
    vector_store_user: str | None = Field(
        default=None,
        description="Username for server backends that authenticate with user/password.",
    )
    vector_store_password: SecretStr | None = Field(
        default=None,
        description="Password for server backends. Treated as a secret.",
    )

    mongodb_url: str = Field(
        default="",
        description=(
            "MongoDB connection URL (optional — enables MongoDBMemoryStore). "
            "Example: mongodb://user:pass@localhost:27017/prismal"
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
            "PRISMAL_LANGFUSE_HOST",
            "LANGFUSE_HOST",
        ),
        description="Langfuse server URL",
    )
    langfuse_public_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "PRISMAL_LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_PUBLIC_KEY",
        ),
        description="Langfuse public key",
    )
    langfuse_secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices(
            "PRISMAL_LANGFUSE_SECRET_KEY",
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
            " (set PRISMAL_OTEL_ENABLED=true in production)"
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
        default="prismal",
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
        default="data/logs/prismal.log",
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
            "Default TTL for stored facts in days. 0 disables expiration. SPEC-039 AC-039-6."
        ),
    )

    # ── CodeAct Agent (Phase 38 / SPEC-040; hardened by Phase 42 / SPEC-044) ─
    codeact_enabled: bool = Field(
        default=False,
        description=(
            "Toggle CodeAct agent. Default is FALSE after SPEC-044 "
            "hardening — CodeAct still runs in a host subprocess "
            "(isolation comes in Phase 43 / SPEC-045), so operators "
            "must explicitly opt in. When False the supervisor "
            "downgrades any 'codeact' routing decision to the "
            "classic ReAct 'coder' node (see supervisor.py). Do NOT "
            "enable in production without also configuring a real "
            "sandbox isolation backend."
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
            # Standard library — text, serialization, path, math, dates.
            "pathlib,json,re,typing,datetime,collections,"
            "itertools,functools,math,statistics,random,hashlib,base64,"
            "csv,io,"
            # Data science stack.
            "pandas,numpy,polars,matplotlib,sklearn,torch,flaml,duckdb,"
            # HTTP clients (egress is still gated by the isolation
            # backend's network policy — the allowlist only decides
            # what can be *imported*, not what can actually reach the
            # network).
            "requests,httpx"
            # ─────────────────────────────────────────────────────────
            # INTENTIONALLY REMOVED by SPEC-044 AC-044-5:
            #   os         — exposes shell bridges + environment dict
            #   sys        — exposes sys.modules for module-level pivot
            #   subprocess — direct process spawning
            # Operators who need any of these must override the env
            # var explicitly AND accept the increased risk.
            # ─────────────────────────────────────────────────────────
        ),
        description=(
            "Comma-separated list of packages CodeAct code blocks may "
            "import. Any non-allowlisted import blocks execution. "
            "SPEC-044 AC-044-5 removed 'os', 'sys', 'subprocess' from "
            "the default; they must be added explicitly by operators "
            "who accept the risk, and even then are still subject to "
            "the AST denylist (SPEC-044 AC-044-1, AC-044-2, AC-044-3)."
        ),
    )

    # ── Sandbox Process Isolation (Phase 43 / SPEC-045) ──────────────
    is_production: bool = Field(
        default=False,
        description=(
            "Production-deployment marker. When True, the sandbox "
            "isolation factory rejects the 'none' backend (CLAUDE.md "
            "Phase 43 rule #2) so that operators cannot accidentally "
            "deploy Prismal without real process isolation. "
            "Defaults to False so dev / test environments are "
            "unaffected. Flip via PRISMAL_IS_PRODUCTION=true."
        ),
    )
    sandbox_isolation_required: bool = Field(
        default=False,
        description=(
            "When True, ``SandboxExecutor.run_code()`` must delegate to "
            "a real isolation backend (docker / podman / nsjail / bwrap / "
            "firejail) and refuses to fall back to the unisolated host "
            "subprocess path. Default is False during the Phase 43 "
            "transition; flip to True once the deployment has at least "
            "one isolation backend wired and verified."
        ),
    )
    sandbox_isolation_backend: str = Field(
        default="",
        description=(
            "Explicit isolation backend override. Empty string = "
            "autoselect via ``BACKEND_PRIORITY``. Accepted values: "
            "'docker', 'podman', 'nsjail', 'bwrap', 'firejail', 'none'. "
            "The 'none' backend is a dev-mode fallback and is rejected "
            "when ``is_production=True``."
        ),
    )
    sandbox_container_image: str = Field(
        default="python:3.13-slim",
        description=(
            "Container image used by DockerBackend / PodmanBackend. "
            "Must have ``python`` on PATH so the backend can launch "
            "``python -`` and feed the user code on stdin."
        ),
    )
    sandbox_memory_mb: int = Field(
        default=256,
        ge=32,
        le=8192,
        description="Per-invocation memory cap in MB (docker --memory).",
    )
    sandbox_pids_limit: int = Field(
        default=32,
        ge=4,
        le=1024,
        description="Per-invocation PID cap (docker --pids-limit).",
    )
    sandbox_cpus: float = Field(
        default=1.0,
        gt=0.0,
        le=16.0,
        description="Per-invocation CPU quota (docker --cpus).",
    )
    sandbox_tmpfs_size_mb: int = Field(
        default=128,
        ge=16,
        le=2048,
        description=(
            "Ephemeral workspace size in MB. Mounted at ``/sandbox`` "
            "inside the container as rw,noexec,nosuid and wiped on "
            "container exit."
        ),
    )
    sandbox_env_allowlist: str = Field(
        default="",
        description=(
            "Comma-separated list of env-var names to forward into the "
            "isolated environment. Default is empty — no host env is "
            "visible inside the sandbox. Keys matching the patterns "
            "``*API_KEY*`` / ``*SECRET*`` / ``*TOKEN*`` / "
            "``*PASSWORD*`` (case-insensitive) are rejected at parse "
            "time with a WARNING log, even if the operator tries to "
            "add them explicitly."
        ),
    )

    # ── Hierarchical Multi-Agent Architecture (Phase 40 / SPEC-042) ───
    hierarchical_mode: bool = Field(
        default=False,
        description=(
            "Enable 3-level hierarchical agent graph. When True, "
            "``get_async_compiled_graph()`` builds a root supervisor "
            "that routes to 3 domain orchestrators (research, "
            "engineering, analysis) + cron_manager instead of the "
            "flat 12-agent topology. Default is False for full "
            "backward compatibility — see SPEC-042 AC-042-4."
        ),
    )

    # ── Computer Use Agent / CUA (Phase 41 / SPEC-043) ────────────────
    cua_enabled: bool = Field(
        default=False,
        description=(
            "Enable the Computer Use Agent. Requires a vision-capable "
            "model configured via ``PRISMAL_CUA_VISION_MODEL`` and "
            "Playwright installed. Default is False because CUA "
            "executes UI actions in a real browser and HIGH_RISK "
            "actions require HITL approval."
        ),
    )
    cua_vision_model: str = Field(
        default="",
        description=(
            "Vision-capable model string used by the CUA agent to "
            "perceive browser screenshots (e.g. 'claude-opus-4-6'). "
            "Empty string disables CUA even when cua_enabled=True — "
            "the node returns a graceful error message in that case."
        ),
    )
    cua_max_actions: int = Field(
        default=20,
        ge=1,
        le=200,
        description=(
            "Max browser actions per CUA task before forced stop. "
            "Prevents runaway agents when the VLM fails to emit a "
            "'finish' action."
        ),
    )
    cua_screenshot_interval_ms: int = Field(
        default=500,
        ge=0,
        description=(
            "Milliseconds to wait between screenshots in the CUA "
            "action loop. Lowered to 0 in tests."
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
            "Enable JWT-based multi-user RBAC. When False, the simple api_key auth is used."
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

    # ── Multimodal layer (Fase F, opt-in) ──────────────────────────────
    multimodal_enabled: bool = Field(
        default=False,
        description="Master toggle for the multimodal layer (vision/audio/video).",
    )
    vision_enabled: bool = Field(
        default=False,
        description="Enable VisionAgent and the vision_node.",
    )
    audio_enabled: bool = Field(
        default=False,
        description="Enable AudioAgent and the audio_node.",
    )
    video_enabled: bool = Field(
        default=False,
        description="Enable VideoAgent and the video_node.",
    )

    # Multimodal models
    vision_model: str = Field(
        default="",
        description="VLM model string (LiteLLM). Reuses cua_vision_model when empty.",
    )
    multimodal_model: str = Field(
        default="gemini/gemini-2.0-flash",
        description="Natively multimodal model (audio+image+video+text).",
    )
    cross_modal_embedding_model: str = Field(
        default="open_clip:ViT-B-32",
        description="Cross-modal embedding model (CLIP-style), used by [multimodal-embed].",
    )

    # Media limits (inherited by MediaValidator)
    max_image_bytes: int = Field(
        default=10_485_760,
        ge=1024,
        description="Max image size in bytes (10 MB default).",
    )
    max_audio_bytes: int = Field(
        default=52_428_800,
        ge=1024,
        description="Max audio size in bytes (50 MB default).",
    )
    max_video_bytes: int = Field(
        default=209_715_200,
        ge=1024,
        description="Max video size in bytes (200 MB default).",
    )
    max_audio_duration_s: float = Field(
        default=600.0,
        ge=0.5,
        description="Max audio duration in seconds.",
    )
    max_video_duration_s: float = Field(
        default=300.0,
        ge=0.5,
        description="Max video duration in seconds.",
    )
    max_frames_per_video: int = Field(
        default=60,
        ge=1,
        le=600,
        description="Max frames sampled per video (caps LLM cost).",
    )
    video_sample_fps: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Frames-per-second sampled from video.",
    )

    # TTS / OCR
    tts_max_chars: int = Field(
        default=2000,
        ge=1,
        le=10_000,
        description="Max characters per TTS synthesis call.",
    )
    vision_ocr_enabled: bool = Field(
        default=False,
        description="Run an OCR pass in VisionAgent by default.",
    )
    media_workspace: str = Field(
        default="",
        description=(
            "Filesystem root where ingested media is spilled to content-addressed "
            "files (per session). Empty string uses the system temp dir."
        ),
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
        description=("Confine agent filesystem access to this directory. Empty = unrestricted."),
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

    # ── Constitutional AI (Advanced Architectures / SPEC-PAT-003) ─────
    constitutional_enabled: bool = Field(
        default=False,
        description=(
            "Global toggle for the Constitutional AI filter. When True, "
            "the supervisor may route through ConstitutionalFilter.apply() "
            "before emitting agent output to the user."
        ),
    )
    constitutional_max_revisions: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Per-principle cap on revision attempts before marking ``max_revisions_reached=True``."
        ),
    )
    constitutional_principles: list[str] = Field(
        default_factory=lambda: ["P001", "P002", "P003"],
        description=(
            "IDs of the principles enabled by default. The canonical set "
            "lives in ``prismal.agents.patterns.constitutional.DEFAULT_PRINCIPLES`` "
            "(``P001 no_harmful_content``, ``P002 factual_accuracy``, "
            "``P003 no_pii_exposure``). Custom deployments may override with "
            "their own list via env var as a JSON array: "
            '``PRISMAL_CONSTITUTIONAL_PRINCIPLES=\'["P001","custom-P999"]\'``.'
        ),
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

    # ── Extension surface (Fase X) ───────────────────────────────────
    plugins_autodiscover: bool = Field(
        default=True,
        description="Enable plugin auto-discovery via entry points at startup.",
    )
    plugins_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "If non-empty, only plugins whose entry-point name appears here "
            "are loaded. Recommended in production."
        ),
    )
    plugins_denylist: list[str] = Field(
        default_factory=list,
        description="Plugins to disable. Takes precedence over the allowlist.",
    )
    plugins_groups_enabled: list[str] = Field(
        default_factory=lambda: ["subgraphs", "nodes", "tools", "rag_engines"],
        description="Entry-point groups to discover. Default: all supported groups.",
    )
    extension_default_security: Literal["off", "standard", "strict"] = Field(
        default="standard",
        description="Default security level for @prismal_node when not set explicitly.",
    )
    extension_default_audit: bool = Field(
        default=True,
        description="Default audit flag for @prismal_node when not set explicitly.",
    )
    extension_default_timeout_s: float | None = Field(
        default=None,
        description="Default per-invocation timeout for @prismal_node (None = no timeout).",
    )

    # ── Tool provider injection (Fase Y) ─────────────────────────────
    tool_provider_mode: Literal["global", "context"] = Field(
        default="global",
        description=(
            "Tool provider resolution mode: 'global' uses the provider injected "
            "via set_tool_provider() (variante A); 'context' resolves a per-session "
            "provider from the graph config (variante B, multi-tenant)."
        ),
    )
    tool_provider_strict: bool = Field(
        default=False,
        description=(
            "If True, a missing tool provider raises ToolProviderNotConfigured "
            "instead of degrading to static stubs with a warning."
        ),
    )

    # ── Config source injection (Fase W) ─────────────────────────────
    config_source_strict: bool = Field(
        default=False,
        description=(
            "If True, a missing injected ConfigSourcePort raises ConfigSourceError "
            "instead of falling back to EnvConfigSource — for hosts that must "
            "never read ambient environment."
        ),
    )

    # Runtime composition root (Phase R — SPEC-CR-006)
    runtime_mode: Literal["global", "context"] = Field(
        default="global",
        description=(
            "Runtime composition mode used by build_runtime(): 'global' injects "
            "process singletons (set_tool_provider); 'context' keeps every port "
            "inside the returned RuntimeContext for per-session/per-tenant use. "
            "Unifies tool_provider_mode (Y) and the vector-store mode (Z). "
            "Backward-compat: when runtime_mode is not set explicitly but "
            "tool_provider_mode is, runtime_mode is derived from it."
        ),
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
    ml_random_seed: int = Field(default=42, description="Global random seed for reproducibility")
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
            "Default IANA timezone for all cron jobs. Empty = inherit from 'timezone' field."
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

    # ── LLM rate-limit retry ───────────────────────────────────────────────
    llm_rate_limit_max_retries: int = Field(
        default=3,
        ge=0,
        le=10,
        description=(
            "Maximum number of LLM invocation retries on 429 / rate-limit "
            "errors inside the ReAct loop.  0 disables retry (legacy "
            "behaviour: fail fast and return an apology string)."
        ),
    )
    llm_rate_limit_base_delay_seconds: float = Field(
        default=5.0,
        ge=0.0,
        le=120.0,
        description=(
            "Base delay in seconds for exponential backoff between LLM "
            "rate-limit retries.  Attempt N waits `base * 2**N` seconds, "
            "capped at 60 s."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_runtime_mode(cls, data: Any) -> Any:
        """Derive ``runtime_mode`` from ``tool_provider_mode`` (SPEC-CR-006).

        Backward-compat: a host that already set ``tool_provider_mode='context'``
        (Phase Y) but never heard of ``runtime_mode`` (Phase R) should get a
        context runtime by default. Only fills ``runtime_mode`` when it is
        absent from the input; an explicit ``runtime_mode`` always wins.
        """
        if isinstance(data, dict) and "runtime_mode" not in data:
            tool_mode = data.get("tool_provider_mode")
            if tool_mode in ("global", "context"):
                data["runtime_mode"] = tool_mode
        return data

    @model_validator(mode="after")
    def _validate_telegram_webhook(self) -> "Settings":
        """Validate webhook config when telegram_webhook_enabled is True."""
        if self.telegram_webhook_enabled:
            if not self.telegram_webhook_url.startswith("https://"):
                raise ValueError("PRISMAL_TELEGRAM_WEBHOOK_URL must be an HTTPS URL")
            if not self.telegram_webhook_secret.get_secret_value():
                raise ValueError(
                    "PRISMAL_TELEGRAM_WEBHOOK_SECRET must be set when webhook is enabled"
                )
        return self

    @model_validator(mode="after")
    def _resolve_llm_provider(self) -> "Settings":
        """Resolve ``llm_provider`` into ``default_model`` / ``fallback_model``.

        When ``llm_provider`` is non-empty the validator:

        1. Validates the provider name against the known map.
        2. Replaces ``default_model`` / ``fallback_model`` with the
           provider defaults *unless* the user already pinned a model
           whose LiteLLM prefix is consistent with the provider
           (e.g. ``ollama/mistral`` when provider is ``ollama``).
        3. Logs a ``warnings.warn`` entry when a conflict is detected
           so the operator notices stale overrides in ``.env``.

        An empty ``llm_provider`` is the legacy path — explicit
        ``default_model`` / ``fallback_model`` values are used as-is.
        """
        if not self.llm_provider:
            return self

        provider = self.llm_provider.strip().lower()
        provider_map: dict[str, tuple[str, str, tuple[str, ...]]] = {
            # key: (default_model, fallback_model, litellm_prefixes)
            "anthropic": (
                "claude-sonnet-4-5",
                "gpt-4o-mini",
                ("claude-", "anthropic/"),
            ),
            "openai": (
                "gpt-4o",
                "gpt-4o-mini",
                ("gpt-", "o1-", "openai/"),
            ),
            "google": (
                "gemini/gemini-1.5-pro",
                "gemini/gemini-2.0-flash",
                ("gemini/", "google/"),
            ),
            "ollama": (
                # ``ollama_chat/`` uses Ollama's /api/chat endpoint which
                # supports native tool / function calling for compatible
                # models (llama3.1+, mistral, qwen2.5, ...), avoiding
                # LiteLLM's prompt-based emulation that otherwise leaks
                # raw ``{"function": "respond", ...}`` JSON into the reply.
                "ollama_chat/llama3.1",
                "",
                ("ollama/", "ollama_chat/"),
            ),
        }
        entry = provider_map.get(provider)
        if entry is None:
            raise ValueError(
                f"Unknown PRISMAL_LLM_PROVIDER value: {self.llm_provider!r}."
                f" Accepted: '', 'anthropic', 'openai', 'google', 'ollama'."
            )

        default_default, default_fallback, prefixes = entry

        def _matches(model: str) -> bool:
            return any(model.startswith(p) for p in prefixes)

        import warnings

        if not self.default_model or not _matches(self.default_model):
            if self.default_model and not _matches(self.default_model):
                warnings.warn(
                    f"PRISMAL_LLM_PROVIDER={provider!r} but "
                    f"PRISMAL_DEFAULT_MODEL={self.default_model!r} does "
                    f"not match that provider — overriding with "
                    f"{default_default!r}.  Remove one of the two env "
                    f"vars to silence this warning.",
                    stacklevel=2,
                )
            self.default_model = default_default

        if self.fallback_model and not _matches(self.fallback_model):
            warnings.warn(
                f"PRISMAL_LLM_PROVIDER={provider!r} but "
                f"PRISMAL_FALLBACK_MODEL={self.fallback_model!r} does "
                f"not match that provider — overriding with "
                f"{default_fallback!r}.  Set PRISMAL_FALLBACK_MODEL= "
                f"(empty) to disable the fallback wrapper entirely.",
                stacklevel=2,
            )
            self.fallback_model = default_fallback
        elif not self.fallback_model:
            # Use provider default (may itself be empty for ollama).
            self.fallback_model = default_fallback

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
                        f" (field: PRISMAL_{field_name.upper()})"
                    ) from exc
        return self

    @model_validator(mode="after")
    def _validate_skynet(self) -> "Settings":
        """Validate and clamp the Skynet swarm settings (S1-04 / RF-SKY-03).

        1. ``skynet_max_swarm`` is clamped to ``parallel_max_workers`` — the
           operator-visible ceiling always wins.
        2. A fixed ``skynet_swarm_size`` larger than the effective cap is a
           configuration contradiction and raises ``SkynetConfigError``
           (overflow *orders* are deferred at runtime, but a fixed size that
           can never dispatch in one round is an operator error).
        """
        from prismal.core.exceptions import SkynetConfigError

        if self.skynet_max_swarm > self.parallel_max_workers:
            self.skynet_max_swarm = self.parallel_max_workers

        # skynet_max_swarm is ≥ 1 (Field ge=1), so a dynamic size (0) never trips this.
        if self.skynet_swarm_size > self.skynet_max_swarm:
            raise SkynetConfigError(
                f"PRISMAL_SKYNET_SWARM_SIZE={self.skynet_swarm_size} exceeds the "
                f"effective swarm cap min(skynet_max_swarm, parallel_max_workers)"
                f"={self.skynet_max_swarm}. Lower the fixed size or raise the cap."
            )

        # S+3: remote workers need the A2A layer. Missing it is not fatal —
        # remote-bound roles simply degrade to local — so warn, never raise.
        if self.skynet_remote_workers_enabled and not self.a2a_enabled:
            from prismal.core.logging import get_logger

            get_logger("prismal.core.config").warning("skynet.remote_needs_a2a")
        return self

    @model_validator(mode="after")
    def _validate_budget(self) -> "Settings":
        """Validate the cost/budget governance settings (C1-04 / SPEC-CST-CFG-001).

        ``budget_soft_ratio`` is range-bound by its Field (``[0, 1]``); here we
        reject an unknown ``budget_scope`` so a typo fails fast at load time
        rather than silently disabling tenant accounting.
        """
        valid_scopes = {"turn", "session", "tenant"}
        if self.budget_scope not in valid_scopes:
            raise ValueError(
                f"PRISMAL_BUDGET_SCOPE={self.budget_scope!r} is invalid; "
                f"expected one of {sorted(valid_scopes)}."
            )
        valid_windows = {"month", "day"}
        if self.budget_tenant_window not in valid_windows:
            raise ValueError(
                f"PRISMAL_BUDGET_TENANT_WINDOW={self.budget_tenant_window!r} is invalid; "
                f"expected one of {sorted(valid_windows)}."
            )
        return self

    @model_validator(mode="after")
    def _validate_eval(self) -> "Settings":
        """Validate the eval-harness settings (V1-03 / SPEC-EVL-CFG-001).

        ``eval_regression_tolerance``/``eval_seed`` are range-bound by their
        Fields; here we reject an unknown ``eval_default_mode`` so a typo fails
        fast at load time rather than silently running the wrong mode.
        """
        valid_modes = {"fakes", "live_api"}
        if self.eval_default_mode not in valid_modes:
            raise ValueError(
                f"PRISMAL_EVAL_DEFAULT_MODE={self.eval_default_mode!r} is invalid; "
                f"expected one of {sorted(valid_modes)}."
            )
        return self

    @model_validator(mode="after")
    def _validate_blind_review(self) -> "Settings":
        """Validate the Blind Review Pipeline settings (BRP1-03 / SPEC-BRP-CFG-001).

        Mirrors ``_validate_skynet``/``_validate_budget``:

        1. ``blind_review_approval_threshold`` must be in ``[0.0, 1.0]``, else
           :class:`BlindReviewConfigError`.
        2. ``blind_review_max_iterations`` must be ``>= 1``, else
           :class:`BlindReviewConfigError`.
        3. A same-model reviewer pair is a legal (if weaker) configuration — it
           logs a ``WARNING`` (``blind_review.reviewers_share_model``) rather
           than raising.
        """
        from prismal.core.exceptions import BlindReviewConfigError

        if not 0.0 <= self.blind_review_approval_threshold <= 1.0:
            raise BlindReviewConfigError(
                f"PRISMAL_BLIND_REVIEW_APPROVAL_THRESHOLD="
                f"{self.blind_review_approval_threshold} is out of range; "
                f"expected 0.0 <= threshold <= 1.0."
            )
        if self.blind_review_max_iterations < 1:
            raise BlindReviewConfigError(
                f"PRISMAL_BLIND_REVIEW_MAX_ITERATIONS="
                f"{self.blind_review_max_iterations} is invalid; expected >= 1."
            )
        if (
            self.blind_review_reviewer_a_model is not None
            and self.blind_review_reviewer_a_model == self.blind_review_reviewer_b_model
        ):
            from prismal.core.logging import get_logger

            get_logger("prismal.core.config").warning(
                "blind_review.reviewers_share_model",
                model=self.blind_review_reviewer_a_model,
            )
        return self

    @model_validator(mode="after")
    def _validate_hardening(self) -> "Settings":
        """Validate the runtime-hardening settings (H1-01 / SPEC-HRD-CFG-001).

        Numeric ranges are enforced by their Fields; here we reject an unknown
        ``hardening_mode`` or ``hardening_tool_policy_default`` so a typo fails
        fast at load time rather than silently disabling a control.
        """
        valid_modes = {"off", "warn", "enforce"}
        if self.hardening_mode not in valid_modes:
            raise ValueError(
                f"PRISMAL_HARDENING_MODE={self.hardening_mode!r} is invalid; "
                f"expected one of {sorted(valid_modes)}."
            )
        valid_defaults = {"allow", "deny"}
        if self.hardening_tool_policy_default not in valid_defaults:
            raise ValueError(
                f"PRISMAL_HARDENING_TOOL_POLICY_DEFAULT="
                f"{self.hardening_tool_policy_default!r} is invalid; "
                f"expected one of {sorted(valid_defaults)}."
            )
        return self

    @model_validator(mode="after")
    def _validate_node_typesafety(self) -> "Settings":
        """Reject an unknown ``node_typesafety_mode`` at load time (SPEC-NTS-CFG-001).

        Mirrors ``_validate_hardening`` so a typo fails fast at load time rather
        than silently disabling the control.
        """
        valid_modes = {"off", "warn", "enforce"}
        if self.node_typesafety_mode not in valid_modes:
            raise ValueError(
                f"PRISMAL_NODE_TYPESAFETY_MODE={self.node_typesafety_mode!r} is "
                f"invalid; expected one of {sorted(valid_modes)}."
            )
        return self

    @model_validator(mode="after")
    def _validate_observability(self) -> "Settings":
        """Reject unknown observability enum values at load time (SPEC-OBS-CFG-001).

        Buffer sizes are range-bound by their Fields (``gt=0``); here we reject an
        unknown ``observability_dataset_export_format`` / ``observability_score_source_default``
        so a typo fails fast at load time rather than silently misbehaving (mirrors
        ``_validate_budget`` / ``_validate_hardening``).
        """
        valid_formats = {"langsmith", "langfuse"}
        if self.observability_dataset_export_format not in valid_formats:
            raise ValueError(
                f"PRISMAL_OBSERVABILITY_DATASET_EXPORT_FORMAT="
                f"{self.observability_dataset_export_format!r} is invalid; "
                f"expected one of {sorted(valid_formats)}."
            )
        valid_sources = {"human", "llm_judge", "system"}
        if self.observability_score_source_default not in valid_sources:
            raise ValueError(
                f"PRISMAL_OBSERVABILITY_SCORE_SOURCE_DEFAULT="
                f"{self.observability_score_source_default!r} is invalid; "
                f"expected one of {sorted(valid_sources)}."
            )
        return self

    @model_validator(mode="after")
    def _validate_guardrails_modernization(self) -> "Settings":
        """Validate the guardrails-modernization settings (SPEC-GRD-CFG-001).

        An empty ``nemo_classifier_categories`` is only a configuration error
        when the classifier is actually enabled — disabled-by-default is the
        zero-overhead path and an empty list there is harmless.
        """
        if self.nemo_classifier_enabled and not self.nemo_classifier_categories:
            raise ValueError(
                "PRISMAL_NEMO_CLASSIFIER_CATEGORIES must be non-empty when "
                "PRISMAL_NEMO_CLASSIFIER_ENABLED=true."
            )
        return self

    @model_validator(mode="after")
    def _validate_loop_hardening(self) -> "Settings":
        """Validate the loop-hardening settings (SPEC-LH-CFG-001).

        Numeric ranges are enforced by their Fields; here we reject an unknown
        ``context_compaction_strategy`` so a typo fails fast at load time
        rather than silently disabling a control.
        """
        valid_strategies = {"truncate", "summarize", "mask_observations"}  # + TKE MSK-001
        if self.context_compaction_strategy not in valid_strategies:
            raise ValueError(
                f"PRISMAL_CONTEXT_COMPACTION_STRATEGY={self.context_compaction_strategy!r} "
                f"is invalid; expected one of {sorted(valid_strategies)}."
            )
        return self

    @model_validator(mode="after")
    def _validate_token_efficiency(self) -> "Settings":
        """Validate the token-efficiency settings (SPEC-TKE-CFG-001).

        Numeric floors are enforced by their Fields; here we reject unknown
        literals, negative per-tool/per-agent budgets and an inverted
        hysteresis band so a typo fails fast at load time.
        """
        literals: dict[str, set[str]] = {
            "prompt_cache_ttl": {"5m", "1h"},
            "prompt_cache_scope": {"tenant", "global"},
            "prompt_layout": {"legacy", "cache_stable"},
            "tool_gating_mode": {"remove", "mask"},
            "artifact_store_backend": {"memory", "sqlite"},
            "llm_effort_profile": {"none", "recommended"},
        }
        for name, allowed in literals.items():
            value = getattr(self, name)
            if value not in allowed:
                raise ValueError(
                    f"PRISMAL_{name.upper()}={value!r} is invalid; "
                    f"expected one of {sorted(allowed)}."
                )
        bad_effort = {
            agent: level
            for agent, level in self.llm_effort_by_agent.items()
            if level not in {"low", "medium", "high"}
        }
        if bad_effort:
            raise ValueError(
                f"PRISMAL_LLM_EFFORT_BY_AGENT has invalid levels {bad_effort}; "
                "expected low | medium | high."
            )
        for name in ("tool_output_max_tokens_by_tool", "llm_max_tokens_by_agent"):
            negative = {k: v for k, v in getattr(self, name).items() if v < 0}
            if negative:
                raise ValueError(f"PRISMAL_{name.upper()} has negative values {negative}.")
        high, low = self.context_compaction_high_water, self.context_compaction_low_water
        if high > 0 and low >= high:
            raise ValueError(
                f"PRISMAL_CONTEXT_COMPACTION_LOW_WATER={low} must be below "
                f"PRISMAL_CONTEXT_COMPACTION_HIGH_WATER={high}."
            )
        return self

    @model_validator(mode="after")
    def _validate_llm_gateways(self) -> "Settings":
        """Validate the gateway settings (SPEC-GW-CFG-001) so typos fail at load time."""
        literals: dict[str, set[str | None]] = {
            "openrouter_provider_sort": {None, "price", "throughput", "latency"},
            "openrouter_auto_tier": {None, "low", "medium", "high", "xhigh", "max"},
            "opencode_go_quota_action": {"refuse", "downgrade"},
            "omniroute_upstream_attestation": {None, "api_keys_only", "subscription_oauth"},
        }
        for name, allowed in literals.items():
            value = getattr(self, name)
            if value not in allowed:
                raise ValueError(
                    f"PRISMAL_{name.upper()}={value!r} is invalid; expected one of "
                    f"{sorted(a for a in allowed if a is not None)}."
                )
        for name in ("opencode_go_window_5h_ratio", "opencode_go_window_week_ratio"):
            ratio = getattr(self, name)
            if not 0.0 < ratio <= 1.0:
                raise ValueError(f"PRISMAL_{name.upper()}={ratio} must be in (0, 1].")
        if self.opencode_go_quota_action == "downgrade" and not self.opencode_go_downgrade_model:
            raise ValueError(
                "PRISMAL_OPENCODE_GO_QUOTA_ACTION=downgrade requires "
                "PRISMAL_OPENCODE_GO_DOWNGRADE_MODEL."
            )
        semver = re.compile(r"^\d+\.\d+\.\d+$")
        for name in ("omniroute_min_version", "omniroute_declared_version"):
            version = getattr(self, name)
            if version is not None and not semver.match(version):
                raise ValueError(f"PRISMAL_{name.upper()}={version!r} is not MAJOR.MINOR.PATCH.")
        bad_refs = {a: r for a, r in self.llm_gateway_agent_map.items() if not r.startswith("gw:")}
        if bad_refs:
            raise ValueError(f"PRISMAL_LLM_GATEWAY_AGENT_MAP values must be gw: refs: {bad_refs}.")
        bad_plugins = set(self.openrouter_plugins) - {
            "response-healing",
            "context-compression",
            "pdf",
        }
        if bad_plugins:
            raise ValueError(
                f"PRISMAL_OPENROUTER_PLUGINS has unsupported ids {sorted(bad_plugins)}."
            )
        name_re = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
        builtin = {"openrouter", "opencode", "opencode-go", "omniroute"}
        for name, entry in self.llm_gateway_openai_compatible.items():
            if not name_re.match(name) or name in builtin:
                raise ValueError(
                    f"PRISMAL_LLM_GATEWAY_OPENAI_COMPATIBLE name {name!r} is invalid or reserved."
                )
            if not isinstance(entry, dict) or not entry.get("base_url"):
                raise ValueError(
                    f"PRISMAL_LLM_GATEWAY_OPENAI_COMPATIBLE[{name!r}] needs a base_url."
                )
        return self

    @model_validator(mode="after")
    def _validate_decisions(self) -> "Settings":
        """Validate the typed-decision settings (SPEC-DEC-CFG-001, CAL-002) at load time."""
        backends = {"rule", "ollama", "laya", "jev", "llm"}
        unknown = [b for b in self.decision_backend_order if b not in backends]
        if unknown:
            raise ValueError(f"PRISMAL_DECISION_BACKEND_ORDER has unknown backends {unknown}.")
        from prismal.decisions.catalog import INTEGRATION_FLAGS

        for integration, mode in self.decision_modes.items():
            if integration not in INTEGRATION_FLAGS:
                raise ValueError(
                    f"PRISMAL_DECISION_MODES key {integration!r} is not one of "
                    f"{sorted(INTEGRATION_FLAGS)}."
                )
            if mode not in {"off", "shadow", "enforce"}:
                raise ValueError(
                    f"PRISMAL_DECISION_MODES[{integration!r}]={mode!r}; "
                    "expected off|shadow|enforce."
                )
        if "latest" in self.jev_model.lower() or not re.search(r"\d+\.\d+", self.jev_model):
            raise ValueError(f"PRISMAL_JEV_MODEL={self.jev_model!r} must be a pinned version.")
        if self.jev_transport not in {"http", "sdk"}:
            raise ValueError(f"PRISMAL_JEV_TRANSPORT={self.jev_transport!r}; expected http|sdk.")
        if self.laya_mode not in {"local", "http"}:
            raise ValueError(f"PRISMAL_LAYA_MODE={self.laya_mode!r}; expected local|http.")
        if self.laya_mode == "http" and not self.laya_base_url:
            raise ValueError("PRISMAL_LAYA_MODE=http requires PRISMAL_LAYA_BASE_URL.")
        if not self.decision_languages:
            raise ValueError("PRISMAL_DECISION_LANGUAGES must name at least one language.")
        self._validate_decision_ollama()
        tier_defaults = {"low": 0.85, "medium": 0.90, "high": 0.97}
        for tier, tau in self.decision_tau_overrides.items():
            if tier not in tier_defaults:
                raise ValueError(
                    f"PRISMAL_DECISION_TAU_OVERRIDES key {tier!r} must be low|medium|high "
                    "(critical is never auto-accepted)."
                )
            if not tier_defaults[tier] <= tau <= 1.0:
                raise ValueError(
                    f"PRISMAL_DECISION_TAU_OVERRIDES[{tier!r}]={tau} must be in "
                    f"[{tier_defaults[tier]}, 1.0] (raise-only)."
                )
        if self.decisions_enabled:
            from prismal.decisions.calibration import validate_enforce_calibration

            validate_enforce_calibration(self)
        return self

    def _validate_decision_ollama(self) -> None:
        """Ollama tier models, remote guard (OLL-010) and concrete HITL thresholds."""
        for tier, model in self.decision_ollama_models.items():
            if tier not in {"fast", "accurate"} or not model.strip():
                raise ValueError(
                    f"PRISMAL_DECISION_OLLAMA_MODELS[{tier!r}]={model!r}; expected "
                    "fast|accurate → a non-empty model name."
                )
        from prismal.decisions.catalog import HITL_QUESTION_IDS

        for qid, threshold in self.decision_hitl_raise_thresholds.items():
            if qid not in HITL_QUESTION_IDS:
                raise ValueError(
                    f"PRISMAL_DECISION_HITL_RAISE_THRESHOLDS key {qid!r} is not one of "
                    f"{list(HITL_QUESTION_IDS)}."
                )
            if not 0.0 < threshold <= 1.0:
                raise ValueError(
                    f"PRISMAL_DECISION_HITL_RAISE_THRESHOLDS[{qid!r}]={threshold} "
                    "must be in (0, 1]."
                )
        # Only an explicit decision URL is checked at load: an unset one falls back to the
        # LLM's ``ollama_base_url`` (possibly remote for existing users) and the factory
        # then refuses to build the tier instead (no startup break, no SSRF).
        url = self.decision_ollama_base_url
        if (
            not self.decisions_enabled
            or url is None
            or "ollama" not in self.decision_backend_order
            or self.decision_ollama_allow_remote
        ):
            return
        if not is_loopback_url(url):
            raise ValueError(
                f"Ollama decision backend at {url!r} is not loopback; Ollama has no "
                "authentication, so set PRISMAL_DECISION_OLLAMA_ALLOW_REMOTE=true to accept it."
            )

    @model_validator(mode="after")
    def _validate_protocol_upgrades(self) -> "Settings":
        """Validate the protocol-upgrade settings (SPEC-PRT-CFG-001) at load time."""
        if self.mcp_tool_cache_default_ttl_s > self.mcp_tool_cache_max_ttl_s:
            raise ValueError(
                "PRISMAL_MCP_TOOL_CACHE_DEFAULT_TTL_S must be <= PRISMAL_MCP_TOOL_CACHE_MAX_TTL_S."
            )
        if self.mcp_task_poll_interval_s > self.mcp_task_poll_max_interval_s:
            raise ValueError(
                "PRISMAL_MCP_TASK_POLL_INTERVAL_S must be <= PRISMAL_MCP_TASK_POLL_MAX_INTERVAL_S."
            )
        if self.a2a_protocol_version == "0.3" and not self.a2a_accept_legacy:
            raise ValueError(
                "PRISMAL_A2A_ACCEPT_LEGACY=false requires PRISMAL_A2A_PROTOCOL_VERSION=1.0."
            )
        return self

    @model_validator(mode="after")
    def _validate_memory(self) -> "Settings":
        """Validate the agentic-memory settings (SPEC-MEM-CFG-001) at load time."""
        for name in ("memory_recall_budget_tokens", "memory_playbook_budget_tokens"):
            if getattr(self, name) <= 0:
                raise ValueError(f"PRISMAL_{name.upper()} must be > 0.")
        if not 0.0 < self.memory_sufficiency_tau < 1.0:
            raise ValueError("PRISMAL_MEMORY_SUFFICIENCY_TAU must be in (0, 1).")
        # Pure syntactic check (no scheduler import at load time): five crontab fields.
        fields = self.memory_consolidation_cron.split()
        if len(fields) != 5 or not all(_CRON_FIELD_RE.fullmatch(f) for f in fields):
            raise ValueError(
                f"PRISMAL_MEMORY_CONSOLIDATION_CRON={self.memory_consolidation_cron!r} is not "
                "a valid crontab expression."
            )
        types = ("episodic", "semantic", "procedural", "preference")
        for kind, days in self.memory_retention_days_by_type.items():
            if kind not in types or days < 0:
                raise ValueError(
                    f"PRISMAL_MEMORY_RETENTION_DAYS_BY_TYPE[{kind!r}]={days}: type must be one "
                    f"of {types} and days >= 0."
                )
        if (
            self.memory_v2_enabled
            and self.memory_mode == "enforce"
            and self.memory_write_gate == "decision"
            and not self.decisions_enabled
        ):
            raise ValueError(
                "PRISMAL_MEMORY_WRITE_GATE=decision in enforce mode requires "
                "PRISMAL_DECISIONS_ENABLED=true."
            )
        return self

    @model_validator(mode="after")
    def _validate_routing(self) -> "Settings":
        """Validate the adaptive-routing settings (SPEC-AMR-CFG-001, SEC-001) at load time."""
        tiers = ("nano", "small", "medium", "frontier")
        risks = ("low", "medium", "high", "critical")
        if not 0.0 < self.routing_batch_alpha <= 1.0:
            raise ValueError(
                f"PRISMAL_ROUTING_BATCH_ALPHA={self.routing_batch_alpha} must be in (0, 1]."
            )
        negative = {m: c for m, c in self.routing_model_concurrency.items() if c < 0}
        if negative:
            raise ValueError(f"PRISMAL_ROUTING_MODEL_CONCURRENCY has negative caps {negative}.")
        if self.routing_cascade_start_tier not in tiers:
            raise ValueError(
                f"PRISMAL_ROUTING_CASCADE_START_TIER={self.routing_cascade_start_tier!r}; "
                f"expected one of {tiers}."
            )
        if self.routing_cascade_max_escalations > 3:
            raise ValueError("PRISMAL_ROUTING_CASCADE_MAX_ESCALATIONS must be <= 3.")
        for risk, tier in self.routing_risk_floor.items():
            if risk not in risks or tier not in tiers:
                raise ValueError(
                    f"PRISMAL_ROUTING_RISK_FLOOR[{risk!r}]={tier!r}: risk must be one of "
                    f"{risks} and tier one of {tiers} (auto is never a floor)."
                )
        unknown_caps = [t for t in self.routing_tier_capabilities if t not in tiers]
        if unknown_caps:
            raise ValueError(f"PRISMAL_ROUTING_TIER_CAPABILITIES has unknown tiers {unknown_caps}.")
        if self.batch_poll_initial_s > self.batch_poll_max_s:
            raise ValueError("PRISMAL_BATCH_POLL_INITIAL_S must be <= PRISMAL_BATCH_POLL_MAX_S.")
        if self.adaptive_routing_enabled:
            # SEC-001 fail-closed: a usable floor for critical risk is mandatory.
            if "critical" not in self.routing_risk_floor:
                raise ValueError(
                    "PRISMAL_ROUTING_RISK_FLOOR must define a 'critical' floor when "
                    "adaptive routing is enabled."
                )
            if self.routing_policy == "batch" and not self.routing_predictor_path:
                raise ValueError("routing_policy='batch' requires PRISMAL_ROUTING_PREDICTOR_PATH.")
            from pathlib import Path

            if Path(self.routing_tier_models).is_file():
                # SEC-001: an invalid tier map / risk_floor refuses to start (a
                # missing file is tolerated: tiers fall back to default_model).
                from prismal.routing.policies.static import load_tier_models

                load_tier_models(self.routing_tier_models)
        return self

    @model_validator(mode="after")
    def _validate_identity(self) -> "Settings":
        """Validate the identity & access governance settings (ID1-02 / SPEC-IDN-CFG-001).

        Numeric ranges are enforced by their Fields; here we reject an unknown
        ``identity_mode``/``identity_provider``/``identity_did_method``/
        ``identity_vault`` (and an ``oidc`` provider without an ``oidc_issuer``)
        so a misconfiguration fails fast at load time rather than silently
        disabling a control. Raises :class:`IdentityConfigError`.
        """
        from prismal.core.exceptions import IdentityConfigError

        checks = {
            "PRISMAL_IDENTITY_MODE": (self.identity_mode, {"off", "warn", "enforce"}),
            "PRISMAL_IDENTITY_PROVIDER": (self.identity_provider, {"local", "oidc"}),
            "PRISMAL_IDENTITY_DID_METHOD": (self.identity_did_method, {"key", "web"}),
            "PRISMAL_IDENTITY_VAULT": (self.identity_vault, {"env", "file"}),
        }
        for env_name, (value, valid) in checks.items():
            if value not in valid:
                raise IdentityConfigError(
                    f"{env_name}={value!r} is invalid; expected one of {sorted(valid)}."
                )

        if self.identity_provider == "oidc" and not self.oidc_issuer:
            raise IdentityConfigError(
                "PRISMAL_IDENTITY_PROVIDER='oidc' requires PRISMAL_OIDC_ISSUER to be set."
            )
        return self

    # ── Vector store helpers (Phase Z) ───────────────────────────────
    def resolve_vector_store_path(self) -> str:
        """Return the persistence path for an embedded vector-store backend.

        Honours the ``chroma_path`` backward-compatible alias (SPEC-VS-010): when
        the backend is ``chroma`` the existing ``chroma_path`` always wins (zero
        behaviour change for current deployments); for the other embedded
        backends (``lancedb``, ``sqlite_vec``) ``vector_store_path`` is used.
        """
        if self.vector_store_backend == "chroma":
            return self.chroma_path
        return self.vector_store_path


def build_settings(source: "ConfigSourcePort | None" = None) -> Settings:
    """Build a :class:`Settings` from a configuration source (Phase W).

    Pure constructor over a source — no global state, no caching — which makes
    it the per-tenant / per-test path:

    - *source* given → ``Settings`` is built from that source only.
    - *source* ``None`` → uses the injected global source
      (:func:`get_config_source`) or the default :class:`EnvConfigSource`.

    Raises:
        ConfigSourceError: when no source is available (none passed, none
            injected) and ``config_source_strict`` is enabled — for hosts that
            must never read ambient environment.
    """
    from prismal.core.config_source import _default_source, get_config_source

    if source is None and get_config_source() is None:
        # No explicit and no injected source: consult the default once to honour
        # strict mode (the one allowed ambient read is the strict flag itself).
        probe = _build_with_source(_default_source())
        if probe.config_source_strict:
            from prismal.core.exceptions import ConfigSourceError

            raise ConfigSourceError(
                "none",
                "config_source_strict is set but no ConfigSourcePort was injected",
            )
        return probe

    resolved = source or get_config_source()
    assert resolved is not None  # narrowed by the branch above
    return _build_with_source(resolved)


def _build_with_source(source: "ConfigSourcePort") -> Settings:
    """Construct ``Settings`` with *source* active for ``settings_customise_sources``."""
    token = _active_source.set(source)
    try:
        return Settings()
    finally:
        _active_source.reset(token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings instance.

    Uses lru_cache to ensure a single Settings object is created and reused
    across the application lifetime. Delegates to :func:`build_settings`, which
    resolves the injected :class:`~prismal.core.config_source.ConfigSourcePort`
    (or the default :class:`~prismal.core.config_source.EnvConfigSource`).

    Returns:
        The singleton Settings instance built from the active config source.
    """
    return build_settings()


def reload_settings() -> None:
    """Clear the :func:`get_settings` cache (Phase W).

    Called by :func:`prismal.core.config_source.set_config_source` so the next
    :func:`get_settings` call rebuilds ``Settings`` from the newly-injected
    configuration source.

    Defensive: tests frequently ``monkeypatch`` ``get_settings`` with a plain
    function that has no ``cache_clear``. Because the autouse ``.env`` isolation
    fixture runs ``set_config_source(None)`` during teardown *before* monkeypatch
    restores the original, clearing a cache that no longer exists is a no-op
    rather than an :class:`AttributeError`.
    """
    cache_clear = getattr(get_settings, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()


@lru_cache(maxsize=1)
def get_maintenance_settings() -> MaintenanceSettings:
    """Return the cached :class:`MaintenanceSettings` instance.

    Uses ``lru_cache`` so the same object is reused across the application
    lifetime.  Also exposed via :attr:`Settings.maintenance` for convenience.

    Returns:
        Singleton :class:`MaintenanceSettings` loaded from env / .env file.
    """
    return MaintenanceSettings()
