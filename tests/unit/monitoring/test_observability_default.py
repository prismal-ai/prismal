"""Unit tests for DefaultObservabilityProvider (OBS2-02 — SPEC-OBS-PRT-001, DD-OBS-003/006)."""

from __future__ import annotations

from prismal.agents.extension.ports import ObservabilityPort, conforms_to
from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.monitoring.observability import DefaultObservabilityProvider
from prismal.monitoring.observability_types import ToolCallRecord


def _provider(**overrides: object) -> DefaultObservabilityProvider:
    return DefaultObservabilityProvider(settings=Settings(**overrides))


def test_conforms_to_port() -> None:
    assert conforms_to(_provider(), ObservabilityPort) is True


def test_record_node_never_raises_with_backends_disabled() -> None:
    # OTel/Langfuse are unconfigured in tests → must degrade silently, not raise.
    provider = _provider()
    provider.record_node(
        run_id="coder.s.turn0",
        node_name="coder",
        session_id="s",
        status="ok",
        duration_ms=3.0,
        tool_calls=[ToolCallRecord(tool_name="t", node="coder", ok=True)],
        usage=Usage(prompt_tokens=2, completion_tokens=1, calls=1),
    )
    summary = provider.get_run_summary("coder.s.turn0")
    assert summary is not None
    assert summary.visited_nodes == ["coder"]
    assert summary.usage.total_tokens == 3


def test_record_score_never_raises_and_stores_local() -> None:
    provider = _provider()
    provider.record_node(
        run_id="coder.s.turn0", node_name="coder", session_id="s", status="ok", duration_ms=1.0
    )
    provider.record_score(run_id="coder.s.turn0", name="human_review", value=1.0, source="human")
    summary = provider.get_run_summary("coder.s.turn0")
    assert summary is not None
    assert summary.scores[0].name == "human_review"


def test_ring_buffer_caps_spans_keeping_most_recent() -> None:
    provider = _provider(observability_run_buffer_size=2)
    for i in range(3):
        provider.record_node(
            run_id="a.s.turn0",
            node_name=f"node{i}",
            session_id="s",
            status="ok",
            duration_ms=1.0,
        )
    summary = provider.get_run_summary("a.s.turn0")
    assert summary is not None
    # Only the 2 most-recent spans survive the ring buffer.
    assert [s.node for s in summary.spans] == ["node1", "node2"]


def test_lru_evicts_oldest_run_over_max_runs() -> None:
    provider = _provider(observability_max_runs=1)
    provider.record_node(
        run_id="old.s.turn0", node_name="n", session_id="s", status="ok", duration_ms=1.0
    )
    provider.record_node(
        run_id="new.s.turn0", node_name="n", session_id="s", status="ok", duration_ms=1.0
    )
    assert provider.get_run_summary("old.s.turn0") is None
    assert provider.get_run_summary("new.s.turn0") is not None


def test_unknown_run_returns_none() -> None:
    assert _provider().get_run_summary("ghost") is None
