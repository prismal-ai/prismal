"""Researcher sub-agent node.

Specialist agent responsible for searching the web and querying the RAG
knowledge base to gather information and synthesise findings with citations.

The node implements a **ReAct loop**: the LLM is invoked with tools bound;
if it requests tool calls they are executed synchronously and the results fed
back as ``ToolMessage`` objects; the loop continues until the LLM produces a
final answer (no pending tool calls) or the iteration cap is reached.

Anthropic requires the last message in every request to be a ``HumanMessage``.
:func:`_trim_to_last_human` enforces this invariant by stripping any trailing
non-human messages from the conversation history before each LLM call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import BaseMessage, SystemMessage

from lightagent.agents.tool_registry import get_tools_for_agent, react_loop
from lightagent.core.logging import get_logger
from lightagent.providers.registry import ProviderRegistry

if TYPE_CHECKING:
    from lightagent.agents.state import AgentState

logger = get_logger("lightagent.agents.researcher")

# Maximum tool-call iterations per researcher invocation.  Guards against
# runaway loops when tool results never satisfy the LLM.
_MAX_TOOL_ITERATIONS: int = 5

_SYSTEM_PROMPT = """You are a research specialist with access to web search and a \
knowledge base.

Your responsibilities:
- Search the web for up-to-date information relevant to the user's query.
- Query the internal knowledge base (RAG) for domain-specific documents.
- Read referenced files when needed for additional context.
- Always cite your sources clearly, including URLs and document names.
- Synthesise findings into a concise, well-structured response.
- If conflicting information is found, present all perspectives and note discrepancies.
- Flag when information may be outdated or uncertain.

Tool and skill failure handling (IMPORTANT):
- If a tool returns a stub result (e.g. "[stub] ..."), an error, or no useful
  information, do NOT pretend the search succeeded. Acknowledge the limitation
  clearly and politely.
- If a tool is not available or not configured, inform the user and suggest
  what configuration may be needed (e.g. missing API key in .env).
- Never fabricate search results or cite sources you did not actually retrieve.
- If no information was found, say so honestly and offer alternative approaches
  or ask the user for a more specific query."""

# Tool map is built dynamically at call time to include live MCP + skill tools.


def _trim_to_last_human(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Return *messages* trimmed so the last element is a HumanMessage.

    Anthropic's API rejects requests where the final message role is
    ``assistant`` ("This model does not support assistant message prefill").
    This guard removes any trailing non-human messages before each LLM call
    so the invariant is always satisfied.

    Args:
        messages: Full conversation history slice.

    Returns:
        Messages up to and including the last HumanMessage, or the original
        list unchanged when no HumanMessage is present.
    """
    last_human = -1
    for i, msg in enumerate(messages):
        if getattr(msg, "type", "") == "human":
            last_human = i
    if last_human == -1:
        return messages
    return messages[: last_human + 1]


async def researcher_node(state: AgentState) -> dict[str, object]:
    """Execute the researcher sub-agent node with a ReAct tool loop.

    Calls the LLM (with tools bound), executes any requested tool calls,
    feeds the results back as ``ToolMessage`` objects, and repeats until
    the LLM returns a final answer or the iteration cap is reached.

    The conversation slice sent to the provider is trimmed so that it always
    ends with a ``HumanMessage``, satisfying the Anthropic API constraint.

    Args:
        state: Current agent state from LangGraph.

    Returns:
        Updated state dict with ``current_agent`` set to ``'researcher'``
        and new ``messages`` containing the research results.
    """
    session_id = state.get("session_id")
    logger.debug("researcher_node_called", session_id=session_id)

    registry = ProviderRegistry()
    llm = registry.get_llm_with_fallback()
    active_tools = get_tools_for_agent("researcher")
    llm_with_tools = llm.bind_tools(active_tools)

    system = [SystemMessage(content=_SYSTEM_PROMPT)]
    loop_messages = list(_trim_to_last_human(list(state["messages"])))

    response = await react_loop(
        llm_with_tools,
        active_tools,
        system + loop_messages,
        agent_name="researcher",
        max_iterations=_MAX_TOOL_ITERATIONS,
        session_id=str(session_id) if session_id else None,
    )

    logger.info("researcher_complete", session_id=session_id)
    return {"current_agent": "researcher", "messages": [response]}


__all__ = ["researcher_node"]
