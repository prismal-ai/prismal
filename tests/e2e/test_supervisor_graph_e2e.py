"""End-to-end tests for the SUPERVISOR LangGraph state machine.

These run the *real* compiled graph from :func:`build_supervisor_graph` with an
in-memory checkpointer, and drive routing deterministically by replacing the
LLM with a fake chat model (no API keys, no network). They verify the wiring
(graph builds with the expected nodes) and the routing behaviour (a request the
intent router doesn't match is sent to the LLM, whose ``"END"`` answer routes
the graph to termination).

Marked ``e2e`` so they can be selected/excluded independently. They require the
project dependencies installed (run in CI / project venv).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from prismal.agents.graph import build_supervisor_graph
from prismal.agents.state import create_initial_state

pytestmark = pytest.mark.e2e


def _build_graph() -> object:
    """Compile the flat supervisor graph with an in-memory checkpointer.

    No subgraph graphs are passed, so the flat topology is used regardless of
    the ``enable_subgraphs`` setting — keeping the run deterministic and free
    of disk/database side effects.
    """
    return build_supervisor_graph(checkpointer=MemorySaver())


def test_supervisor_graph_compiles_with_expected_nodes() -> None:
    """The compiled graph builds and exposes the supervisor + specialist nodes."""
    graph = _build_graph()
    node_ids = set(graph.get_graph().nodes)

    assert "supervisor" in node_ids
    # A couple of well-known specialist nodes from the 26-node roster.
    assert "coder" in node_ids
    assert "researcher" in node_ids


async def test_supervisor_routes_to_end_with_fake_llm() -> None:
    """A non-intent request whose LLM answer is 'END' runs supervisor -> END.

    Patches the provider's ``get_llm_with_fallback`` (the method the supervisor
    actually uses) with a fake whose ``ainvoke`` always returns ``AIMessage
    ("END")``. Memory recall/extraction are stubbed so the test isolates graph
    routing rather than the memory subsystem.
    """

    async def fake_ainvoke(_messages: object) -> AIMessage:
        return AIMessage(content="END")

    graph = _build_graph()
    state = create_initial_state("e2e-route-end")
    # A general request the deterministic intent router does NOT match, so the
    # supervisor falls through to the (faked) LLM routing decision.
    state["messages"] = [HumanMessage(content="Please reply with a short greeting, nothing else.")]

    with (
        patch("prismal.providers.registry.ProviderRegistry.get_llm_with_fallback") as mock_llm,
        patch("prismal.agents.supervisor._recall_memory_context", new=AsyncMock(return_value="")),
        patch("prismal.agents.supervisor._spawn_memory_extraction", new=MagicMock()),
    ):
        mock_llm.return_value.ainvoke = fake_ainvoke
        result = await graph.ainvoke(state, config={"configurable": {"thread_id": "e2e-route-end"}})

    # The run completed and terminated at the supervisor (routed to END).
    assert result["session_id"] == "e2e-route-end"
    assert result["next_agent"] is None
    # The original human turn is still present in the accumulated state.
    assert any(isinstance(m, HumanMessage) for m in result["messages"])
