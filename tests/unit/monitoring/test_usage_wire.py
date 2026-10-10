"""Usage wire object + run-summary accessor (SPEC-PRT-USG-001, PRT5-06, PRT6-07)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from prismal.budget.types import Usage
from prismal.monitoring import get_run_summary
from prismal.monitoring.observability import FakeObservabilityProvider
from prismal.monitoring.observability_resolve import (
    clear_observability_run,
    seed_observability_run,
)
from prismal.monitoring.observability_types import RunSummary

_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "protocols" / "usage_wire.json"

_KEYS = [
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
    "calls",
    "cost_usd",
    "cost_source",
    "estimated",
    "served_model",
    "gateway",
]


def _summary(**usage: Any) -> RunSummary:
    u = Usage(**usage)
    return RunSummary(
        run_id="coder.s.turn1",
        session_id="s",
        agent_name="coder",
        visited_nodes=[],
        spans=[],
        tool_calls=[],
        usage=u,
        latency_ms=0.0,
        gateway=u.gateway,
        served_model=u.served_model,
    )


def test_wire_has_exactly_the_12_keys_in_order() -> None:
    wire = _summary().to_usage_wire()
    assert list(wire) == _KEYS


def test_wire_types_and_semantics() -> None:
    wire = _summary(
        prompt_tokens=1000,
        completion_tokens=200,
        cache_read_tokens=400,
        cache_write_tokens=100,
        reasoning_tokens=50,
        calls=3,
        cost_usd=0.0123456789,
        cost_source="litellm",
        served_model="anthropic/claude-x",
        gateway="openrouter",
    ).to_usage_wire()
    assert wire["total_tokens"] == 1200
    assert wire["cost_usd"] == 0.012346
    assert wire["cost_source"] == "litellm"
    assert wire["estimated"] is False
    assert wire["served_model"] == "anthropic/claude-x"
    assert wire["gateway"] == "openrouter"
    for key in _KEYS[:7]:
        assert isinstance(wire[key], int) and wire[key] >= 0


def test_empty_values_are_null_and_none_source() -> None:
    wire = _summary().to_usage_wire()
    assert wire["served_model"] is None and wire["gateway"] is None
    assert wire["cost_source"] == "none"
    assert wire["estimated"] is False  # no calls ⇒ nothing estimated


@pytest.mark.parametrize(
    ("kwargs", "source", "estimated"),
    [
        ({"calls": 1, "cost_source": "table", "estimated": True}, "table", True),
        ({"calls": 1, "cost_source": "gateway"}, "gateway", False),
        ({"calls": 1, "cost_source": "subscription"}, "subscription", False),
        ({"calls": 2, "cost_source": "litellm", "estimated": True}, "mixed", True),
        ({"calls": 1, "cost_source": ""}, "none", True),
    ],
)
def test_cost_source_and_estimated(kwargs: dict[str, Any], source: str, estimated: bool) -> None:
    wire = _summary(**kwargs).to_usage_wire()
    assert wire["cost_source"] == source
    assert wire["estimated"] is estimated


def test_wire_is_json_serializable_and_matches_contract_fixture() -> None:
    wire = _summary(
        prompt_tokens=1200,
        completion_tokens=300,
        cache_read_tokens=800,
        cache_write_tokens=0,
        reasoning_tokens=40,
        calls=2,
        cost_usd=0.004321,
        cost_source="gateway",
        served_model="anthropic/claude-sonnet-4.5",
        gateway="openrouter",
    ).to_usage_wire()
    contract = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert list(contract["example"]) == _KEYS
    assert json.loads(json.dumps(wire)) == contract["example"]
    assert [k["name"] for k in contract["keys"]] == _KEYS


def _seed(provider: FakeObservabilityProvider, session: str, turn: int, agent: str) -> str:
    run_id = seed_observability_run(session, provider, agent_name=agent, turn=turn)
    provider.record_node(
        run_id=run_id,
        node_name=agent,
        session_id=session,
        status="ok",
        duration_ms=1.0,
        tool_calls=[],
        usage=Usage(prompt_tokens=10 * turn, completion_tokens=1, calls=1, cost_source="litellm"),
    )
    return run_id


def test_get_run_summary_last_turn_and_all() -> None:
    provider = FakeObservabilityProvider()
    try:
        _seed(provider, "sess-u", 1, "coder")
        _seed(provider, "sess-u", 2, "coder")
        last = get_run_summary("sess-u")
        assert last is not None and last.usage.prompt_tokens == 20
        total = get_run_summary("sess-u", turn="all")
        assert total is not None and total.usage.prompt_tokens == 30
        assert total.usage.calls == 2
    finally:
        clear_observability_run("sess-u")


def test_get_run_summary_unknown_session_is_none() -> None:
    assert get_run_summary("nope") is None


def test_get_run_summary_never_raises() -> None:
    class _Broken:
        def get_run_summary(self, run_id: str) -> RunSummary:
            raise RuntimeError("boom")

    seed_observability_run("sess-b", _Broken(), agent_name="a", turn=1)  # type: ignore[arg-type]
    try:
        assert get_run_summary("sess-b") is None
    finally:
        clear_observability_run("sess-b")
