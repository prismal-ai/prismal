"""RunSummary gateway fields (Phase GW — SPEC-GW-COST-001, GW5-07)."""

from __future__ import annotations

from prismal.budget.types import Usage
from prismal.core.config import Settings
from prismal.monitoring.observability import DefaultObservabilityProvider
from prismal.monitoring.observability_types import RunSummary


def test_gateway_fields_default_empty() -> None:
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
    assert (summary.gateway, summary.served_model, summary.equivalent_usd) == ("", "", 0.0)


def test_provider_reports_last_gateway_and_served_model() -> None:
    provider = DefaultObservabilityProvider(settings=Settings())
    for usage in (
        Usage(prompt_tokens=10, gateway="openrouter", served_model="a/b"),
        Usage(prompt_tokens=10, equivalent_usd=0.5, gateway="opencode-go", served_model="glm-5"),
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
    assert (summary.gateway, summary.served_model) == ("opencode-go", "glm-5")
    assert summary.equivalent_usd == 0.5
