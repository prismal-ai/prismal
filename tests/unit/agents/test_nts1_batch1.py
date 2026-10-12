"""NTS.1 batch 1 — I/O contracts on coder, researcher, data_analyst, planner, critic.

Each node declares narrow boundary projections of ``AgentState`` (DD-NTS-003).
They are only consulted with ``node_typesafety_enabled`` (``warn`` first). The
nodes keep their error semantics (``propagate_errors=True``): adopting a
contract must not turn a failure into a swallowed ``metadata`` error update.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.coder import CoderInput, CoderOutput, coder_node
from prismal.agents.critic import CriticInput, CriticOutput, critic_node
from prismal.agents.data_analyst import DataAnalystInput, DataAnalystOutput, data_analyst_node
from prismal.agents.extension.node_schema import validate_node_input, validate_node_output
from prismal.agents.planner import PlannerInput, PlannerOutput, planner_node
from prismal.agents.researcher import ResearcherInput, ResearcherOutput, researcher_node
from prismal.agents.state import create_initial_state

BATCH = [
    (coder_node, CoderInput, CoderOutput),
    (researcher_node, ResearcherInput, ResearcherOutput),
    (data_analyst_node, DataAnalystInput, DataAnalystOutput),
    (planner_node, PlannerInput, PlannerOutput),
    (critic_node, CriticInput, CriticOutput),
]


@pytest.mark.parametrize(("node", "inp", "out"), BATCH, ids=lambda x: getattr(x, "__name__", ""))
def test_models_are_wired_and_errors_keep_propagating(node: Any, inp: Any, out: Any) -> None:
    meta = node.__prismal_node__
    assert meta.input_model is inp
    assert meta.output_model is out
    assert meta.propagate_errors is True
    assert (meta.security, meta.audit, meta.retry, meta.timeout_s) == ("off", False, None, None)


@pytest.mark.parametrize(("node", "inp", "out"), BATCH, ids=lambda x: getattr(x, "__name__", ""))
def test_a_fresh_state_satisfies_every_input_contract(node: Any, inp: Any, out: Any) -> None:
    state = create_initial_state(session_id="s-nts1")
    state["messages"] = [HumanMessage(content="hi")]
    assert validate_node_input(state, inp, node_name=node.__prismal_node__.name).ok


_PLANNER_OK = {
    "current_agent": "planner",
    "messages": [AIMessage(content="plan")],
    "task_plan": ["research", "code"],
    "pending_tasks": ["research", "code"],
    "completed_tasks": [],
    "metadata": {"planner": {}},
}


def test_planner_output_contract_accepts_a_real_plan_and_rejects_a_bad_one() -> None:
    assert validate_node_output(_PLANNER_OK, PlannerOutput, node_name="planner").ok
    bad = {**_PLANNER_OK, "task_plan": [{"step": 1}]}
    result = validate_node_output(bad, PlannerOutput, node_name="planner")
    assert not result.ok
    assert any("task_plan" in e for e in result.errors)


def test_critic_output_contract_rejects_a_negative_iteration_count() -> None:
    ok = {"current_agent": "critic", "messages": [AIMessage(content="ok")], "iteration_count": 1}
    assert validate_node_output(ok, CriticOutput, node_name="critic").ok
    assert not validate_node_output(
        {**ok, "iteration_count": -1}, CriticOutput, node_name="critic"
    ).ok


async def test_a_failing_llm_still_raises_out_of_the_node() -> None:
    """Before the contract the exception reached the graph; it still must."""
    state = create_initial_state(session_id="s-nts1-err")
    state["messages"] = [HumanMessage(content="write code")]
    with (
        patch("prismal.agents.coder.ProviderRegistry") as registry,
        patch("prismal.agents.coder.react_loop", AsyncMock(side_effect=RuntimeError("down"))),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="down"):
            await coder_node(state)
