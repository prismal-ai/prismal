"""react_loop notes tool failures for the routing cascade (SPEC-AMR-CAS-001 tool_errors)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from prismal.agents.tool_registry import react_loop
from prismal.routing import outcome


@tool
def broken(x: str) -> str:
    """Always fails."""
    raise RuntimeError("permanent failure")


class _LLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[{"name": "broken", "args": {"x": "a"}, "id": "c1"}],
            )
        return AIMessage(content="done")


async def test_failed_tool_is_noted_in_active_scope() -> None:
    stats = [0]
    token = outcome._TOOL_ERRORS.set(stats)
    try:
        await react_loop(_LLM(), [broken], [HumanMessage(content="go")], agent_name="coder")
    finally:
        outcome._TOOL_ERRORS.reset(token)
    assert stats[0] == 1


async def test_failed_tool_outside_scope_is_harmless() -> None:
    result = await react_loop(_LLM(), [broken], [HumanMessage(content="go")], agent_name="coder")
    assert isinstance(result, AIMessage)
