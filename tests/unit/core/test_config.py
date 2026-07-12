"""Unit tests for core Settings configuration."""

import pytest
from pydantic import ValidationError


def test_settings_default_model() -> None:
    """Settings loads default_model with its class-level default."""
    from prismal.core.config import Settings

    s = Settings()
    assert s.default_model == "claude-sonnet-4-5"


def test_settings_fallback_model() -> None:
    """Settings has a fallback_model defined."""
    from prismal.core.config import Settings

    s = Settings()
    assert s.fallback_model == "gpt-4o-mini"


def test_settings_security_mode_default() -> None:
    """Security mode defaults to 'strict'."""
    from prismal.core.config import Settings

    s = Settings()
    assert s.security_mode == "strict"


def test_settings_security_mode_invalid() -> None:
    """Invalid security_mode raises ValidationError."""
    from prismal.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(security_mode="unknown")  # type: ignore[arg-type]


def test_settings_risk_threshold_default() -> None:
    """Risk threshold defaults to 30."""
    from prismal.core.config import Settings

    s = Settings()
    assert s.risk_threshold == 30


def test_settings_risk_threshold_out_of_range() -> None:
    """Risk threshold must be 0-100."""
    from prismal.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(risk_threshold=150)


def test_settings_db_url_default() -> None:
    """DB URL defaults to SQLite path."""
    from prismal.core.config import Settings

    s = Settings()
    assert "sqlite" in s.db_url


def test_settings_ports() -> None:
    """API and dashboard ports are defined."""
    from prismal.core.config import Settings

    s = Settings()
    assert s.port == 8000
    assert s.dashboard_port == 3000


def test_settings_shell_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell execution is disabled by default (security)."""
    from prismal.core.config import Settings

    monkeypatch.delenv("PRISMAL_SHELL_ENABLED", raising=False)
    # Pass _env_file=None so .env on disk cannot override the default.
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.shell_enabled is False


def test_get_settings_cache_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings() returns the same cached instance; cache can be cleared."""
    from prismal.core.config import get_settings

    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    get_settings.cache_clear()


def test_settings_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRISMAL_ env vars override defaults."""
    from prismal.core.config import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PRISMAL_DEFAULT_MODEL", "gpt-4o")
    s = Settings()  # fresh instance, not cached
    assert s.default_model == "gpt-4o"
    get_settings.cache_clear()


def test_cron_notify_defaults_empty() -> None:
    """Cron notification targets default to empty strings (opt-in)."""
    from prismal.core.config import Settings

    s = Settings()
    assert s.cron_notify_telegram_chat_id == ""
    assert s.cron_notify_slack_channel == ""


# ---------------------------------------------------------------------------
# llm_provider resolver (Phase 44)
# ---------------------------------------------------------------------------


def test_llm_provider_blank_keeps_explicit_models() -> None:
    """With llm_provider='' the explicit default_model/fallback_model win."""
    from prismal.core.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="",
        default_model="claude-sonnet-4-5",
        fallback_model="gpt-4o-mini",
    )
    assert s.default_model == "claude-sonnet-4-5"
    assert s.fallback_model == "gpt-4o-mini"


def test_llm_provider_ollama_resolves_defaults() -> None:
    """llm_provider=ollama picks an ollama_chat/* model for native tools."""
    from prismal.core.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="ollama",
        default_model="",
        fallback_model="",
    )
    assert s.default_model.startswith("ollama_chat/")
    assert s.fallback_model == ""  # single-provider setup


def test_llm_provider_ollama_keeps_consistent_model() -> None:
    """A consistent ollama/* default_model is preserved (no override)."""
    from prismal.core.config import Settings

    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="ollama",
        default_model="ollama/mistral",
        fallback_model="",
    )
    assert s.default_model == "ollama/mistral"


def test_llm_provider_conflict_warns_and_overrides() -> None:
    """A cross-provider default_model triggers a warning and is overridden."""
    import warnings

    from prismal.core.config import Settings

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        s = Settings(
            _env_file=None,  # type: ignore[call-arg]
            llm_provider="ollama",
            default_model="claude-sonnet-4-5",
            fallback_model="gpt-4o-mini",
        )

    assert s.default_model.startswith("ollama_chat/")
    assert s.fallback_model == ""
    messages = [str(w.message) for w in captured]
    assert any("PRISMAL_DEFAULT_MODEL" in m for m in messages)
    assert any("PRISMAL_FALLBACK_MODEL" in m for m in messages)


def test_llm_provider_unknown_value_raises() -> None:
    """Unknown provider names raise a clear validation error."""
    from prismal.core.config import Settings

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, llm_provider="cohere")  # type: ignore[call-arg]
    assert "Unknown PRISMAL_LLM_PROVIDER" in str(excinfo.value)


def test_default_model_accepts_prismal_model_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRISMAL_MODEL is accepted as an alias for default_model."""
    from prismal.core.config import Settings

    monkeypatch.delenv("PRISMAL_DEFAULT_MODEL", raising=False)
    monkeypatch.setenv("PRISMAL_MODEL", "ollama/codellama")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.default_model == "ollama/codellama"


def test_blind_review_settings_defaults() -> None:
    """Blind Review Pipeline settings load with their spec defaults (SPEC-BRP-CFG-001)."""
    from prismal.core.config import Settings

    s = Settings()

    assert s.blind_review_pipeline_enabled is False
    assert s.blind_review_spec_model is None
    assert s.blind_review_implementer_model is None
    assert s.blind_review_reviewer_a_model is None
    assert s.blind_review_reviewer_b_model is None
    assert s.blind_review_spec_capabilities == ["docs", "requirements"]
    assert s.blind_review_implementer_capabilities == ["code", "sandbox"]
    assert s.blind_review_reviewer_a_capabilities == ["code_review", "testing"]
    assert s.blind_review_reviewer_b_capabilities == ["security", "style"]
    assert s.blind_review_approval_threshold == 0.8
    assert s.blind_review_max_iterations == 3


def test_blind_review_validation_threshold() -> None:
    """An out-of-range approval threshold raises BlindReviewConfigError (SPEC-BRP-CFG-001)."""
    from prismal.core.config import Settings
    from prismal.core.exceptions import BlindReviewConfigError

    with pytest.raises(BlindReviewConfigError):
        Settings(blind_review_approval_threshold=1.5)
    with pytest.raises(BlindReviewConfigError):
        Settings(blind_review_approval_threshold=-0.1)


def test_blind_review_validation_iterations() -> None:
    """A max-iterations floor below 1 raises BlindReviewConfigError (SPEC-BRP-CFG-001)."""
    from prismal.core.config import Settings
    from prismal.core.exceptions import BlindReviewConfigError

    with pytest.raises(BlindReviewConfigError):
        Settings(blind_review_max_iterations=0)


def test_blind_review_validation_same_model_warns() -> None:
    """A same-model reviewer pair logs a WARNING but does not raise (SPEC-BRP-CFG-001)."""
    from structlog.testing import capture_logs

    from prismal.core.config import Settings

    with capture_logs() as cap_logs:
        s = Settings(
            blind_review_reviewer_a_model="claude-sonnet-4-5",
            blind_review_reviewer_b_model="claude-sonnet-4-5",
        )

    assert s.blind_review_reviewer_a_model == "claude-sonnet-4-5"
    assert any(log.get("event") == "blind_review.reviewers_share_model" for log in cap_logs)
