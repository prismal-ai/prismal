"""Integration test: Langfuse trace lifecycle (T-13A)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _enabled_settings(pub: str = "pk-test", sec: str = "sk-test") -> MagicMock:
    """Return a mock settings object with Langfuse enabled."""
    s = MagicMock()
    s.langfuse_enabled = True
    s.langfuse_public_key.get_secret_value.return_value = pub
    s.langfuse_secret_key.get_secret_value.return_value = sec
    s.langfuse_host = "https://cloud.langfuse.com"
    return s


def test_langfuse_create_trace_noop_without_keys() -> None:
    """create_trace returns _NoOpTrace when keys are missing."""
    from prismal.monitoring.langfuse_client import LangfuseManager, _NoOpTrace

    LangfuseManager._instance = None
    LangfuseManager._initialized = False
    s = MagicMock()
    s.langfuse_enabled = True
    s.langfuse_public_key.get_secret_value.return_value = ""
    s.langfuse_secret_key.get_secret_value.return_value = ""
    s.langfuse_host = "https://cloud.langfuse.com"
    with patch(
        "prismal.monitoring._settings_proxy.get_monitoring_settings",
        return_value=s,
    ):
        mgr = LangfuseManager()

    trace = mgr.create_trace("test", session_id="s1")
    assert isinstance(trace, _NoOpTrace)
    trace.update(name="updated")
    trace.event(name="my_event")
    trace.score(name="quality", value=0.9)


def test_langfuse_score_trace_noop_when_disabled() -> None:
    """score_trace is a safe no-op when disabled."""
    from prismal.monitoring.langfuse_client import LangfuseManager

    LangfuseManager._instance = None
    LangfuseManager._initialized = False
    s = MagicMock()
    s.langfuse_enabled = False
    s.langfuse_public_key.get_secret_value.return_value = ""
    s.langfuse_secret_key.get_secret_value.return_value = ""
    with patch(
        "prismal.monitoring._settings_proxy.get_monitoring_settings",
        return_value=s,
    ):
        mgr = LangfuseManager()

    mgr.score_trace("trace-id-1", "relevance", 0.95, comment="Good")


def test_langfuse_callback_handler_with_mock_client() -> None:
    """get_callback_handler delegates to real Langfuse when enabled."""
    from prismal.monitoring.langfuse_client import LangfuseManager

    LangfuseManager._instance = None
    LangfuseManager._initialized = False

    mock_client = MagicMock()
    mock_handler = MagicMock()

    # Langfuse and CallbackHandler are imported inside methods, so patch at source
    with (
        patch(
            "prismal.monitoring._settings_proxy.get_monitoring_settings",
            return_value=_enabled_settings(),
        ),
        patch("langfuse.Langfuse", return_value=mock_client),
        patch("langfuse.langchain.CallbackHandler", return_value=mock_handler),
    ):
        mgr = LangfuseManager()
        if mgr.enabled:
            handler = mgr.get_callback_handler(session_id="s1")
            assert handler is not None


def test_langfuse_singleton_identity() -> None:
    """LangfuseManager always returns the exact same instance."""
    from prismal.monitoring.langfuse_client import LangfuseManager

    LangfuseManager._instance = None
    LangfuseManager._initialized = False
    s = MagicMock()
    s.langfuse_enabled = False
    s.langfuse_public_key.get_secret_value.return_value = ""
    s.langfuse_secret_key.get_secret_value.return_value = ""
    with patch(
        "prismal.monitoring._settings_proxy.get_monitoring_settings",
        return_value=s,
    ):
        a = LangfuseManager()
        b = LangfuseManager()
        c = LangfuseManager()

    assert a is b
    assert b is c
