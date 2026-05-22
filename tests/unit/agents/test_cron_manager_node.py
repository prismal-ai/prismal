"""Unit tests for the CronManagerAgent node and supervisor routing.

Tests cover:
- cron_manager_node returns the correct current_agent value
- cron_manager_node calls react_loop with a SystemMessage as the first message
- cron_manager_node requests tools for the 'cron_manager' agent
- cron_manager_node passes session_id to react_loop
- MEMBERS in supervisor.py includes 'cron_manager'
- The compiled graph contains a 'cron_manager' node
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.state import create_initial_state

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mock_llm(response: str = "Scheduled successfully") -> MagicMock:
    """Return a mock LLM that satisfies bind_tools and ainvoke contracts.

    Args:
        response: Content of the AIMessage returned by ainvoke.

    Returns:
        A MagicMock behaving like a LangChain BaseChatModel.
    """
    mock = MagicMock()
    mock.bind_tools = MagicMock(return_value=mock)
    mock.ainvoke = AsyncMock(return_value=AIMessage(content=response))
    return mock


# ---------------------------------------------------------------------------
# cron_manager_node tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_manager_node_returns_correct_agent_name() -> None:
    """cron_manager_node must set current_agent to 'cron_manager'."""
    from prismal.agents.cron_manager import cron_manager_node

    state = create_initial_state(session_id="sess-cron-1")
    state["messages"] = [HumanMessage(content="Schedule a daily report at 9 AM")]

    with (
        patch("lightagent.agents.cron_manager.ProviderRegistry") as mock_reg,
        patch("lightagent.agents.cron_manager.get_tools_for_agent", return_value=[]),
        patch(
            "lightagent.agents.cron_manager.react_loop",
            new_callable=AsyncMock,
            return_value=AIMessage(content="Scheduled"),
        ),
    ):
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await cron_manager_node(state)

    assert result["current_agent"] == "cron_manager"


@pytest.mark.asyncio
async def test_cron_manager_node_returns_messages() -> None:
    """cron_manager_node must include 'messages' in its returned dict."""
    from prismal.agents.cron_manager import cron_manager_node

    state = create_initial_state(session_id="sess-cron-2")
    state["messages"] = [HumanMessage(content="List all scheduled jobs")]

    with (
        patch("lightagent.agents.cron_manager.ProviderRegistry") as mock_reg,
        patch("lightagent.agents.cron_manager.get_tools_for_agent", return_value=[]),
        patch(
            "lightagent.agents.cron_manager.react_loop",
            new_callable=AsyncMock,
            return_value=AIMessage(content="No jobs found"),
        ),
    ):
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        result = await cron_manager_node(state)

    assert "messages" in result
    assert len(result["messages"]) == 1


@pytest.mark.asyncio
async def test_cron_manager_node_calls_react_loop_with_system_prompt() -> None:
    """react_loop must receive a SystemMessage as the first message in the list."""
    from prismal.agents.cron_manager import cron_manager_node

    state = create_initial_state(session_id="sess-cron-3")
    state["messages"] = [HumanMessage(content="Pause job daily_brief")]

    captured_calls: list[list[object]] = []

    async def _fake_react_loop(
        llm: object,
        tools: object,
        messages: list[object],
        *,
        agent_name: str,
        max_iterations: int,
        session_id: str | None,
    ) -> AIMessage:
        """Capture the messages argument and return a stub AIMessage."""
        captured_calls.append(list(messages))
        return AIMessage(content="Paused")

    with (
        patch("lightagent.agents.cron_manager.ProviderRegistry") as mock_reg,
        patch("lightagent.agents.cron_manager.get_tools_for_agent", return_value=[]),
        patch("lightagent.agents.cron_manager.react_loop", side_effect=_fake_react_loop),
    ):
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        await cron_manager_node(state)

    assert len(captured_calls) == 1
    first_message = captured_calls[0][0]
    assert isinstance(first_message, SystemMessage)


@pytest.mark.asyncio
async def test_cron_manager_node_uses_cron_tools() -> None:
    """get_tools_for_agent must be called with 'cron_manager'."""
    from prismal.agents.cron_manager import cron_manager_node

    state = create_initial_state(session_id="sess-cron-4")
    state["messages"] = [HumanMessage(content="Remove job old_task")]

    with (
        patch("lightagent.agents.cron_manager.ProviderRegistry") as mock_reg,
        patch("lightagent.agents.cron_manager.get_tools_for_agent", return_value=[]) as mock_tools,
        patch(
            "lightagent.agents.cron_manager.react_loop",
            new_callable=AsyncMock,
            return_value=AIMessage(content="Removed"),
        ),
    ):
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        await cron_manager_node(state)

    mock_tools.assert_called_once_with("cron_manager")


@pytest.mark.asyncio
async def test_cron_manager_node_passes_session_id() -> None:
    """react_loop must receive the session_id from state."""
    from prismal.agents.cron_manager import cron_manager_node

    state = create_initial_state(session_id="my-session-42")
    state["messages"] = [HumanMessage(content="Schedule hourly check")]

    passed_session_ids: list[str | None] = []

    async def _fake_react_loop(
        llm: object,
        tools: object,
        messages: object,
        *,
        agent_name: str,
        max_iterations: int,
        session_id: str | None,
    ) -> AIMessage:
        """Capture session_id and return a stub AIMessage."""
        passed_session_ids.append(session_id)
        return AIMessage(content="Scheduled")

    with (
        patch("lightagent.agents.cron_manager.ProviderRegistry") as mock_reg,
        patch("lightagent.agents.cron_manager.get_tools_for_agent", return_value=[]),
        patch("lightagent.agents.cron_manager.react_loop", side_effect=_fake_react_loop),
    ):
        mock_reg.return_value.get_llm_with_fallback.return_value = _mock_llm()
        await cron_manager_node(state)

    assert len(passed_session_ids) == 1
    assert passed_session_ids[0] == "my-session-42"


# ---------------------------------------------------------------------------
# Supervisor integration test
# ---------------------------------------------------------------------------


def test_supervisor_routes_to_cron_manager() -> None:
    """'cron_manager' must be present in the supervisor MEMBERS list."""
    from prismal.agents.supervisor import MEMBERS

    assert "cron_manager" in MEMBERS


# ---------------------------------------------------------------------------
# Graph integration test
# ---------------------------------------------------------------------------


def test_graph_has_cron_manager_node(tmp_path: Path) -> None:
    """The compiled graph must include a 'cron_manager' node in its mermaid output."""
    from prismal.agents.graph import build_supervisor_graph

    db_path = tmp_path / "cron_test.db"
    graph = build_supervisor_graph(checkpoint_path=db_path)
    mermaid = graph.get_graph().draw_mermaid()
    assert "cron_manager" in mermaid
