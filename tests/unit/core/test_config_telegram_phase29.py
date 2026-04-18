"""Tests for Phase 29 Telegram Enhanced Bot config fields (T-301)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from lightagent.core.config import Settings

# ── Default values ─────────────────────────────────────────────────────────────


def test_telegram_webhook_enabled_defaults_false() -> None:
    """telegram_webhook_enabled defaults to False."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_webhook_enabled is False


def test_telegram_webhook_url_defaults_empty() -> None:
    """telegram_webhook_url defaults to empty string."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_webhook_url == ""


def test_telegram_webhook_secret_defaults_empty() -> None:
    """telegram_webhook_secret defaults to empty SecretStr."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_webhook_secret.get_secret_value() == ""


def test_telegram_max_connections_defaults_40() -> None:
    """telegram_max_connections defaults to 40."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_max_connections == 40


def test_telegram_session_inline_keyboard_defaults_true() -> None:
    """telegram_session_inline_keyboard defaults to True."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_session_inline_keyboard is True


def test_telegram_max_sessions_per_user_defaults_10() -> None:
    """telegram_max_sessions_per_user defaults to 10."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_max_sessions_per_user == 10


def test_telegram_message_track_defaults_true() -> None:
    """telegram_message_track defaults to True."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_message_track is True


def test_telegram_parse_mode_defaults_html() -> None:
    """telegram_parse_mode defaults to 'HTML'."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_parse_mode == "HTML"


# ── Webhook validator — happy path ─────────────────────────────────────────────


def test_webhook_enabled_with_valid_url_and_secret_passes() -> None:
    """Valid webhook config (https URL + secret) passes validation."""
    s = Settings(
        _env_file=None,  # type: ignore[call-arg]
        telegram_webhook_enabled=True,
        telegram_webhook_url="https://example.com/webhook",
        telegram_webhook_secret=SecretStr("mysecret"),
    )
    assert s.telegram_webhook_enabled is True


def test_webhook_disabled_without_url_passes() -> None:
    """Webhook disabled (default) with no URL still passes validation."""
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_webhook_enabled is False


# ── Webhook validator — rejection ──────────────────────────────────────────────


def test_webhook_enabled_with_http_url_raises() -> None:
    """telegram_webhook_url must start with https:// when webhook is enabled."""
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            telegram_webhook_enabled=True,
            telegram_webhook_url="http://example.com/webhook",
            telegram_webhook_secret=SecretStr("mysecret"),
        )


def test_webhook_enabled_without_secret_raises() -> None:
    """telegram_webhook_secret must be set when webhook is enabled."""
    with pytest.raises(ValidationError, match="SECRET"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            telegram_webhook_enabled=True,
            telegram_webhook_url="https://example.com/webhook",
            telegram_webhook_secret=SecretStr(""),
        )


# ── Bounds ─────────────────────────────────────────────────────────────────────


def test_telegram_max_connections_lower_bound() -> None:
    """telegram_max_connections accepts 1 (minimum)."""
    s = Settings(_env_file=None, telegram_max_connections=1)  # type: ignore[call-arg]
    assert s.telegram_max_connections == 1


def test_telegram_max_connections_upper_bound() -> None:
    """telegram_max_connections accepts 100 (maximum)."""
    s = Settings(_env_file=None, telegram_max_connections=100)  # type: ignore[call-arg]
    assert s.telegram_max_connections == 100


def test_telegram_max_connections_below_min_raises() -> None:
    """telegram_max_connections rejects 0 (below minimum)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_max_connections=0)  # type: ignore[call-arg]


def test_telegram_max_connections_above_max_raises() -> None:
    """telegram_max_connections rejects 101 (above maximum)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_max_connections=101)  # type: ignore[call-arg]


def test_telegram_max_sessions_per_user_min_is_one() -> None:
    """telegram_max_sessions_per_user accepts 1 (minimum)."""
    s = Settings(_env_file=None, telegram_max_sessions_per_user=1)  # type: ignore[call-arg]
    assert s.telegram_max_sessions_per_user == 1


def test_telegram_max_sessions_per_user_zero_raises() -> None:
    """telegram_max_sessions_per_user rejects 0 (below minimum)."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None, telegram_max_sessions_per_user=0)  # type: ignore[call-arg]


# ── Env-var overrides ──────────────────────────────────────────────────────────


def test_env_override_telegram_webhook_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIGHTAGENT_TELEGRAM_WEBHOOK_ENABLED=true overrides default."""
    monkeypatch.setenv("LIGHTAGENT_TELEGRAM_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("LIGHTAGENT_TELEGRAM_WEBHOOK_URL", "https://bot.example.com/hook")
    monkeypatch.setenv("LIGHTAGENT_TELEGRAM_WEBHOOK_SECRET", "supersecret")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_webhook_enabled is True
    assert s.telegram_webhook_url == "https://bot.example.com/hook"
    assert s.telegram_webhook_secret.get_secret_value() == "supersecret"


def test_env_override_telegram_parse_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """LIGHTAGENT_TELEGRAM_PARSE_MODE overrides default HTML."""
    monkeypatch.setenv("LIGHTAGENT_TELEGRAM_PARSE_MODE", "MarkdownV2")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_parse_mode == "MarkdownV2"


def test_env_override_telegram_message_track_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIGHTAGENT_TELEGRAM_MESSAGE_TRACK=false disables tracking."""
    monkeypatch.setenv("LIGHTAGENT_TELEGRAM_MESSAGE_TRACK", "false")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_message_track is False
