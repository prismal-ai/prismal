"""researcher_node adopts recalled memory as a tail message (MEM follow-up).

The recalled-memory message is appended after the conversation, so the system
prompt and history — the cache-stable prefix — are byte-identical with and
without memory. With memory off the react_loop input is unchanged.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from prismal.agents.state import create_initial_state


async def _run(memory: HumanMessage | None) -> list[Any]:
    from prismal.agents.researcher import researcher_node

    state = create_initial_state(session_id="s-mem")
    state["messages"] = [HumanMessage(content="Which database does billing use?")]
    loop = AsyncMock(return_value=AIMessage(content="PostgreSQL"))
    with (
        patch("prismal.agents.researcher.ProviderRegistry") as registry,
        patch("prismal.agents.researcher.react_loop", loop),
        patch("prismal.agents.researcher.memory_turn_message", AsyncMock(return_value=memory)),
    ):
        registry.return_value.get_llm_with_fallback.return_value = MagicMock()
        result = await researcher_node(state)
    assert result["messages"][0].content == "PostgreSQL"
    return list(loop.await_args.args[2])


async def test_memory_message_is_appended_after_the_conversation() -> None:
    memory = HumanMessage(
        content="<turn_context>memory:<memory>billing: PostgreSQL</memory></turn_context>"
    )

    with_memory = await _run(memory)
    without = await _run(None)

    assert with_memory[-1] is memory
    assert with_memory[:-1] == without  # prefix (system + history) unchanged


async def test_without_memory_the_loop_input_ends_with_the_user_turn() -> None:
    messages = await _run(None)

    assert messages[0].type == "system"
    assert messages[-1].content == "Which database does billing use?"
