"""Tests for LangfuseManager singleton (T-131)."""

from __future__ import annotations

from unittest.mock import patch

from prismal.monitoring.langfuse_client import LangfuseManager, _NoOpTrace


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
    with patch("prismal.monitoring.langfuse_client.logger"):
        with patch(
            "prismal.monitoring._settings_proxy.get_monitoring_settings"
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
    with patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
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
    with patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
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
    with patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.langfuse_enabled = False
        s.langfuse_public_key.get_secret_value.return_value = ""
        s.langfuse_secret_key.get_secret_value.return_value = ""
        mock_settings.return_value = s
        mgr = LangfuseManager()
    mgr.flush()  # must not raise
    mgr.shutdown()  # must not raise


# ── _NoOpTrace / _NoOpGeneration methods ──────────────────────────────────────


def test_noop_trace_score_and_generation() -> None:
    """_NoOpTrace.score() and .generation() are callable without error."""
    trace = _NoOpTrace()
    trace.score(value=0.9, name="accuracy")  # must not raise
    gen = trace.generation(name="llm-call", model="gpt-4")
    assert gen is not None
    gen.end(output="result")  # must not raise
    gen.update(usage={"tokens": 10})  # must not raise


# ── LangfuseManager — initialization paths ────────────────────────────────────


def test_langfuse_enabled_creates_client() -> None:
    """Manager creates a real Langfuse client when keys are present."""
    from unittest.mock import MagicMock, patch

    _reset_singleton()

    mock_client = MagicMock()
    mock_langfuse_cls = MagicMock(return_value=mock_client)
    langfuse_stub = MagicMock()
    langfuse_stub.Langfuse = mock_langfuse_cls

    with (
        patch.dict("sys.modules", {"langfuse": langfuse_stub}),
        patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_s,
    ):
        s = MagicMock()
        s.langfuse_enabled = True
        s.langfuse_public_key.get_secret_value.return_value = "pk-test"
        s.langfuse_secret_key.get_secret_value.return_value = "sk-test"
        s.langfuse_host = "https://cloud.langfuse.com"
        mock_s.return_value = s
        mgr = LangfuseManager()

    assert mgr.enabled is True
    mock_langfuse_cls.assert_called_once_with(
        public_key="pk-test",
        secret_key="sk-test",
        host="https://cloud.langfuse.com",
    )


def test_langfuse_init_exception_disables() -> None:
    """Exception in _setup disables the manager (fail-open)."""
    from unittest.mock import MagicMock, patch

    _reset_singleton()

    langfuse_stub = MagicMock()
    langfuse_stub.Langfuse.side_effect = RuntimeError("network unreachable")

    with (
        patch.dict("sys.modules", {"langfuse": langfuse_stub}),
        patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_s,
    ):
        s = MagicMock()
        s.langfuse_enabled = True
        s.langfuse_public_key.get_secret_value.return_value = "pk"
        s.langfuse_secret_key.get_secret_value.return_value = "sk"
        s.langfuse_host = "https://cloud.langfuse.com"
        mock_s.return_value = s
        mgr = LangfuseManager()

    assert mgr.enabled is False


# ── LangfuseManager — enabled-path helpers ────────────────────────────────────


def _make_enabled_manager() -> tuple[LangfuseManager, object]:
    """Return an enabled LangfuseManager with a mock internal client."""
    from unittest.mock import MagicMock

    _reset_singleton()
    mgr = LangfuseManager.__new__(LangfuseManager)
    mgr._initialized = True  # type: ignore[attr-defined]
    mock_client = MagicMock()
    mgr._client = mock_client  # type: ignore[attr-defined]
    mgr.enabled = True  # type: ignore[attr-defined]
    LangfuseManager._instance = mgr
    return mgr, mock_client


# ── create_trace when enabled ─────────────────────────────────────────────────


def test_create_trace_when_enabled_returns_real_trace() -> None:
    """create_trace() delegates to self._client.trace() when enabled."""
    from unittest.mock import MagicMock

    mgr, mock_client = _make_enabled_manager()
    fake_trace = MagicMock()
    mock_client.trace.return_value = fake_trace

    result = mgr.create_trace(
        name="agent.run",
        session_id="s-1",
        user_id="u-1",
        metadata={"key": "val"},
    )
    assert result is fake_trace
    mock_client.trace.assert_called_once_with(
        name="agent.run",
        session_id="s-1",
        user_id="u-1",
        metadata={"key": "val"},
    )


def test_create_trace_minimal_kwargs() -> None:
    """create_trace() only passes non-None optional kwargs."""
    from unittest.mock import MagicMock

    mgr, mock_client = _make_enabled_manager()
    mock_client.trace.return_value = MagicMock()

    mgr.create_trace(name="ping")
    call_kwargs = mock_client.trace.call_args[1]
    assert "session_id" not in call_kwargs
    assert "user_id" not in call_kwargs


def test_create_trace_exception_returns_noop() -> None:
    """create_trace() returns a _NoOpTrace when the client raises."""
    mgr, mock_client = _make_enabled_manager()
    mock_client.trace.side_effect = RuntimeError("SDK error")

    result = mgr.create_trace(name="fail")
    assert isinstance(result, _NoOpTrace)


# ── get_callback_handler when enabled ────────────────────────────────────────


def test_get_callback_handler_when_enabled() -> None:
    """get_callback_handler() returns a handler when enabled."""
    from unittest.mock import MagicMock, patch

    mgr, _ = _make_enabled_manager()

    mock_handler = MagicMock()
    mock_handler_cls = MagicMock(return_value=mock_handler)
    langchain_stub = MagicMock()
    langchain_stub.CallbackHandler = mock_handler_cls

    with patch.dict("sys.modules", {"langfuse.langchain": langchain_stub}):
        result = mgr.get_callback_handler(trace_id="t-1", session_id="s-1")

    assert result is mock_handler
    mock_handler_cls.assert_called_once_with(trace_id="t-1", session_id="s-1")


def test_get_callback_handler_exception_returns_none() -> None:
    """get_callback_handler() returns None when the import/handler raises."""
    from unittest.mock import patch

    mgr, _ = _make_enabled_manager()

    with patch.dict("sys.modules", {"langfuse.langchain": None}):
        result = mgr.get_callback_handler()

    assert result is None


# ── score_trace when enabled ──────────────────────────────────────────────────


def test_score_trace_when_enabled() -> None:
    """score_trace() calls client.score with the correct arguments."""
    mgr, mock_client = _make_enabled_manager()

    mgr.score_trace(trace_id="t-001", name="relevance", value=0.9, comment="good")

    mock_client.score.assert_called_once_with(
        trace_id="t-001",
        name="relevance",
        value=0.9,
        comment="good",
    )


def test_score_trace_noop_when_disabled() -> None:
    """score_trace() is a safe no-op when disabled."""
    from unittest.mock import MagicMock, patch

    _reset_singleton()
    with patch("prismal.monitoring._settings_proxy.get_monitoring_settings") as mock_s:
        s = MagicMock()
        s.langfuse_enabled = False
        s.langfuse_public_key.get_secret_value.return_value = ""
        s.langfuse_secret_key.get_secret_value.return_value = ""
        mock_s.return_value = s
        mgr = LangfuseManager()
    mgr.score_trace(trace_id="t", name="x", value=0.5)  # must not raise


def test_score_trace_exception_handled() -> None:
    """score_trace() logs a warning and does not raise on SDK error."""
    mgr, mock_client = _make_enabled_manager()
    mock_client.score.side_effect = RuntimeError("SDK error")

    mgr.score_trace(trace_id="t", name="x", value=1.0)  # must not raise


# ── flush when enabled ────────────────────────────────────────────────────────


def test_flush_when_enabled() -> None:
    """flush() calls client.flush() when enabled."""
    mgr, mock_client = _make_enabled_manager()
    mgr.flush()
    mock_client.flush.assert_called_once()


def test_flush_exception_handled() -> None:
    """flush() logs a warning and does not raise on SDK error."""
    mgr, mock_client = _make_enabled_manager()
    mock_client.flush.side_effect = RuntimeError("flush failed")
    mgr.flush()  # must not raise


# ── shutdown ──────────────────────────────────────────────────────────────────


def test_shutdown_when_enabled_calls_flush() -> None:
    """shutdown() flushes when enabled."""
    mgr, mock_client = _make_enabled_manager()
    mgr.shutdown()
    mock_client.flush.assert_called_once()
