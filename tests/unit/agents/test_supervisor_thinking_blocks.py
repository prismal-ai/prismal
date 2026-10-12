"""Reasoning models (qwen3, Claude extended thinking) return content blocks.

A live run against Ollama ``qwen3:14b`` logged ``supervisor_invalid_routing``
with a raw response of ``[{'type': 'thinking', ...}, {'type': 'text', ...}]``:
the supervisor stringified the whole block list, so routing always fell back to
``END`` and the thinking text could leak into a direct answer. Only the ``text``
blocks (and text outside inline ``<think>…</think>``) are the model's answer.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.state import create_initial_state
from prismal.agents.supervisor import _response_text, supervisor_node

_THINKING = {"type": "thinking", "thinking": "The user wants research, so researcher."}


def test_text_blocks_are_kept_and_thinking_blocks_dropped() -> None:
    content = [_THINKING, {"type": "text", "text": "researcher"}]
    assert _response_text(content) == "researcher"


def test_reasoning_and_redacted_blocks_are_dropped() -> None:
    content = [
        {"type": "reasoning", "reasoning": "hmm"},
        {"type": "redacted_thinking", "data": "xyz"},
        {"type": "text", "text": "coder"},
    ]
    assert _response_text(content) == "coder"


def test_inline_think_tags_are_stripped() -> None:
    assert _response_text("<think>\nlet me see\n</think>\n\nresearcher") == "researcher"


def test_plain_text_is_unchanged() -> None:
    assert _response_text("  researcher\n") == "  researcher\n"


async def test_supervisor_routes_a_thinking_block_response() -> None:
    state = create_initial_state(session_id="sess-thinking")
    state["messages"] = [HumanMessage(content="Find information about LangGraph")]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=AIMessage(content=[_THINKING, {"type": "text", "text": "researcher"}])
    )

    with patch("prismal.agents.supervisor.ProviderRegistry") as registry:
        registry.return_value.get_llm_with_fallback.return_value = llm
        result = await supervisor_node(state)

    assert result["next_agent"] == "researcher"


async def test_direct_answer_never_leaks_thinking() -> None:
    state = create_initial_state(session_id="sess-thinking-end")
    state["messages"] = [HumanMessage(content="What is 2+2?")]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content=[_THINKING, {"type": "text", "text": "END"}]),
            AIMessage(content=[_THINKING, {"type": "text", "text": "2+2 equals 4."}]),
        ]
    )
    llm.bind_tools.return_value = llm

    with patch("prismal.agents.supervisor.ProviderRegistry") as registry:
        registry.return_value.get_llm_with_fallback.return_value = llm
        result = await supervisor_node(state)

    assert result["next_agent"] is None
    answer = result["messages"][-1].content
    assert answer == "2+2 equals 4."
    assert "thinking" not in answer
