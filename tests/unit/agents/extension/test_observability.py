"""Tests for tool-provider observability (Fase Y6 — metrics, span, log parity)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from langchain_core.tools import StructuredTool

from prismal.agents import tool_registry
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    StubToolProvider,
)
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.tools import BaseTool


def _make_tool(name: str) -> StructuredTool:
    def _fn() -> str:
        return "ok"

    return StructuredTool.from_function(func=_fn, name=name, description=f"tool {name}")


class _FakeOtel:
    """Recording stand-in for OTelManager."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, Any], MagicMock]] = []
        self.counters: list[tuple[str, int, dict[str, Any]]] = []

    @contextmanager
    def start_span(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> Iterator[MagicMock]:
        span = MagicMock()
        self.spans.append((name, dict(attributes or {}), span))
        yield span

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.counters.append((metric, value, dict(attributes or {})))


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_registry, "_provider", None)


@pytest.fixture
def fake_otel(monkeypatch: pytest.MonkeyPatch) -> _FakeOtel:
    fake = _FakeOtel()
    monkeypatch.setattr("prismal.monitoring.otel.OTelManager", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Y6-01 — counter registration
# ---------------------------------------------------------------------------


class TestCounterRegistration:
    def test_tool_provider_counters_registered(self) -> None:
        mgr = OTelManager.__new__(OTelManager)
        mgr._initialized = True
        mgr._counters = {}
        mgr._histograms = {}
        mgr._meter = MagicMock()
        mgr._register_standard_metrics()

        for key in (
            "tool_provider_resolved",
            "tools_injected",
            "tool_provider_fallback",
            "tool_provider_subprovider_errors",
        ):
            assert key in mgr._counters, key

        registered_names = [call.args[0] for call in mgr._meter.create_counter.call_args_list]
        for name in (
            "prismal.tool_provider_resolved_total",
            "prismal.tools_injected_total",
            "prismal.tool_provider_fallback_total",
            "prismal.tool_provider_subprovider_errors_total",
        ):
            assert name in registered_names, name


# ---------------------------------------------------------------------------
# Y6-02 — span + counters at resolution
# ---------------------------------------------------------------------------


def _span_attrs(fake: _FakeOtel) -> dict[str, Any]:
    """Merge initial attributes and set_attribute calls of the only span."""
    assert len(fake.spans) == 1
    name, attrs, span = fake.spans[0]
    assert name == "prismal.tools.resolve"
    merged = dict(attrs)
    for call in span.set_attribute.call_args_list:
        merged[call.args[0]] = call.args[1]
    return merged


class TestResolutionInstrumentation:
    def test_span_and_counters_with_injected_provider(self, fake_otel: _FakeOtel) -> None:
        provider = FakeToolProvider(default=[_make_tool("a"), _make_tool("b")])
        tool_registry.set_tool_provider(provider)

        tools = tool_registry.get_tools_for_agent("coder")

        attrs = _span_attrs(fake_otel)
        assert attrs["prismal.agent"] == "coder"
        assert attrs["prismal.tool_provider"] == "fake"
        assert attrs["prismal.n_tools"] == len(tools) == 2
        assert attrs["prismal.fallback"] is False

        assert ("tool_provider_resolved", 1, {"provider": "fake"}) in fake_otel.counters
        assert ("tools_injected", 2, {"agent": "coder"}) in fake_otel.counters
        assert all(m != "tool_provider_fallback" for m, _, _ in fake_otel.counters)

    def test_composite_provider_label(self, fake_otel: _FakeOtel) -> None:
        tool_registry.set_tool_provider(CompositeToolProvider([StubToolProvider()]))
        tool_registry.get_tools_for_agent("researcher")

        attrs = _span_attrs(fake_otel)
        assert attrs["prismal.tool_provider"] == "composite"

    def test_fallback_increments_fallback_counter(self, fake_otel: _FakeOtel) -> None:
        tools = tool_registry.get_tools_for_agent("researcher")

        attrs = _span_attrs(fake_otel)
        assert attrs["prismal.tool_provider"] == "stub"
        assert attrs["prismal.fallback"] is True
        assert attrs["prismal.n_tools"] == len(tools)

        assert ("tool_provider_fallback", 1, {}) in fake_otel.counters
        assert ("tool_provider_resolved", 1, {"provider": "stub"}) in fake_otel.counters

    def test_capabilities_recorded_on_span(self, fake_otel: _FakeOtel) -> None:
        tool_registry.set_tool_provider(FakeToolProvider())
        tool_registry.get_tools_for_agent("rag_agent", required_capabilities=["rag", "general"])

        attrs = _span_attrs(fake_otel)
        assert attrs["prismal.capabilities"] == "rag,general"

    def test_ctx_provider_is_instrumented(self, fake_otel: _FakeOtel) -> None:
        ctx_provider = FakeToolProvider(default=[_make_tool("ctx_t")])
        config = {"configurable": {"tool_provider": ctx_provider}}

        tool_registry.get_tools_for_agent_ctx("coder", config)

        attrs = _span_attrs(fake_otel)
        assert attrs["prismal.agent"] == "coder"
        assert attrs["prismal.tool_provider"] == "fake"
        assert attrs["prismal.n_tools"] == 1
        assert ("tool_provider_resolved", 1, {"provider": "fake"}) in fake_otel.counters


# ---------------------------------------------------------------------------
# Y6 — subprovider error counter (CompositeToolProvider)
# ---------------------------------------------------------------------------


class TestSubproviderErrorCounter:
    def test_raising_subprovider_increments_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeOtel()
        monkeypatch.setattr("prismal.monitoring.otel.OTelManager", lambda: fake)

        class _Boom:
            def get_tools(
                self, *, agent_name: str, capabilities: list[str] | None = None
            ) -> list[BaseTool]:
                raise RuntimeError("down")

        composite = CompositeToolProvider([_Boom(), StubToolProvider()])
        composite.get_tools(agent_name="researcher")

        assert (
            "tool_provider_subprovider_errors",
            1,
            {"provider": "_Boom"},
        ) in fake.counters


# ---------------------------------------------------------------------------
# Y6-02 — log parity (tool_provider.tools_resolved fields)
# ---------------------------------------------------------------------------


class TestLogParity:
    def test_tools_resolved_log_fields(self) -> None:
        from structlog.testing import capture_logs

        composite = CompositeToolProvider(
            [FakeToolProvider(default=[_make_tool("live_a")]), StubToolProvider()]
        )
        with capture_logs() as logs:
            composite.get_tools(agent_name="researcher")

        resolved = [e for e in logs if e["event"] == "tool_provider.tools_resolved"]
        assert resolved, logs
        entry = resolved[0]
        # Parity with the historical tool_registry.tools_resolved fields.
        assert entry["agent"] == "researcher"
        assert {"live", "stubs_kept", "total"} <= entry.keys()
        assert entry["live"] == 1
        assert entry["total"] == entry["live"] + entry["stubs_kept"]
