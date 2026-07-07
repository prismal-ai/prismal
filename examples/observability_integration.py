"""Observability integration demo with injected fakes (Phase OBS).

Drives the ``ObservabilityPort`` without any collector, Langfuse project, or LLM:

1. Seed a per-run provider through the registry (``observability_resolve``).
2. Simulate a supervisor run recording node visits, a tool call, and LLM usage.
3. Query the ``RunSummary`` and attach an LLM-judge score.
4. Export the run as a LangSmith- and a Langfuse-shaped dataset record.

Run::

    python examples/observability_integration.py
"""

from __future__ import annotations

from prismal.budget.types import Usage
from prismal.monitoring.observability import (
    FakeObservabilityProvider,
    run_name_for,
    trace_tags_for,
)
from prismal.monitoring.observability_resolve import (
    clear_observability_run,
    get_observability_provider,
    seed_observability_run,
)
from prismal.monitoring.observability_types import DatasetFormat, ToolCallRecord


def main() -> None:
    """Run the observability demo end to end."""
    session_id = "sess-demo"

    # 1. Naming convention — the single source of truth both dashboards group by.
    print("run name:", run_name_for(agent_name="planner", session_id=session_id, turn=0))
    print("tags:", trace_tags_for(agent_name="planner", node="coder", org_id="acme"))

    # 2. Seed a per-run provider (live provider stays out of checkpointed state).
    provider = FakeObservabilityProvider()
    run_id = seed_observability_run(
        session_id, provider, agent_name="planner", turn=0
    )
    resolved = get_observability_provider(session_id)
    assert resolved is provider

    try:
        # 3. Simulate the supervisor visiting two nodes.
        resolved.record_node(
            run_id=run_id,
            node_name="planner",
            session_id=session_id,
            status="ok",
            duration_ms=8.0,
            usage=Usage(prompt_tokens=20, completion_tokens=10, calls=1),
        )
        resolved.record_node(
            run_id=run_id,
            node_name="coder",
            session_id=session_id,
            status="ok",
            duration_ms=12.0,
            tool_calls=[ToolCallRecord(tool_name="write_file", node="coder", ok=True)],
            usage=Usage(prompt_tokens=15, completion_tokens=25, calls=1),
        )

        # 4. Query the run summary.
        summary = resolved.get_run_summary(run_id)
        assert summary is not None
        print("visited:", summary.visited_nodes)
        print("tool calls:", [t.tool_name for t in summary.tool_calls])
        print("tokens:", summary.usage.total_tokens, "latency_ms:", summary.latency_ms)

        # 5. Attach an LLM-judge score (Phase V eval harness plugs in here).
        resolved.record_score(
            run_id=run_id,
            name="llm_judge:groundedness",
            value=0.82,
            source="llm_judge",
        )
        summary = resolved.get_run_summary(run_id)
        assert summary is not None
        print("scores:", [(s.name, s.value) for s in summary.scores])

        # 6. Export the run for both vendors' evaluation-dataset formats.
        print("langsmith:", resolved.export_dataset([run_id], fmt=DatasetFormat.LANGSMITH))
        print("langfuse:", resolved.export_dataset([run_id], fmt=DatasetFormat.LANGFUSE))
    finally:
        clear_observability_run(session_id)


if __name__ == "__main__":
    main()
