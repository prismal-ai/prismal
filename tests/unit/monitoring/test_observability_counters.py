"""Unit tests for the observability OTel counters (OBS5-01 — SPEC-OBS-OTEL-001)."""

from __future__ import annotations

from typing import Any

import pytest

from prismal.core.config import Settings
from prismal.monitoring.observability import DefaultObservabilityProvider
from prismal.monitoring.observability_types import DatasetFormat


class _CountingOtel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    def increment_counter(
        self, metric: str, value: int = 1, attributes: dict[str, Any] | None = None
    ) -> None:
        self.calls.append((metric, value, attributes or {}))


@pytest.fixture
def otel(monkeypatch: pytest.MonkeyPatch) -> _CountingOtel:
    counter = _CountingOtel()
    monkeypatch.setattr("prismal.monitoring.observability.OTelManager", lambda: counter)
    # Silence the Langfuse leg so record_score focuses on the counter.
    monkeypatch.setattr("prismal.monitoring.observability.LangfuseManager", lambda: _NoopLangfuse())
    return counter


class _NoopLangfuse:
    def score_trace(self, *a: object, **k: object) -> None:
        pass


def _provider(**overrides: object) -> DefaultObservabilityProvider:
    return DefaultObservabilityProvider(settings=Settings(**overrides))


def test_record_score_increments_scores_counter(otel: _CountingOtel) -> None:
    provider = _provider()
    provider.record_node(
        run_id="a.s.turn0", node_name="a", session_id="s", status="ok", duration_ms=1.0
    )
    provider.record_score(run_id="a.s.turn0", name="groundedness", value=0.9)
    assert ("observability_scores", 1, {"name": "groundedness"}) in otel.calls


def test_export_dataset_increments_export_counter(otel: _CountingOtel) -> None:
    provider = _provider()
    provider.record_node(
        run_id="a.s.turn0", node_name="a", session_id="s", status="ok", duration_ms=1.0
    )
    provider.export_dataset(["a.s.turn0"], fmt=DatasetFormat.LANGFUSE)
    assert ("observability_dataset_exports", 1, {"fmt": "langfuse"}) in otel.calls


def test_eviction_increments_runs_evicted(otel: _CountingOtel) -> None:
    provider = _provider(observability_max_runs=1)
    provider.record_node(
        run_id="old.s.turn0", node_name="n", session_id="s", status="ok", duration_ms=1.0
    )
    provider.record_node(
        run_id="new.s.turn0", node_name="n", session_id="s", status="ok", duration_ms=1.0
    )
    assert ("observability_runs", 1, {"result": "evicted"}) in otel.calls


def test_get_summary_increments_runs_completed_once(otel: _CountingOtel) -> None:
    provider = _provider()
    provider.record_node(
        run_id="a.s.turn0", node_name="n", session_id="s", status="ok", duration_ms=1.0
    )
    provider.get_run_summary("a.s.turn0")
    provider.get_run_summary("a.s.turn0")  # second query must not double-count
    completed = [c for c in otel.calls if c == ("observability_runs", 1, {"result": "completed"})]
    assert len(completed) == 1


def test_unknown_run_summary_does_not_count(otel: _CountingOtel) -> None:
    provider = _provider()
    provider.get_run_summary("ghost")
    completed = [c for c in otel.calls if c[0] == "observability_runs"]
    assert completed == []


def test_counters_registered_on_manager() -> None:
    # The three OBS counter keys are registered by _register_standard_metrics.
    import inspect

    from prismal.monitoring.otel import OTelManager

    source = inspect.getsource(OTelManager._register_standard_metrics)
    assert "prismal.observability_runs_total" in source
    assert "prismal.observability_scores_total" in source
    assert "prismal.observability_dataset_exports_total" in source
