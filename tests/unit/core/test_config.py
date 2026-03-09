"""Unit tests for core Settings configuration."""

import pytest
from pydantic import ValidationError


def test_settings_default_model() -> None:
    """Settings loads default_model with fallback value."""
    from lightagent.core.config import Settings

    s = Settings()
    assert s.default_model == "claude-sonnet-4-6"


def test_settings_fallback_model() -> None:
    """Settings has a fallback_model defined."""
    from lightagent.core.config import Settings

    s = Settings()
    assert s.fallback_model == "gpt-4o-mini"


def test_settings_security_mode_default() -> None:
    """Security mode defaults to 'strict'."""
    from lightagent.core.config import Settings

    s = Settings()
    assert s.security_mode == "strict"


def test_settings_security_mode_invalid() -> None:
    """Invalid security_mode raises ValidationError."""
    from lightagent.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(security_mode="unknown")  # type: ignore[arg-type]


def test_settings_risk_threshold_default() -> None:
    """Risk threshold defaults to 30."""
    from lightagent.core.config import Settings

    s = Settings()
    assert s.risk_threshold == 30


def test_settings_risk_threshold_out_of_range() -> None:
    """Risk threshold must be 0-100."""
    from lightagent.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(risk_threshold=150)


def test_settings_db_url_default() -> None:
    """DB URL defaults to SQLite path."""
    from lightagent.core.config import Settings

    s = Settings()
    assert "sqlite" in s.db_url


def test_settings_ports() -> None:
    """API and dashboard ports are defined."""
    from lightagent.core.config import Settings

    s = Settings()
    assert s.port == 8000
    assert s.dashboard_port == 3000


def test_settings_shell_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell execution is disabled by default (security)."""
    from lightagent.core.config import Settings

    monkeypatch.delenv("LIGHTAGENT_SHELL_ENABLED", raising=False)
    # Pass _env_file=None so .env on disk cannot override the default.
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.shell_enabled is False


def test_get_settings_cache_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_settings() returns the same cached instance; cache can be cleared."""
    from lightagent.core.config import get_settings

    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    get_settings.cache_clear()


def test_settings_env_var_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIGHTAGENT_ env vars override defaults."""
    from lightagent.core.config import Settings, get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LIGHTAGENT_DEFAULT_MODEL", "gpt-4o")
    s = Settings()  # fresh instance, not cached
    assert s.default_model == "gpt-4o"
    get_settings.cache_clear()


def test_cron_notify_defaults_empty() -> None:
    """Cron notification targets default to empty strings (opt-in)."""
    from lightagent.core.config import Settings

    s = Settings()
    assert s.cron_notify_telegram_chat_id == ""
    assert s.cron_notify_slack_channel == ""
