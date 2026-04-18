"""Tests for OTelManager singleton (T-132)."""

from __future__ import annotations

from unittest.mock import patch

from lightagent.monitoring.otel import OTelManager, _NoOpSpan


def _reset_singleton() -> None:
    OTelManager._instance = None
    OTelManager._initialized = False


def test_otel_manager_is_singleton() -> None:
    """Two calls return the same instance."""
    _reset_singleton()
    a = OTelManager()
    b = OTelManager()
    assert a is b


def test_start_span_returns_noop_when_disabled() -> None:
    """start_span returns a _NoOpSpan context manager when OTEL disabled."""
    _reset_singleton()
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    assert not mgr.enabled
    with mgr.start_span("test.op") as span:
        assert isinstance(span, _NoOpSpan)
        span.set_attribute("key", "value")  # must not raise


def test_noop_span_methods() -> None:
    """All _NoOpSpan methods are callable without error."""
    span = _NoOpSpan()
    span.set_attribute("foo", "bar")
    span.add_event("my_event", {"k": "v"})
    span.record_exception(ValueError("test"))
    span.set_status(None)


def test_increment_counter_noop_when_disabled() -> None:
    """increment_counter is safe no-op when OTEL disabled."""
    _reset_singleton()
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    mgr.increment_counter("llm_requests")  # must not raise


def test_record_histogram_noop_when_disabled() -> None:
    """record_histogram is safe no-op when OTEL disabled."""
    _reset_singleton()
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    mgr.record_histogram("agent_latency", 0.5)  # must not raise


def test_start_span_with_attributes_noop() -> None:
    """start_span accepts attributes when disabled."""
    _reset_singleton()
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    with mgr.start_span("op", attributes={"foo": "bar"}) as span:
        assert isinstance(span, _NoOpSpan)


# ── OTelManager — enabled-path helper ─────────────────────────────────────────


def _make_enabled_otel(
    counters: dict | None = None,
    histograms: dict | None = None,
) -> tuple[OTelManager, object, object]:
    """Return an enabled OTelManager with mock tracer and meter."""
    from unittest.mock import MagicMock

    _reset_singleton()
    mgr = OTelManager.__new__(OTelManager)
    mgr._initialized = True  # type: ignore[attr-defined]
    mock_tracer = MagicMock()
    mock_meter = MagicMock()
    mgr._tracer = mock_tracer  # type: ignore[attr-defined]
    mgr._meter = mock_meter  # type: ignore[attr-defined]
    mgr.enabled = True  # type: ignore[attr-defined]
    mgr._counters = counters or {}  # type: ignore[attr-defined]
    mgr._histograms = histograms or {}  # type: ignore[attr-defined]
    OTelManager._instance = mgr
    return mgr, mock_tracer, mock_meter


# ── increment_counter ──────────────────────────────────────────────────────────


def test_increment_counter_when_counter_exists() -> None:
    """increment_counter() calls counter.add with value and attributes."""
    from unittest.mock import MagicMock

    mock_counter = MagicMock()
    mgr, _, _ = _make_enabled_otel(counters={"llm_requests": mock_counter})

    mgr.increment_counter("llm_requests", value=3, attributes={"model": "gpt-4"})
    mock_counter.add.assert_called_once_with(3, {"model": "gpt-4"})


def test_increment_counter_default_value_is_1() -> None:
    """increment_counter() defaults to value=1 when not specified."""
    from unittest.mock import MagicMock

    mock_counter = MagicMock()
    mgr, _, _ = _make_enabled_otel(counters={"agent_errors": mock_counter})

    mgr.increment_counter("agent_errors")
    mock_counter.add.assert_called_once_with(1, {})


def test_increment_counter_unknown_key_is_noop() -> None:
    """increment_counter() silently ignores unknown metric names."""
    mgr, _, _ = _make_enabled_otel()
    mgr.increment_counter("nonexistent_metric")  # must not raise


def test_increment_counter_exception_handled() -> None:
    """increment_counter() swallows exceptions from counter.add."""
    from unittest.mock import MagicMock

    mock_counter = MagicMock()
    mock_counter.add.side_effect = RuntimeError("metrics error")
    mgr, _, _ = _make_enabled_otel(counters={"llm_requests": mock_counter})

    mgr.increment_counter("llm_requests")  # must not raise


# ── record_histogram ───────────────────────────────────────────────────────────


def test_record_histogram_when_histogram_exists() -> None:
    """record_histogram() calls histogram.record with value and attributes."""
    from unittest.mock import MagicMock

    mock_histogram = MagicMock()
    mgr, _, _ = _make_enabled_otel(histograms={"agent_latency": mock_histogram})

    mgr.record_histogram("agent_latency", 1.5, attributes={"status": "ok"})
    mock_histogram.record.assert_called_once_with(1.5, {"status": "ok"})


def test_record_histogram_unknown_key_is_noop() -> None:
    """record_histogram() silently ignores unknown metric names."""
    mgr, _, _ = _make_enabled_otel()
    mgr.record_histogram("nonexistent", 0.5)  # must not raise


def test_record_histogram_exception_handled() -> None:
    """record_histogram() swallows exceptions from histogram.record."""
    from unittest.mock import MagicMock

    mock_histogram = MagicMock()
    mock_histogram.record.side_effect = RuntimeError("histogram error")
    mgr, _, _ = _make_enabled_otel(histograms={"llm_latency": mock_histogram})

    mgr.record_histogram("llm_latency", 2.0)  # must not raise


# ── start_span when enabled ────────────────────────────────────────────────────


def test_start_span_when_enabled_uses_tracer() -> None:
    """start_span() uses the real tracer when enabled."""
    from unittest.mock import MagicMock

    mock_span = MagicMock()
    mgr, mock_tracer, _ = _make_enabled_otel()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with mgr.start_span("test.op") as span:
        assert span is mock_span

    mock_tracer.start_as_current_span.assert_called_once_with("test.op")


def test_start_span_with_attributes_sets_them() -> None:
    """start_span() sets attributes on the span when provided."""
    from unittest.mock import MagicMock

    mock_span = MagicMock()
    mgr, mock_tracer, _ = _make_enabled_otel()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

    with mgr.start_span("op", attributes={"key": "val"}):
        pass

    mock_span.set_attribute.assert_called_with("key", "val")


def test_start_span_records_exception_and_reraises() -> None:
    """start_span() records an exception on the span and re-raises it."""
    from unittest.mock import MagicMock

    import pytest

    mock_span = MagicMock()
    mgr, mock_tracer, _ = _make_enabled_otel()
    ctx = mock_tracer.start_as_current_span.return_value
    ctx.__enter__ = MagicMock(return_value=mock_span)
    # Return False so the exception propagates
    ctx.__exit__ = MagicMock(return_value=False)

    with pytest.raises(ValueError, match="boom"), mgr.start_span("fail.op"):
        raise ValueError("boom")

    mock_span.record_exception.assert_called_once()


# ── shutdown ───────────────────────────────────────────────────────────────────


def test_shutdown_noop_when_disabled() -> None:
    """shutdown() returns immediately when not enabled."""
    _reset_singleton()
    with patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings:
        from unittest.mock import MagicMock

        s = MagicMock()
        s.otel_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()
    mgr.shutdown()  # must not raise, returns early


def test_shutdown_when_enabled() -> None:
    """shutdown() calls provider.shutdown() when OTEL is enabled."""
    from unittest.mock import MagicMock, patch

    mgr, _, _ = _make_enabled_otel()

    mock_tp = MagicMock()
    mock_mp = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "opentelemetry": MagicMock(
                trace=MagicMock(get_tracer_provider=MagicMock(return_value=mock_tp)),
                metrics=MagicMock(get_meter_provider=MagicMock(return_value=mock_mp)),
            )
        },
    ):
        # Import the already-imported opentelemetry from inside shutdown
        # We patch the local-import names by patching sys.modules
        import sys
        import types

        otel_stub = types.ModuleType("opentelemetry")
        otel_trace_stub = types.ModuleType("opentelemetry.trace")
        otel_metrics_stub = types.ModuleType("opentelemetry.metrics")

        otel_trace_stub.get_tracer_provider = MagicMock(return_value=mock_tp)
        otel_metrics_stub.get_meter_provider = MagicMock(return_value=mock_mp)
        otel_stub.trace = otel_trace_stub
        otel_stub.metrics = otel_metrics_stub

        with patch.dict(
            sys.modules,
            {
                "opentelemetry": otel_stub,
                "opentelemetry.trace": otel_trace_stub,
                "opentelemetry.metrics": otel_metrics_stub,
            },
        ):
            mgr.shutdown()

    mock_tp.shutdown.assert_called_once()
    mock_mp.shutdown.assert_called_once()


# ── _probe_endpoint ────────────────────────────────────────────────────────────


def test_probe_endpoint_returns_true_when_reachable() -> None:
    """_probe_endpoint() returns True when the TCP connection succeeds."""
    from unittest.mock import MagicMock, patch

    _reset_singleton()
    mgr = OTelManager.__new__(OTelManager)
    mgr._initialized = True  # type: ignore[attr-defined]
    mgr.enabled = False  # type: ignore[attr-defined]

    mock_sock = MagicMock()
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)

    with patch(
        "lightagent.monitoring.otel.socket.create_connection",
        return_value=mock_sock,
    ):
        assert mgr._probe_endpoint("http://localhost:4318") is True


def test_probe_endpoint_returns_false_when_refused() -> None:
    """_probe_endpoint() returns False when connection is refused."""
    from unittest.mock import patch

    _reset_singleton()
    mgr = OTelManager.__new__(OTelManager)
    mgr._initialized = True  # type: ignore[attr-defined]
    mgr.enabled = False  # type: ignore[attr-defined]

    with patch(
        "lightagent.monitoring.otel.socket.create_connection",
        side_effect=OSError(111, "Connection refused"),
    ):
        assert mgr._probe_endpoint("http://localhost:4318") is False


def test_setup_disables_otel_when_endpoint_unreachable() -> None:
    """OTelManager stays disabled when OTLP endpoint is unreachable."""
    from unittest.mock import MagicMock, patch

    _reset_singleton()
    with (
        patch("lightagent.monitoring._settings_proxy.get_monitoring_settings") as mock_settings,
        patch(
            "lightagent.monitoring.otel.socket.create_connection",
            side_effect=OSError(111, "Connection refused"),
        ),
    ):
        s = MagicMock()
        s.otel_enabled = True
        s.otel_exporter = "otlp"
        s.otel_endpoint = "http://localhost:4318"
        s.otel_service_name = "lightagent"
        s.otel_metrics_enabled = False
        mock_settings.return_value = s
        mgr = OTelManager()

    assert not mgr.enabled
