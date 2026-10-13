"""NTS.1 batch 2 — I/O contracts on rag_agent, parallel_research, codeact, cua.

Same rules as batch 1 (``test_nts1_batch1.py``): narrow ``AgentState``
projections, consulted only with ``node_typesafety_enabled``, and
``propagate_errors=True`` so failures still reach the graph unchanged.

Batch-specific contracts:

- ``rag_agent`` types its own part of ``metadata`` (``metadata.rag_agent``:
  reflection score in [0, 1], iterations >= 0) and leaves other keys free.
- ``parallel_researcher_worker`` writes the ``operator.add`` channel
  ``parallel_results``: exactly one well-formed entry per worker, so a worker
  that returned the whole list (duplicating every finding) is caught. Its
  input is a ``Send`` payload carrying ``_task`` (not an ``AgentState`` key),
  so it declares no input model.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.codeact_agent import CodeActInput, CodeActOutput, codeact_node
from prismal.agents.cua_agent import CuaInput, CuaOutput, cua_node
from prismal.agents.extension.node_schema import validate_node_input, validate_node_output
from prismal.agents.parallel_research import (
    ParallelResearcherInput,
    ParallelResearcherOutput,
    ParallelWorkerOutput,
    ResearchAggregatorInput,
    ResearchAggregatorOutput,
    parallel_researcher_node,
    parallel_researcher_worker,
    research_aggregator_node,
)
from prismal.agents.rag_agent import RagAgentInput, RagAgentOutput, rag_agent_node
from prismal.agents.state import create_initial_state

BATCH = [
    (rag_agent_node, RagAgentInput, RagAgentOutput),
    (parallel_researcher_node, ParallelResearcherInput, ParallelResearcherOutput),
    (parallel_researcher_worker, None, ParallelWorkerOutput),
    (research_aggregator_node, ResearchAggregatorInput, ResearchAggregatorOutput),
    (codeact_node, CodeActInput, CodeActOutput),
    (cua_node, CuaInput, CuaOutput),
]


def _ids(x: Any) -> str:
    return getattr(x, "__name__", "")


@pytest.mark.parametrize(("node", "inp", "out"), BATCH, ids=_ids)
def test_models_are_wired_and_errors_keep_propagating(node: Any, inp: Any, out: Any) -> None:
    meta = node.__prismal_node__
    assert meta.input_model is inp
    assert meta.output_model is out
    assert meta.propagate_errors is True
    assert (meta.security, meta.audit, meta.retry, meta.timeout_s) == ("off", False, None, None)


@pytest.mark.parametrize(("node", "inp", "out"), BATCH, ids=_ids)
def test_a_fresh_state_satisfies_every_input_contract(node: Any, inp: Any, out: Any) -> None:
    state = create_initial_state(session_id="s-nts1b2")
    state["messages"] = [HumanMessage(content="hi")]
    assert validate_node_input(state, inp, node_name=node.__prismal_node__.name).ok


# ── rag_agent: typed metadata.rag_agent ─────────────────────────────────────


def _rag_update(**rag: Any) -> dict[str, Any]:
    return {
        "current_agent": "rag_agent",
        "messages": [AIMessage(content="grounded")],
        "metadata": {"other": {"kept": True}, "rag_agent": rag},
    }


def test_rag_output_accepts_its_metadata_and_other_keys() -> None:
    update = _rag_update(reflection_score=0.9, reflection_iterations=1)
    assert validate_node_output(update, RagAgentOutput, node_name="rag_agent").ok


@pytest.mark.parametrize(
    "rag",
    [
        {"reflection_score": 1.5, "reflection_iterations": 1},
        {"reflection_score": 0.5, "reflection_iterations": -1},
        {"reflection_iterations": 1},
    ],
)
def test_rag_output_rejects_a_bad_rag_agent_block(rag: dict[str, Any]) -> None:
    result = validate_node_output(_rag_update(**rag), RagAgentOutput, node_name="rag_agent")
    assert not result.ok
    assert any("rag_agent" in e for e in result.errors)


# ── parallel_research: the operator.add channel ─────────────────────────────


_ENTRY = {"agent": "parallel_researcher_worker", "task": "t", "result": "r"}


def test_worker_output_accepts_exactly_one_entry() -> None:
    update = {"parallel_results": [_ENTRY]}
    assert validate_node_output(update, ParallelWorkerOutput, node_name="w").ok
    with_env = {"parallel_results": [{**_ENTRY, "envelope": {"summary": "s"}}]}
    assert validate_node_output(with_env, ParallelWorkerOutput, node_name="w").ok


@pytest.mark.parametrize(
    "results",
    [[], [_ENTRY, _ENTRY], [{"agent": "x", "task": "t"}]],
    ids=["none", "duplicated", "no-result"],
)
def test_worker_output_rejects_anything_but_one_entry(results: list[Any]) -> None:
    update = {"parallel_results": results}
    assert not validate_node_output(update, ParallelWorkerOutput, node_name="w").ok


def test_aggregator_input_rejects_a_malformed_result() -> None:
    state = create_initial_state(session_id="s")
    state["parallel_results"] = [{"task": "t"}]
    assert not validate_node_input(state, ResearchAggregatorInput, node_name="agg").ok


# ── error semantics ─────────────────────────────────────────────────────────


async def test_a_failing_worker_llm_still_raises_out_of_the_node() -> None:
    state: Any = {"_task": "find x", "session_id": "s"}
    with (
        patch("prismal.agents.parallel_research.ProviderRegistry") as registry,
        patch(
            "prismal.agents.parallel_research.react_loop",
            AsyncMock(side_effect=RuntimeError("down")),
        ),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="down"):
            await parallel_researcher_worker(state)
