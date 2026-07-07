"""End-to-end observability with fakes (Phase OBS — OBS6-06).

A simulated run seeded through the per-run registry accumulates node visits and
tool calls; ``get_run_summary`` reflects them end-to-end. Also asserts the
``build_test_runtime`` opt-in fake injection surfaces the same provider.
"""

from __future__ import annotations

from prismal.budget.types import Usage
from prismal.composition import build_test_runtime
from prismal.monitoring.observability import FakeObservabilityProvider
from prismal.monitoring.observability_resolve import (
    clear_observability_run,
    get_observability_provider,
    seed_observability_run,
)
from prismal.monitoring.observability_types import DatasetFormat, ToolCallRecord


def test_simulated_run_reflected_in_summary() -> None:
    provider = FakeObservabilityProvider()
    run_id = seed_observability_run("sess-e2e", provider, agent_name="planner", turn=0)
    try:
        resolved = get_observability_provider("sess-e2e")
        assert resolved is provider

        # Simulate the supervisor visiting two nodes with a tool call + LLM usage.
        resolved.record_node(
            run_id=run_id,
            node_name="planner",
            session_id="sess-e2e",
            status="ok",
            duration_ms=8.0,
            usage=Usage(prompt_tokens=20, completion_tokens=10, calls=1),
        )
        resolved.record_node(
            run_id=run_id,
            node_name="coder",
            session_id="sess-e2e",
            status="ok",
            duration_ms=12.0,
            tool_calls=[ToolCallRecord(tool_name="write_file", node="coder", ok=True)],
            usage=Usage(prompt_tokens=15, completion_tokens=25, calls=1),
        )

        summary = resolved.get_run_summary(run_id)
        assert summary is not None
        assert summary.agent_name == "planner"
        assert summary.visited_nodes == ["planner", "coder"]
        assert [t.tool_name for t in summary.tool_calls] == ["write_file"]
        assert summary.usage.calls == 2
        assert summary.usage.total_tokens == 70
        assert summary.latency_ms == 20.0

        records = resolved.export_dataset([run_id], fmt=DatasetFormat.LANGSMITH)
        assert records[0]["metadata"]["run_id"] == run_id
    finally:
        clear_observability_run("sess-e2e")


def test_build_test_runtime_surfaces_injected_provider() -> None:
    provider = FakeObservabilityProvider()
    ctx = build_test_runtime(observability=provider)
    assert ctx.observability is provider
    ctx.observability.record_node(
        run_id="a.s.turn0", node_name="a", session_id="s", status="ok", duration_ms=1.0
    )
    summary = ctx.observability.get_run_summary("a.s.turn0")
    assert summary is not None
    assert summary.visited_nodes == ["a"]
