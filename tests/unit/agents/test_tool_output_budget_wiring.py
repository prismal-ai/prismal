"""Tool-output budget wiring (Phase TKE — TKE5-03; SPEC-TKE-OUT-001)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prismal.agents.artifacts import InMemoryArtifactStore
from prismal.agents.extension._middleware import (
    DEFAULT_MIDDLEWARE_STACK,
    hardening_middleware,
    tool_output_budget_middleware,
)
from prismal.agents.tool_output_budget import ToolOutputBudget
from prismal.agents.tool_registry import react_loop
from prismal.core.config import Settings

_BIG = "x" * 40_000


def _on(**kw: Any) -> Settings:
    return Settings(
        token_efficiency_enabled=True,
        tool_output_budget_enabled=True,
        tool_output_max_tokens_default=100,
        **kw,
    )


def _llm(*responses: AIMessage) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(responses))
    return llm


def _big_tool() -> MagicMock:
    tool = MagicMock()
    tool.name = "dump"
    tool.ainvoke = AsyncMock(return_value=_BIG)
    return tool


async def _tool_message_after_loop(**react_kwargs: Any) -> ToolMessage:
    llm = _llm(
        AIMessage(content="", tool_calls=[{"name": "dump", "args": {}, "id": "c1"}]),
        AIMessage(content="done"),
    )
    await react_loop(llm, [_big_tool()], [HumanMessage(content="go")], **react_kwargs)
    return next(m for m in llm.ainvoke.await_args_list[1].args[0] if isinstance(m, ToolMessage))


async def test_react_loop_legacy_cap_without_budget() -> None:
    with patch("prismal.agents.tool_registry._default_tool_output_budget", return_value=None):
        msg = await _tool_message_after_loop()
    assert msg.content.endswith("…[truncated]")
    assert len(msg.content) == 4_000 + len("\n…[truncated]")


async def test_react_loop_applies_budget_with_artifact() -> None:
    store = InMemoryArtifactStore(settings=_on())
    budget = ToolOutputBudget(settings=_on(), artifact_store=store)
    msg = await _tool_message_after_loop(tool_output_budget=budget, session_id="s1")
    assert "artifact=" in msg.content
    assert len(msg.content) <= 400
    artifact_id = msg.content.split("artifact=")[1].split(";")[0]
    assert store.get(artifact_id, run_id="s1", tenant="default").total_chars == len(_BIG)


async def test_react_loop_resolves_default_budget_from_settings() -> None:
    budget = ToolOutputBudget(settings=_on(), artifact_store=InMemoryArtifactStore(settings=_on()))
    with patch("prismal.agents.tool_registry._default_tool_output_budget", return_value=budget):
        msg = await _tool_message_after_loop()
    assert "artifact=" in msg.content


def test_default_budget_none_when_disabled() -> None:
    from prismal.agents.tool_registry import _default_tool_output_budget

    with patch("prismal.core.config.get_settings", return_value=Settings()):
        assert _default_tool_output_budget() is None


def test_budget_middleware_sits_outside_hardening() -> None:
    stack = DEFAULT_MIDDLEWARE_STACK
    assert stack.index(tool_output_budget_middleware) == stack.index(hardening_middleware) - 1


async def test_budget_middleware_caps_tool_messages() -> None:
    async def node(_state: Any) -> dict[str, Any]:
        return {"messages": [ToolMessage(content=_BIG, tool_call_id="c1", name="dump")]}

    with patch("prismal.agents.extension._middleware.get_settings", return_value=_on()):
        update = await tool_output_budget_middleware(node, {"session_id": "s"}, MagicMock())
    assert len(update["messages"][0].content) <= 400
    assert update["messages"][0].tool_call_id == "c1"


async def test_budget_middleware_passthrough_when_off() -> None:
    original = ToolMessage(content=_BIG, tool_call_id="c1")

    async def node(_state: Any) -> dict[str, Any]:
        return {"messages": [original]}

    with patch("prismal.agents.extension._middleware.get_settings", return_value=Settings()):
        update = await tool_output_budget_middleware(node, {}, MagicMock())
    assert update["messages"][0] is original
