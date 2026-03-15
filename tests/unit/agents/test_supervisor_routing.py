"""
Unit tests for the supervisor agent node and router.

Tests cover:
- Routing to valid sub-agents when LLM returns a member name
- Routing to END when LLM returns "END"
- Graceful fallback to END on invalid / gibberish LLM responses
- current_agent field always set to "supervisor"
- supervisor_router return values for None and agent name inputs
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.state import create_initial_state
from lightagent.agents.supervisor import supervisor_node, supervisor_router

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_mock_llm(response_text: str) -> MagicMock:
    """
    Return a mock LLM whose ainvoke returns an AIMessage with response_text.

    The supervisor calls ``llm.ainvoke(messages)`` directly, so only
    ``ainvoke`` needs to be mocked as an AsyncMock.

    Args:
        response_text: The text content the mock LLM should return.

    Returns:
        A MagicMock that behaves like a LangChain BaseChatModel for the
        purposes of the supervisor node.
    """
    mock_response = AIMessage(content=response_text)
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)
    return mock_llm


# ---------------------------------------------------------------------------
# supervisor_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_routes_to_researcher() -> None:
    """When the LLM returns 'researcher', next_agent must equal 'researcher'."""
    state = create_initial_state(session_id="sess-test-researcher")
    state["messages"] = [HumanMessage(content="Find information about LangGraph")]

    with patch("lightagent.agents.supervisor.ProviderRegistry") as mock_registry:
        mock_registry.return_value.get_llm_with_fallback.return_value = _make_mock_llm(
            "researcher"
        )
        result = await supervisor_node(state)

    assert result["next_agent"] == "researcher"


@pytest.mark.asyncio
async def test_supervisor_routes_to_end_when_done() -> None:
    """When the LLM returns 'END', next_agent must be None and an AIMessage is added."""
    state = create_initial_state(session_id="sess-test-end")
    state["messages"] = [HumanMessage(content="What is 2+2?")]

    # First call returns routing decision "END"; second call (via bind_tools)
    # returns the answer.
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content="END"),
            AIMessage(content="2+2 equals 4."),
        ]
    )
    # bind_tools must return the same mock so its ainvoke AsyncMock is preserved.
    mock_llm.bind_tools.return_value = mock_llm

    with patch("lightagent.agents.supervisor.ProviderRegistry") as mock_registry:
        mock_registry.return_value.get_llm_with_fallback.return_value = mock_llm
        result = await supervisor_node(state)

    assert result["next_agent"] is None
    assert result["messages"], "Expected an AIMessage response when routing to END"
    assert result["messages"][0].content == "2+2 equals 4."


@pytest.mark.asyncio
async def test_supervisor_handles_invalid_routing() -> None:
    """When the LLM returns gibberish, next_agent must default to None (END)."""
    state = create_initial_state(session_id="sess-test-invalid")
    state["messages"] = [HumanMessage(content="Do something")]

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content="blah_blah_not_a_valid_agent_xyz_123"),
            AIMessage(content="I can help with that."),
        ]
    )
    # bind_tools must return the same mock so its ainvoke AsyncMock is preserved.
    mock_llm.bind_tools.return_value = mock_llm

    with patch("lightagent.agents.supervisor.ProviderRegistry") as mock_registry:
        mock_registry.return_value.get_llm_with_fallback.return_value = mock_llm
        result = await supervisor_node(state)

    # Graceful fallback — must not raise, must set next_agent to None, must answer
    assert result["next_agent"] is None
    assert result["messages"], "Expected an AIMessage response even on invalid routing"


@pytest.mark.asyncio
async def test_supervisor_updates_current_agent() -> None:
    """supervisor_node always returns current_agent='supervisor'."""
    state = create_initial_state(session_id="sess-test-current-agent")
    state["messages"] = [HumanMessage(content="Write some code")]

    with patch("lightagent.agents.supervisor.ProviderRegistry") as mock_registry:
        mock_registry.return_value.get_llm_with_fallback.return_value = _make_mock_llm(
            "coder"
        )
        result = await supervisor_node(state)

    assert result["current_agent"] == "supervisor"


# ---------------------------------------------------------------------------
# supervisor_router tests
# ---------------------------------------------------------------------------


def test_supervisor_router_returns_end_when_none() -> None:
    """supervisor_router must return '__end__' when next_agent is None."""
    state = create_initial_state(session_id="sess-test-router-none")
    state["next_agent"] = None

    route = supervisor_router(state)

    assert route == "__end__"


def test_supervisor_router_returns_member_name() -> None:
    """supervisor_router must return 'researcher' when next_agent='researcher'."""
    state = create_initial_state(session_id="sess-test-router-member")
    state["next_agent"] = "researcher"

    route = supervisor_router(state)

    assert route == "researcher"
