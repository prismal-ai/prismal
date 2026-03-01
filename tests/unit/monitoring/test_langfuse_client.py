"""Tests for LangfuseManager singleton (T-131)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lightagent.monitoring.langfuse_client import LangfuseManager, _NoOpTrace


def _reset_singleton() -> None:
    LangfuseManager._instance = None
    LangfuseManager._initialized = False


def test_langfuse_manager_is_singleton() -> None:
    """Two calls return the same instance."""
    _reset_singleton()
    a = LangfuseManager()
    b = LangfuseManager()
    assert a is b


def test_langfuse_disabled_without_keys() -> None:
    """Manager is disabled when keys are not configured."""
    _reset_singleton()
    with patch("lightagent.monitoring.langfuse_client.logger"):
        with patch(
            "lightagent.monitoring._settings_proxy.get_monitoring_settings"
        ) as mock_settings:
            from unittest.mock import MagicMock

            s = MagicMock()
            s.langfuse_enabled = True
            s.langfuse_public_key.get_secret_value.return_value = ""
            s.langfuse_secret_key.get_secret_value.return_value = ""
            s.langfuse_host = "https://cloud.langfuse.com"
            mock_settings.return_value = s
            mgr = LangfuseManager()
    assert not mgr.enabled


def test_create_trace_returns_noop_when_disabled() -> None:
    """create_trace returns a no-op object when Langfuse is disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.langfuse_enabled = False
        s.langfuse_public_key.get_secret_value.return_value = ""
        s.langfuse_secret_key.get_secret_value.return_value = ""
        mock_settings.return_value = s
        mgr = LangfuseManager()
    trace = mgr.create_trace(name="test")
    assert trace is not None
    assert isinstance(trace, _NoOpTrace)


def test_get_callback_handler_returns_none_when_disabled() -> None:
    """get_callback_handler returns None when Langfuse is disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.langfuse_enabled = False
        s.langfuse_public_key.get_secret_value.return_value = ""
        s.langfuse_secret_key.get_secret_value.return_value = ""
        mock_settings.return_value = s
        mgr = LangfuseManager()
    handler = mgr.get_callback_handler()
    assert handler is None


def test_flush_and_shutdown_noop_when_disabled() -> None:
    """flush/shutdown are safe no-ops when disabled."""
    _reset_singleton()
    with patch(
        "lightagent.monitoring._settings_proxy.get_monitoring_settings"
    ) as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.langfuse_enabled = False
        s.langfuse_public_key.get_secret_value.return_value = ""
        s.langfuse_secret_key.get_secret_value.return_value = ""
        mock_settings.return_value = s
        mgr = LangfuseManager()
    mgr.flush()  # must not raise
    mgr.shutdown()  # must not raise
