"""Deterministic eval toolkit for long-loop token measurement (TKE9-04).

The smoke suite runs without tools, so a live run is one or two LLM calls and
the TKE context controls (observation masking, tool-output budgets, handoff
envelopes) never trigger. ``setup: {tool_provider: eval_toolkit}`` gives every
agent a small, I/O-free order ledger whose records are verbose on purpose.
"""

from __future__ import annotations

import json

import pytest

from prismal.eval.runner import _runtime_kwargs
from prismal.eval.toolkit import ORDER_IDS, eval_toolkit_tools, order_total


def _tools() -> dict[str, object]:
    return {t.name: t for t in eval_toolkit_tools()}


def test_the_toolkit_exposes_list_and_get() -> None:
    assert set(_tools()) == {"list_orders", "get_order"}


def test_list_orders_returns_every_id() -> None:
    listed = json.loads(_tools()["list_orders"].invoke({}))  # type: ignore[attr-defined]
    assert listed == list(ORDER_IDS)


def test_get_order_is_deterministic_verbose_and_carries_its_total() -> None:
    get = _tools()["get_order"]
    first = get.invoke({"order_id": ORDER_IDS[0]})  # type: ignore[attr-defined]
    assert first == get.invoke({"order_id": ORDER_IDS[0]})  # type: ignore[attr-defined]
    record = json.loads(first)
    assert record["total"] == order_total(ORDER_IDS[0])
    assert len(first) > 2000  # verbose on purpose: observations dominate the context


def test_get_order_reports_an_unknown_id_without_raising() -> None:
    out = _tools()["get_order"].invoke({"order_id": "ORD-999"})  # type: ignore[attr-defined]
    assert "unknown order" in out


def test_setup_eval_toolkit_gives_every_agent_the_toolkit() -> None:
    provider = _runtime_kwargs({"tool_provider": "eval_toolkit"})["tool_provider"]
    for agent in ("data_analyst", "researcher", "coder"):
        names = {t.name for t in provider.get_tools(agent_name=agent)}
        assert names == {"list_orders", "get_order"}


def test_setup_fake_keeps_the_default_runtime() -> None:
    assert "tool_provider" not in _runtime_kwargs({"tool_provider": "fake"})


def test_an_unknown_tool_provider_marker_is_rejected() -> None:
    with pytest.raises(ValueError, match="tool_provider"):
        _runtime_kwargs({"tool_provider": "evil_toolkit"})


# ── the runner installs the case's toolkit (agents resolve tools globally) ──


class _ProbeGraph:
    """Records the global tool provider the agents would see mid-run."""

    def __init__(self) -> None:
        self.seen: object = "unset"

    async def astream(self, *_a: object, **_k: object):  # type: ignore[no-untyped-def]
        from prismal.agents.tool_registry import get_tool_provider

        self.seen = get_tool_provider()
        yield {"supervisor": {"messages": []}}


class _Runtime:
    def __init__(self) -> None:
        self.tool_provider = object()

    async def aclose(self) -> None:
        return None


def _run(setup: dict[str, object]) -> tuple[_ProbeGraph, _Runtime]:
    import asyncio

    from prismal.eval.runner import EvalRunner
    from prismal.eval.types import EvalCase

    graph, runtime = _ProbeGraph(), _Runtime()

    async def graph_factory(**_k: object) -> _ProbeGraph:
        return graph

    runner = EvalRunner(graph_factory=graph_factory, runtime_factory=lambda **_k: runtime)
    asyncio.run(runner.run_case(EvalCase(id="c", input="x", assertions=[], setup=setup)))
    return graph, runtime


def test_an_eval_toolkit_case_runs_with_its_provider_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from prismal.agents import tool_registry

    monkeypatch.setattr(tool_registry, "_provider", None)
    graph, runtime = _run({"tool_provider": "eval_toolkit"})
    assert graph.seen is runtime.tool_provider
    assert tool_registry.get_tool_provider() is None  # restored after the case


def test_a_default_case_leaves_the_global_provider_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.agents import tool_registry

    monkeypatch.setattr(tool_registry, "_provider", None)
    graph, _ = _run({"tool_provider": "fake"})
    assert graph.seen is None


def _expected_long_loop_values() -> dict[str, set[str]]:
    """What a correct answer to each long-loop case must contain."""
    totals = {oid: order_total(oid) for oid in ORDER_IDS}
    first_five = list(ORDER_IDS[:5])
    best = max(first_five, key=totals.__getitem__)
    return {
        "loop-001": {str(sum(totals[f"ORD-00{n}"] for n in range(1, 5)))},
        "loop-002": {best, str(totals[best])},
        "loop-003": {str(totals[f"ORD-00{n}"]) for n in (7, 8, 9)},
    }


def test_long_loop_cases_check_the_answer_against_the_toolkit() -> None:
    """Each long-loop case asserts the toolkit's true values, so a fabricated
    answer fails the solve rate instead of passing on ``must_call`` alone."""
    from pathlib import Path

    from prismal.eval.types import AssertionType, EvalSet

    suite = Path(__file__).resolve().parents[3] / "tests/eval/sets/long_loop.yaml"
    expected = _expected_long_loop_values()
    for case in EvalSet.from_yaml(suite).cases:
        contains = [a for a in case.assertions if a.type is AssertionType.CONTAINS]
        assert len(contains) == 1, case.id
        assert set(contains[0].must_contain) == expected[case.id], case.id
