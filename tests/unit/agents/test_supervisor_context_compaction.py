"""Integration tests: supervisor_node wiring for context compaction (Phase LH — LH1-05)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from prismal.agents.context_compaction import clear_context_compaction_run
from prismal.agents.state import create_initial_state
from prismal.agents.supervisor import supervisor_node
from prismal.core.config import Settings


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    state = create_initial_state(session_id="sess-compaction")
    clear_context_compaction_run(state)
    yield
    clear_context_compaction_run(state)


@pytest.mark.asyncio
async def test_supervisor_folds_compaction_update_into_loop_break_return() -> None:
    """The loop-break early-return path still carries the compaction RemoveMessage entries."""
    state = create_initial_state(session_id="sess-compaction")
    state["current_agent"] = "researcher"
    state["messages"] = [HumanMessage(content=f"m{i}", id=f"id{i}") for i in range(199)] + [
        AIMessage(content="done", id="id199")
    ]

    settings = Settings(
        context_compaction_enabled=True,
        context_compaction_max_messages=60,
        context_compaction_keep_recent=10,
    )

    with patch("prismal.agents.supervisor.get_settings", return_value=settings):
        result = await supervisor_node(state)

    assert result["next_agent"] is None
    assert any(isinstance(m, RemoveMessage) for m in result["messages"])


@pytest.mark.asyncio
async def test_supervisor_does_not_fold_compaction_when_disabled() -> None:
    state = create_initial_state(session_id="sess-compaction")
    state["current_agent"] = "researcher"
    state["messages"] = [HumanMessage(content=f"m{i}", id=f"id{i}") for i in range(199)] + [
        AIMessage(content="done", id="id199", type="ai")
    ]

    settings = Settings(context_compaction_enabled=False)

    with patch("prismal.agents.supervisor.get_settings", return_value=settings):
        result = await supervisor_node(state)

    assert result["messages"] == []
