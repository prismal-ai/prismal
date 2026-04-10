"""Integration tests: supervisor routes scheduling requests to cron_manager (T-256).

These tests verify that:
- ``cron_manager`` is a registered member of the supervisor.
- The supervisor routes scheduling-related requests (English and Spanish) to
  ``cron_manager`` when the mock LLM returns that token.
- The supervisor routes cron-listing requests to ``cron_manager``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.state import AgentState
from lightagent.agents.supervisor import MEMBERS, supervisor_node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCH_TARGET = "lightagent.agents.supervisor.ProviderRegistry"


def _make_state(text: str) -> AgentState:
    """Return a minimal AgentState with a single HumanMessage.

    Args:
        text: The user message content.

    Returns:
        A populated :class:`AgentState` ready for supervisor invocation.
    """
    return AgentState(
        messages=[HumanMessage(content=text)],
        current_agent="supervisor",
        next_agent=None,
        task_plan=[],
        completed_tasks=[],
        pending_tasks=[],
        retrieved_docs=[],
        doc_grades=[],
        tool_results=[],
        tool_errors=[],
        parallel_results=[],
        dev_pipeline_modules=[],
        risk_score=0.0,
        permissions_granted=[],
        security_flags=[],
        session_id="sess-cron-test",
        created_at="2026-01-01T00:00:00",
        token_count=0,
        estimated_cost_usd=0.0,
        iteration_count=0,
        metadata={},
    )


def _mock_registry(response_text: str) -> MagicMock:
    """Build a patched ``ProviderRegistry`` whose LLM returns ``response_text``.

    Args:
        response_text: The string the mock LLM should return as routing decision.

    Returns:
        A MagicMock that can be used as the ``ProviderRegistry`` class mock.
    """
    mock_llm = MagicMock()
    mock_response = AIMessage(content=response_text)
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    mock_reg = MagicMock()
    mock_reg.return_value.get_llm_with_fallback.return_value = mock_llm
    return mock_reg


# ---------------------------------------------------------------------------
# Membership test (sync — no LLM call required)
# ---------------------------------------------------------------------------


def test_cron_manager_in_members() -> None:
    """``cron_manager`` is a registered supervisor member."""
    assert "cron_manager" in MEMBERS


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supervisor_routes_schedule_request_to_cron_manager() -> None:
    """Supervisor routes 'schedule a daily summary report' to cron_manager."""
    state = _make_state("schedule a daily summary report every morning at 8am")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_routes_cron_list_request() -> None:
    """Supervisor routes 'list my scheduled jobs' to cron_manager."""
    state = _make_state("lista mis tareas programadas")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_routes_pause_cron_to_cron_manager() -> None:
    """Supervisor routes 'pause scheduled job' to cron_manager."""
    state = _make_state("pause my daily report job")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_routes_resume_cron_to_cron_manager() -> None:
    """Supervisor routes 'resume scheduled task' to cron_manager."""
    state = _make_state("resume the hourly data sync task")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_routes_remove_cron_to_cron_manager() -> None:
    """Supervisor routes 'remove a cron job' to cron_manager."""
    state = _make_state("remove the weekly summary cron job")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_routes_periodic_reminder_to_cron_manager() -> None:
    """Supervisor routes 'set a periodic reminder' to cron_manager."""
    state = _make_state("set a reminder every day at noon to check emails")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_routes_spanish_schedule_request() -> None:
    """Supervisor routes Spanish scheduling requests to cron_manager."""
    state = _make_state("programa una tarea cada hora para sincronizar datos")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "cron_manager"


@pytest.mark.asyncio
async def test_supervisor_current_agent_is_always_supervisor() -> None:
    """The supervisor always sets ``current_agent`` to ``'supervisor'``."""
    state = _make_state("schedule a task")

    with patch(_PATCH_TARGET, _mock_registry("cron_manager")):
        result = await supervisor_node(state)

    assert result.get("current_agent") == "supervisor"


@pytest.mark.asyncio
async def test_supervisor_does_not_route_coding_to_cron_manager() -> None:
    """The supervisor routes coding requests to 'coder', not 'cron_manager'."""
    state = _make_state("write a Python script that computes fibonacci numbers")

    with patch(_PATCH_TARGET, _mock_registry("coder")):
        result = await supervisor_node(state)

    assert result.get("next_agent") == "coder"
    assert result.get("next_agent") != "cron_manager"
