"""NTS.1 batch 2 under ``enforce``: the real node outputs satisfy their contracts.

With ``node_typesafety_mode="enforce"`` a non-conforming input or output
raises ``NodeValidationError`` (propagated as-is by ``propagate_errors``), so
running each real node proves its declared contract matches what it returns.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.state import create_initial_state
from prismal.core.config import Settings


@pytest.fixture(autouse=True)
def enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(node_typesafety_enabled=True, node_typesafety_mode="enforce")
    monkeypatch.setattr("prismal.agents.extension._middleware.get_settings", lambda: settings)


def _state(text: str) -> Any:
    state = create_initial_state(session_id="s-nts1b2-enforce")
    state["messages"] = [HumanMessage(content=text)]
    return state


async def test_rag_agent_conforms_to_its_contract() -> None:
    from prismal.agents import rag_agent

    with (
        patch.object(rag_agent, "ProviderRegistry") as registry,
        patch.object(rag_agent, "react_loop", AsyncMock(return_value=AIMessage(content="d"))),
        patch.object(rag_agent, "reflection_loop", AsyncMock(return_value=("grounded", 0.9))),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        update = await rag_agent.rag_agent_node(_state("what is x?"))

    assert update["current_agent"] == "rag_agent"
    assert 0.0 <= update["metadata"]["rag_agent"]["reflection_score"] <= 1.0


async def test_parallel_research_nodes_conform_to_their_contracts() -> None:
    from prismal.agents import parallel_research as pr

    anchor = await pr.parallel_researcher_node(_state("research"))
    assert anchor == {"current_agent": "parallel_researcher"}

    with (
        patch.object(pr, "ProviderRegistry") as registry,
        patch.object(pr, "react_loop", AsyncMock(return_value=AIMessage(content="found"))),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        worker = await pr.parallel_researcher_worker({"_task": "find x", "session_id": "s"})
    assert len(worker["parallel_results"]) == 1

    empty = await pr.parallel_researcher_worker({"session_id": "s"})
    assert len(empty["parallel_results"]) == 1

    state = _state("research")
    state["parallel_results"] = worker["parallel_results"]
    aggregated = await pr.research_aggregator_node(state)
    assert "find x" in aggregated["messages"][0].content


@pytest.mark.parametrize(
    ("module", "node_name"), [("codeact_agent", "codeact_node"), ("cua_agent", "cua_node")]
)
async def test_disabled_codeact_and_cua_conform_to_their_contracts(
    module: str, node_name: str
) -> None:
    import importlib

    mod = importlib.import_module(f"prismal.agents.{module}")
    update = await getattr(mod, node_name)(_state("do it"))
    assert update["current_agent"] in {"codeact", "cua"}
    assert isinstance(update["messages"][0], AIMessage)
