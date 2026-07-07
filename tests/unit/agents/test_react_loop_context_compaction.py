"""Unit tests: react_loop's optional context_compactor hook (Phase LH — LH1-06).

Compacts the loop's local ``loop_messages`` accumulator (never AgentState) —
position-based, since these messages haven't round-tripped through the
``add_messages`` reducer yet and may lack a stable ``id``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.context_compaction import ContextCompactor
from prismal.agents.tool_registry import react_loop
from prismal.core.config import Settings


def _ai_with_tool_calls(*tool_names: str) -> AIMessage:
    calls = [
        {"name": name, "args": {"query": f"test {name}"}, "id": f"call_{i}"}
        for i, name in enumerate(tool_names)
    ]
    return AIMessage(content="", tool_calls=calls)


def _ai_final(content: str = "final answer") -> AIMessage:
    return AIMessage(content=content, tool_calls=[])


def _make_llm(*responses: AIMessage) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=list(responses))
    return llm


def _make_tool(name: str, result: str = "ok") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = AsyncMock(return_value=result)
    return tool


@pytest.mark.asyncio
async def test_no_compactor_is_unchanged() -> None:
    """context_compactor=None (default) reproduces pre-LH1 behavior exactly."""
    llm = _make_llm(_ai_final("hello"))
    messages = [HumanMessage(content="hi")]

    response = await react_loop(llm, [], messages, agent_name="test")

    assert response.content == "hello"
    sent_messages = llm.ainvoke.call_args[0][0]
    assert list(sent_messages) == messages


@pytest.mark.asyncio
async def test_compactor_trims_loop_messages_before_llm_call() -> None:
    """A long pre-existing history is compacted before the first LLM call."""
    llm = _make_llm(_ai_final("hello"))
    long_history = [HumanMessage(content=f"m{i}") for i in range(100)]
    settings = Settings(
        context_compaction_enabled=True,
        context_compaction_max_messages=20,
        context_compaction_keep_recent=5,
    )
    compactor = ContextCompactor(settings=settings)

    await react_loop(llm, [], long_history, agent_name="test", context_compactor=compactor)

    sent_messages = llm.ainvoke.call_args[0][0]
    assert len(sent_messages) == 5


@pytest.mark.asyncio
async def test_compactor_below_threshold_is_noop() -> None:
    llm = _make_llm(_ai_final("hello"))
    messages = [HumanMessage(content="hi")]
    settings = Settings(context_compaction_enabled=True, context_compaction_max_messages=60)
    compactor = ContextCompactor(settings=settings)

    await react_loop(llm, [], messages, agent_name="test", context_compactor=compactor)

    sent_messages = llm.ainvoke.call_args[0][0]
    assert list(sent_messages) == messages


@pytest.mark.asyncio
async def test_compactor_applies_across_tool_iterations() -> None:
    """Loop-local growth (tool results appended each iteration) can also trigger compaction."""
    tool = _make_tool("search", "some result")
    llm = _make_llm(
        _ai_with_tool_calls("search"),
        _ai_final("done"),
    )
    long_history = [HumanMessage(content=f"m{i}") for i in range(20)]
    settings = Settings(
        context_compaction_enabled=True,
        context_compaction_max_messages=20,
        context_compaction_keep_recent=5,
    )
    compactor = ContextCompactor(settings=settings)

    response = await react_loop(
        llm, [tool], long_history, agent_name="test", context_compactor=compactor
    )

    assert response.content == "done"
    second_call_messages = llm.ainvoke.call_args_list[1][0][0]
    # 20 history + 1 AI response + 1 tool result = 22 > 20 threshold -> compacted.
    assert len(second_call_messages) < 22
