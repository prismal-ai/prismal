"""Tests for the chained order toolkit (TKE9-04, sequential long loop).

``get_linked_order`` returns a record plus the id of the next order in its
chain, so the model cannot batch lookups: every hop needs a new loop turn.
That is the shape observation masking targets.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from prismal.eval.runner import EvalRunner, _runtime_kwargs
from prismal.eval.toolkit import (
    CHAIN_STARTS,
    chain_from,
    eval_toolkit_chain_tools,
    order_total,
)
from prismal.eval.types import AssertionType, EvalCase, EvalSet


def _get(order_id: str) -> dict[str, Any]:
    [tool] = eval_toolkit_chain_tools()
    return json.loads(tool.invoke({"order_id": order_id}))


def test_the_chain_toolkit_exposes_only_get_linked_order() -> None:
    assert [t.name for t in eval_toolkit_chain_tools()] == ["get_linked_order"]


def test_each_record_links_to_the_next_order_of_its_chain() -> None:
    for start in CHAIN_STARTS:
        chain = chain_from(start)
        for here, nxt in zip(chain, [*chain[1:], None], strict=True):
            record = _get(here)
            assert record["order_id"] == here
            assert record["linked_order"] == nxt
            assert record["total"] == order_total(here)


def test_chains_are_disjoint_and_have_three_to_five_hops() -> None:
    chains = [chain_from(s) for s in CHAIN_STARTS]
    assert sorted(len(c) for c in chains) == [3, 4, 5]
    flat = [oid for c in chains for oid in c]
    assert len(flat) == len(set(flat))


def test_an_unknown_id_is_reported_without_raising() -> None:
    [tool] = eval_toolkit_chain_tools()
    assert "unknown order" in tool.invoke({"order_id": "ORD-999"})


def test_setup_marker_gives_every_agent_the_chain_toolkit() -> None:
    provider = _runtime_kwargs({"tool_provider": "eval_toolkit_chain"})["tool_provider"]
    for agent in ("researcher", "coder", "data_analyst"):
        assert {t.name for t in provider.get_tools(agent_name=agent)} == {"get_linked_order"}


def test_a_chain_case_runs_with_its_provider_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.agents import tool_registry

    monkeypatch.setattr(tool_registry, "_provider", None)
    seen: list[object] = []

    class _Graph:
        async def astream(self, *_a: Any, **_k: Any) -> Any:
            seen.append(tool_registry.get_tool_provider())
            yield {}

    class _Runtime:
        def __init__(self) -> None:
            self.tool_provider = _runtime_kwargs({"tool_provider": "eval_toolkit_chain"})[
                "tool_provider"
            ]

        async def aclose(self) -> None:
            return None

    runtime = _Runtime()

    async def graph_factory(**_k: Any) -> Any:
        return _Graph()

    runner = EvalRunner(graph_factory=graph_factory, runtime_factory=lambda **_k: runtime)
    case = EvalCase(id="c", input="x", assertions=[], setup={"tool_provider": "eval_toolkit_chain"})
    asyncio.run(runner.run_case(case))
    assert seen == [runtime.tool_provider]
    assert tool_registry.get_tool_provider() is None


def test_the_sequential_suite_checks_answers_against_the_chains() -> None:
    suite = Path(__file__).resolve().parents[3] / "tests/eval/sets/long_loop_sequential.yaml"
    a, b, c = (chain_from(s) for s in CHAIN_STARTS)
    best = max(b, key=order_total)
    expected = {
        "seq-001": {str(sum(order_total(o) for o in a))},
        "seq-002": {best, str(order_total(best))},
        "seq-003": {str(order_total(o)) for o in c},
    }
    cases = EvalSet.from_yaml(suite).cases
    assert {case.id for case in cases} == set(expected)
    for case in cases:
        assert case.setup["tool_provider"] == "eval_toolkit_chain"
        [contains] = [x for x in case.assertions if x.type is AssertionType.CONTAINS]
        assert set(contains.must_contain) == expected[case.id], case.id
