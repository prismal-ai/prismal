"""RunSummary cache fields (Phase TKE — SPEC-TKE-ACC-005)."""

from __future__ import annotations

from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.monitoring.observability import DefaultObservabilityProvider
from prismal.monitoring.observability_types import RunSummary


def test_run_summary_cache_fields_default_zero() -> None:
    summary = RunSummary(
        run_id="a.s.turn0",
        session_id="s",
        agent_name="a",
        visited_nodes=[],
        spans=[],
        tool_calls=[],
        usage=Usage(),
        latency_ms=0.0,
    )
    assert (summary.cache_read_tokens, summary.cache_write_tokens) == (0, 0)
    assert summary.reasoning_tokens == 0
    assert summary.cache_hit_ratio == 0.0


def test_provider_aggregates_cache_fields_across_nodes() -> None:
    provider = DefaultObservabilityProvider(settings=Settings())
    for usage in (
        Usage(prompt_tokens=1000, cache_read_tokens=600, reasoning_tokens=5),
        Usage(prompt_tokens=1000, cache_read_tokens=1000, cache_write_tokens=0),
    ):
        provider.record_node(
            run_id="a.s.turn0",
            node_name="a",
            session_id="s",
            status="ok",
            duration_ms=1.0,
            usage=usage,
        )
    summary = provider.get_run_summary("a.s.turn0")
    assert summary is not None
    assert summary.cache_read_tokens == 1600
    assert summary.reasoning_tokens == 5
    assert summary.cache_hit_ratio == 0.8
