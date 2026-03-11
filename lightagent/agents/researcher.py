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

from pathlib import Path
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
# Capped at 4: browser workflows (navigate → snapshot) fit in 3 rounds;
# 4 leaves one extra round for follow-up.  Higher values risk accumulating
# enough context to hit Anthropic's 30 k tokens/min rate limit.
_MAX_TOOL_ITERATIONS: int = 4

# Maximum number of recent messages sent to the LLM per researcher invocation.
# Long sessions accumulate many turns; sending the full history magnifies
# token usage when combined with the large system prompt and tool results,
# easily exceeding the 30 k tokens/min Anthropic rate limit.
_HISTORY_WINDOW: int = 6

# Absolute path to the MCP servers config — computed once at import time so
# the researcher can always read the live file regardless of the process CWD.
_MCP_CONFIG_PATH: str = str(
    Path(__file__).resolve().parents[2] / "config" / "mcp_servers.yaml"
)

_SYSTEM_PROMPT = f"""You are a research specialist with access to web search, a \
knowledge base, and filesystem tools.

Your responsibilities:
- Search the web for up-to-date information relevant to the user's query.
- Query the internal knowledge base (RAG) for domain-specific documents.
- Read referenced files when needed for additional context.
- Always cite your sources clearly, including URLs and document names.
- Synthesise findings into a concise, well-structured response.
- If conflicting information is found, present all perspectives and note discrepancies.
- Flag when information may be outdated or uncertain.

MCP Servers queries (CRITICAL — never answer from memory):
When the user asks about MCP servers (list them, check which are active/enabled,
or get details about a specific one), you MUST read the live configuration file
using the read_file tool with this EXACT absolute path:
  {_MCP_CONFIG_PATH}
Do NOT use list_mcp_tools — that lists MCP tool functions, not MCP server config.
After reading the file, parse and present the results:
- Show ONLY servers with `enabled: true` as "active".
- Servers with `enabled: false` are disabled/inactive.
- Present name and description for each active server.
- Never use a memorised or hardcoded list of MCP servers — always read the file.

Browser / Playwright queries (CRITICAL — follow this exact sequence):
When the user asks you to open, visit, navigate to, or read a web page using
Playwright or the browser MCP, use this exact sequence of tool calls:
  1. browser_navigate(url="<the full URL>")  ← navigate first
  2. browser_snapshot()                      ← capture page content
  3. Analyse the snapshot and answer the user.
NEVER use browser_evaluate for navigation. browser_evaluate runs arbitrary
JavaScript — it cannot navigate to a URL. Use browser_navigate for that.
NEVER call list_mcp_tools to discover Playwright tools — you already know them:
  browser_navigate, browser_snapshot, browser_click, browser_fill_form,
  browser_type, browser_press_key, browser_take_screenshot, browser_evaluate,
  browser_close, browser_tabs, browser_wait_for.

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
    # Trim to the most recent messages before applying the last-human invariant.
    # Prevents large conversation histories from exhausting the token-per-minute
    # budget when combined with the system prompt and tool results.
    recent = list(state["messages"])[-_HISTORY_WINDOW:]
    loop_messages = list(_trim_to_last_human(recent))

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
