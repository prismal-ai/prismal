"""Unit tests for all 7 sub-agent nodes.

Tests cover:
- Each node returns the correct ``current_agent`` field.
- Each node includes ``messages`` in the returned dict.
- planner additionally returns ``task_plan`` as a list.
- critic increments ``iteration_count`` by 1.
- rag_agent returns ``retrieved_docs`` and ``doc_grades``.
- critic_router always returns ``"supervisor"``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.state import create_initial_state

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mock_llm(response: str = "Test response") -> MagicMock:
    """Return a mock LLM that satisfies bind_tools and ainvoke contracts.

    The mock's ``bind_tools`` method returns the mock itself, and ``ainvoke``
    is an :class:`AsyncMock` that returns an :class:`AIMessage`.

    Args:
        response: Content of the :class:`AIMessage` returned by ``ainvoke``.

    Returns:
        A :class:`MagicMock` behaving like a LangChain ``BaseChatModel``.
    """
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=response))
    return mock


# ---------------------------------------------------------------------------
# researcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_researcher_node_returns_current_agent() -> None:
    """researcher_node must set current_agent to 'researcher'."""
    from prismal.agents.researcher import researcher_node

    state = create_initial_state(session_id="sess-researcher")
    state["messages"] = [HumanMessage(content="Find info about LangGraph")]

    with patch("prismal.agents.researcher.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await researcher_node(state)

    assert result["current_agent"] == "researcher"
    assert "messages" in result


# ---------------------------------------------------------------------------
# coder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coder_node_returns_current_agent() -> None:
    """coder_node must set current_agent to 'coder'."""
    from prismal.agents.coder import coder_node

    state = create_initial_state(session_id="sess-coder")
    state["messages"] = [HumanMessage(content="Write a Python hello world script")]

    with patch("prismal.agents.coder.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await coder_node(state)

    assert result["current_agent"] == "coder"
    assert "messages" in result


# ---------------------------------------------------------------------------
# rag_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_agent_node_returns_current_agent() -> None:
    """rag_agent_node must set current_agent to 'rag_agent'."""
    from prismal.agents.rag_agent import rag_agent_node

    state = create_initial_state(session_id="sess-rag")
    state["messages"] = [HumanMessage(content="What does our onboarding doc say?")]

    with patch("prismal.agents.rag_agent.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await rag_agent_node(state)

    assert result["current_agent"] == "rag_agent"
    assert "messages" in result
    # Phase 34: rag_agent no longer echoes retrieved_docs/doc_grades because
    # ``retrieved_docs`` now uses an ``operator.add`` reducer (returning the
    # existing list would duplicate it).  LangGraph preserves the fields
    # automatically when they are absent from the partial update.
    assert "retrieved_docs" not in result
    assert "doc_grades" not in result


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_node_returns_current_agent() -> None:
    """planner_node must set current_agent to 'planner'."""
    from prismal.agents.planner import planner_node

    state = create_initial_state(session_id="sess-planner")
    state["messages"] = [HumanMessage(content="Build a data pipeline")]

    planner_response = (
        "1. [agent: researcher] Gather requirements\n"
        "2. [agent: coder] Implement the pipeline\n"
        "3. [agent: critic] Review the result\n"
    )

    with patch("prismal.agents.planner.ProviderRegistry") as mock_reg:
        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content=planner_response))
        mock_llm.bind_tools.return_value = mock_llm  # bind_tools must return the same mock
        mock_reg.return_value.get_llm_with_fallback.return_value = mock_llm
        result = await planner_node(state)

    assert result["current_agent"] == "planner"
    assert "messages" in result
    assert "task_plan" in result
    assert isinstance(result["task_plan"], list)


# ---------------------------------------------------------------------------
# critic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critic_node_returns_current_agent() -> None:
    """critic_node must set current_agent to 'critic'."""
    from prismal.agents.critic import critic_node

    state = create_initial_state(session_id="sess-critic")
    state["messages"] = [HumanMessage(content="Review this answer")]

    with patch("prismal.agents.critic.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await critic_node(state)

    assert result["current_agent"] == "critic"
    assert "messages" in result


@pytest.mark.asyncio
async def test_critic_node_increments_iteration_count() -> None:
    """critic_node must increment iteration_count by 1 from the initial value of 0."""
    from prismal.agents.critic import critic_node

    state = create_initial_state(session_id="sess-critic-iter")
    state["messages"] = [HumanMessage(content="Review this")]
    # iteration_count starts at 0 from create_initial_state

    with patch("prismal.agents.critic.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await critic_node(state)

    assert result["iteration_count"] == 1


def test_critic_router_always_returns_supervisor() -> None:
    """critic_router must always return 'supervisor' regardless of state."""
    from prismal.agents.critic import critic_router

    state = create_initial_state(session_id="sess-critic-router")
    assert critic_router(state) == "supervisor"


# ---------------------------------------------------------------------------
# data_analyst
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_analyst_node_returns_current_agent() -> None:
    """data_analyst_node must set current_agent to 'data_analyst'."""
    from prismal.agents.data_analyst import data_analyst_node

    state = create_initial_state(session_id="sess-data-analyst")
    state["messages"] = [HumanMessage(content="Show me total sales by region")]

    with patch("prismal.agents.data_analyst.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await data_analyst_node(state)

    assert result["current_agent"] == "data_analyst"
    assert "messages" in result


# ---------------------------------------------------------------------------
# file_manager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_manager_node_returns_current_agent() -> None:
    """file_manager_node must set current_agent to 'file_manager'."""
    from prismal.agents.file_manager import file_manager_node

    state = create_initial_state(session_id="sess-file-manager")
    state["messages"] = [HumanMessage(content="Save this text to output.txt")]

    with patch("prismal.agents.file_manager.ProviderRegistry") as mock_reg:
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await file_manager_node(state)

    assert result["current_agent"] == "file_manager"
    assert "messages" in result
