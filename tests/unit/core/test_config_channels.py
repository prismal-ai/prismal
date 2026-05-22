"""Tests for channel-related config fields."""

import pytest

from prismal.core.config import Settings


def test_telegram_bot_token_defaults_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that telegram_bot_token defaults to an empty string when env var is unset."""
    monkeypatch.delenv("PRISMAL_TELEGRAM_BOT_TOKEN", raising=False)
    # Pass _env_file=None so that the .env file on disk is not loaded, ensuring a
    # clean default value regardless of the developer's local credentials.
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.telegram_bot_token.get_secret_value() == ""


def test_slack_bot_token_defaults_empty() -> None:
    """Test that slack_bot_token defaults to an empty string."""
    s = Settings()
    assert s.slack_bot_token.get_secret_value() == ""


def test_channel_security_enabled_default_true() -> None:
    """Test that channel_security_enabled defaults to True."""
    s = Settings()
    assert s.channel_security_enabled is True


def test_signal_api_url_default() -> None:
    """Test that signal_api_url defaults to the local signal-cli-rest-api address."""
    s = Settings()
    assert s.signal_api_url == "http://localhost:8080"


def test_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that channel fields can be overridden via environment variables."""
    monkeypatch.setenv("PRISMAL_TELEGRAM_BOT_TOKEN", "test-token-123")
    s = Settings()
    assert s.telegram_bot_token.get_secret_value() == "test-token-123"
