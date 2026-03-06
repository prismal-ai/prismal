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

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from lightagent.agents.tools import RESEARCHER_TOOLS
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

# Build a name → callable map once at import time
_TOOL_MAP: dict[str, Any] = {t.name: t for t in RESEARCHER_TOOLS}


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
    the LLM returns a final answer or ``_MAX_TOOL_ITERATIONS`` is reached.

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
    llm_with_tools = llm.bind_tools(RESEARCHER_TOOLS)

    system = [SystemMessage(content=_SYSTEM_PROMPT)]

    # Local message list for the ReAct loop — starts from state history
    # trimmed so the last message is always a HumanMessage.
    loop_messages: list[BaseMessage] = list(
        _trim_to_last_human(list(state["messages"]))
    )

    response: AIMessage = AIMessage(content="")

    for iteration in range(_MAX_TOOL_ITERATIONS):
        response = await llm_with_tools.ainvoke(system + loop_messages)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            # LLM produced a final answer — exit the loop
            break

        logger.debug(
            "researcher_tool_calls",
            iteration=iteration,
            tools=[tc["name"] for tc in tool_calls],
            session_id=session_id,
        )

        # Append the assistant's tool-call message before the results
        loop_messages.append(response)

        # Execute each requested tool and collect ToolMessages
        for tc in tool_calls:
            tool_fn = _TOOL_MAP.get(tc["name"])
            if tool_fn is None:
                result = f"Tool '{tc['name']}' not found in RESEARCHER_TOOLS."
            else:
                try:
                    result = str(tool_fn.invoke(tc.get("args", {})))
                except Exception as exc:
                    result = f"Tool error: {exc}"
            loop_messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
    else:
        logger.warning(
            "researcher_tool_iteration_cap_reached",
            max_iterations=_MAX_TOOL_ITERATIONS,
            session_id=session_id,
        )

    logger.info("researcher_complete", session_id=session_id)
    return {"current_agent": "researcher", "messages": [response]}


__all__ = ["researcher_node"]
