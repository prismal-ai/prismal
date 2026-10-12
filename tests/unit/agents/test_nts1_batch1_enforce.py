"""NTS.1 batch 1 under ``enforce``: the real node outputs satisfy their contracts.

With ``node_typesafety_mode="enforce"`` a non-conforming input or output raises
``NodeValidationError`` (propagated as-is by ``propagate_errors``), so running
each real node end to end proves its declared contract matches what it returns.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.state import create_initial_state
from prismal.core.config import Settings
from prismal.core.exceptions import NodeValidationError


@pytest.fixture
def enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(node_typesafety_enabled=True, node_typesafety_mode="enforce")
    monkeypatch.setattr("prismal.agents.extension._middleware.get_settings", lambda: settings)


def _state(text: str) -> Any:
    state = create_initial_state(session_id="s-nts1-enforce")
    state["messages"] = [HumanMessage(content=text)]
    return state


@pytest.mark.parametrize(
    ("module", "node_name"),
    [
        ("coder", "coder_node"),
        ("researcher", "researcher_node"),
        ("data_analyst", "data_analyst_node"),
        ("critic", "critic_node"),
    ],
)
@pytest.mark.usefixtures("enforce")
async def test_react_nodes_conform_to_their_contracts(module: str, node_name: str) -> None:
    import importlib

    mod = importlib.import_module(f"prismal.agents.{module}")
    with (
        patch.object(mod, "ProviderRegistry") as registry,
        patch.object(mod, "react_loop", AsyncMock(return_value=AIMessage(content="done"))),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        update = await getattr(mod, node_name)(_state("do the task"))

    assert update["current_agent"]
    assert update["messages"][0].content == "done"


@pytest.mark.usefixtures("enforce")
async def test_planner_conforms_to_its_contract() -> None:
    from prismal.agents.planner import planner_node

    plan = (
        "1. [agent: researcher] Gather requirements\n"
        "2. [agent: coder] Implement the pipeline\n"
        "3. [agent: critic] Review the result\n"
    )
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content=plan))
    llm.bind_tools.return_value = llm
    with patch("prismal.agents.planner.ProviderRegistry") as registry:
        registry.return_value.get_llm_with_fallback.return_value = llm
        update = await planner_node(_state("Build a data pipeline"))

    assert update["current_agent"] == "planner"
    assert all(isinstance(task, str) for task in update["task_plan"])


@pytest.mark.usefixtures("enforce")
async def test_enforce_rejects_a_bad_input_before_the_node_runs() -> None:
    from prismal.agents.critic import critic_node

    state = _state("review")
    state["iteration_count"] = -3  # corrupted reflection counter
    loop = AsyncMock(return_value=AIMessage(content="never"))
    with (
        patch("prismal.agents.critic.ProviderRegistry"),
        patch("prismal.agents.critic.react_loop", loop),
        pytest.raises(NodeValidationError),
    ):
        await critic_node(state)
    loop.assert_not_awaited()
