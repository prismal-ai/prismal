"""Coder sub-agent node.

Specialist agent responsible for writing, executing, and debugging code.
Produces clean, well-documented code with type hints and docstrings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.coder")

_SYSTEM_PROMPT = """You are a software engineering specialist.

Your responsibilities:
- Write clean, idiomatic code that follows best practices for the target language.
- Always include docstrings for all public functions, methods, and classes.
- Use type hints throughout (Python 3.13+ syntax preferred).
- Execute code snippets to verify correctness before returning results.
- If execution produces errors, diagnose the root cause and fix them iteratively.
- Read existing files for context before modifying or extending them.
- Write output to files when the user asks for persistent results.
- Keep code DRY (Don't Repeat Yourself) and favour readability over cleverness."""


async def coder_node(state: AgentState) -> dict[str, object]:
    """Execute the coder sub-agent node.

    Invokes the LLM with coding-specific tools (code executor, file read/write)
    and returns verified, production-quality code.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'coder'``
        and new ``messages`` containing the generated or reviewed code.
    """
    logger.debug("coder_node_called", session_id=state.get("session_id"))

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    llm_with_tools = llm.bind_tools(get_tools_for_agent('coder'))

    messages = [SystemMessage(content=_SYSTEM_PROMPT), *state["messages"]]
    response: AIMessage = await llm_with_tools.ainvoke(messages)

    logger.info("coder_complete", session_id=state.get("session_id"))
    return {"current_agent": "coder", "messages": [response]}


__all__ = ["coder_node"]
