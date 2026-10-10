"""Supervisor keeps per-request values out of its system prompt (Phase TKE — TKE2-08).

With the cache-stable layout, recalled memory (which varies per turn) moves from
the routing SystemMessage to the tail instruction, so the system prefix is
byte-identical across turns. With the master off the legacy prompt is unchanged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from prismal.agents.state import create_initial_state
from prismal.agents.supervisor import build_system_prompt, supervisor_node
from prismal.core.config import get_settings

_MEMORY = "\n\nKnown user preferences:\n- prefers Spanish"


async def _routing_messages(**settings_update: object) -> list[object]:
    state = create_initial_state(session_id="tke-sup")
    state["messages"] = [HumanMessage(content="hola, ayúdame con algo")]
    settings = get_settings().model_copy(update=settings_update)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="END"))
    llm.bind_tools.return_value = llm
    with (
        patch("prismal.agents.supervisor.ProviderRegistry") as registry,
        patch("prismal.agents.supervisor.get_settings", return_value=settings),
        patch(
            "prismal.agents.supervisor._recall_memory_context",
            AsyncMock(return_value=_MEMORY),
        ),
    ):
        registry.return_value.get_llm_with_fallback.return_value = llm
        await supervisor_node(state)
    return list(llm.ainvoke.call_args_list[0].args[0])  # routing call


async def test_legacy_appends_memory_to_system_prompt() -> None:
    messages = await _routing_messages()
    assert isinstance(messages[0], SystemMessage)
    assert messages[0].content.endswith(_MEMORY)


async def test_cache_stable_system_prompt_is_static() -> None:
    messages = await _routing_messages(token_efficiency_enabled=True, prompt_layout="cache_stable")
    s = get_settings()
    assert messages[0].content == build_system_prompt(
        s.enable_subgraphs,
        s.multimodal_enabled,
        s.kokoro_enabled,
        s.skynet_enabled,
        s.blind_review_pipeline_enabled,
    )
    tail = messages[-1]
    assert isinstance(tail, HumanMessage)
    assert "<turn_context>" in tail.content
    assert "prefers Spanish" in tail.content
