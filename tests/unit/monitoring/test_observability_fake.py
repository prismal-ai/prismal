"""Unit tests for FakeObservabilityProvider (OBS2-03 — SPEC-OBS-PRT-001)."""

from __future__ import annotations

from prismal.agents.extension.ports import ObservabilityPort, conforms_to
from prismal.budget.types import Usage
from prismal.monitoring.observability import FakeObservabilityProvider
from prismal.monitoring.observability_types import ToolCallRecord


def test_conforms_to_port() -> None:
    assert conforms_to(FakeObservabilityProvider(), ObservabilityPort) is True


def test_unknown_run_returns_none() -> None:
    provider = FakeObservabilityProvider()
    assert provider.get_run_summary("nope") is None


def test_record_node_builds_summary() -> None:
    provider = FakeObservabilityProvider()
    provider.record_node(
        run_id="coder.sess-1.turn0",
        node_name="coder",
        session_id="sess-1",
        status="ok",
        duration_ms=12.0,
        tool_calls=[ToolCallRecord(tool_name="search", node="coder", ok=True)],
        usage=Usage(prompt_tokens=10, completion_tokens=5, calls=1),
    )
    summary = provider.get_run_summary("coder.sess-1.turn0")
    assert summary is not None
    assert summary.agent_name == "coder"
    assert summary.session_id == "sess-1"
    assert summary.visited_nodes == ["coder"]
    assert len(summary.spans) == 1
    assert summary.spans[0].status == "ok"
    assert len(summary.tool_calls) == 1
    assert summary.tool_calls[0].tool_name == "search"
    assert summary.usage.total_tokens == 15
    assert summary.latency_ms == 12.0


def test_multiple_nodes_accumulate() -> None:
    provider = FakeObservabilityProvider()
    for node in ("planner", "coder"):
        provider.record_node(
            run_id="planner.sess-9.turn0",
            node_name=node,
            session_id="sess-9",
            status="ok",
            duration_ms=5.0,
            usage=Usage(prompt_tokens=1, completion_tokens=1, calls=1),
        )
    summary = provider.get_run_summary("planner.sess-9.turn0")
    assert summary is not None
    assert summary.visited_nodes == ["planner", "coder"]
    assert summary.usage.calls == 2
    assert summary.latency_ms == 10.0


def test_record_score_appears_in_summary() -> None:
    provider = FakeObservabilityProvider()
    provider.record_node(
        run_id="coder.s.turn0",
        node_name="coder",
        session_id="s",
        status="ok",
        duration_ms=1.0,
    )
    provider.record_score(
        run_id="coder.s.turn0",
        name="llm_judge:groundedness",
        value=0.8,
        comment="solid",
        source="llm_judge",
    )
    summary = provider.get_run_summary("coder.s.turn0")
    assert summary is not None
    assert len(summary.scores) == 1
    assert summary.scores[0].name == "llm_judge:groundedness"
    assert summary.scores[0].value == 0.8
    assert summary.scores[0].source == "llm_judge"


def test_record_score_on_unknown_run_is_noop() -> None:
    provider = FakeObservabilityProvider()
    # Must not raise (fail-open contract).
    provider.record_score(run_id="ghost", name="x", value=1.0)
    assert provider.get_run_summary("ghost") is None


def test_no_io_hot_path_never_raises() -> None:
    provider = FakeObservabilityProvider()
    # Even with an odd status/empty tool calls the sync hot path must not raise.
    provider.record_node(run_id="r", node_name="n", session_id="s", status="error", duration_ms=0.0)
    summary = provider.get_run_summary("r")
    assert summary is not None
    assert summary.spans[0].status == "error"
