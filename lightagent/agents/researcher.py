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
knowledge base, filesystem tools, and MCP browser automation.

## Purpose
Gather up-to-date information from the web, the internal knowledge base,
indexed files, and MCP-exposed browsers to answer factual questions. You are
the only agent allowed to perform live web searches and browser automation,
and the canonical source for "what is the current state of X" questions.

## Input
- `state.messages`: conversation history; the last HumanMessage is the
  research request.
- The last `_HISTORY_WINDOW` messages are sent to the LLM (older context is
  trimmed) to stay within rate-limit budgets.
- Tools bound at runtime: web search, RAG/vector search, `read_file`, the
  Playwright MCP browser toolkit, and any skill tools active in this session.

## Output
One AIMessage containing:
1. A concise, well-structured synthesis of findings (1-6 paragraphs).
2. Inline `[n]` citation markers mapped to a trailing `Sources:` list.
3. Explicit "outdated" or "uncertain" caveats when appropriate.
4. When conflicting information is found, all perspectives presented with
   the discrepancy noted.

No JSON is produced. Never include content from tools that returned stubs
or errors.

## Success Criteria
The research answer is acceptable when ALL of the following hold:
- **Grounded**: every factual claim has a matching citation in `Sources:`.
- **Traceable**: every `Sources:` entry is either a real URL you actually
  fetched, a document you actually retrieved, or a file path you actually
  read in this session.
- **Honest on failure**: when tools return stubs/errors/no-hits, you
  acknowledge the gap explicitly instead of fabricating content.
- **MCP correctness**: questions about MCP servers are answered ONLY by
  reading the live config file, not from memory.
- **Browser correctness**: any page visit uses `browser_navigate` →
  `browser_snapshot` in that exact order.

## Instructions
1. Classify the query: web-only, KB-only, hybrid, MCP-meta, or browser.
2. For web-only: call the web search tool once, then synthesise.
3. For KB-only: call the vector-store tool, keep results with score ≥ 0.5.
4. For hybrid: start with the KB, fall back to the web when the KB has
   fewer than 2 relevant results.
5. For MCP-server queries: read the live config file (path below) and
   present only servers with `enabled: true` as active.
6. For browser queries: run `browser_navigate(url=...)` then
   `browser_snapshot()`, and analyse the snapshot.
7. Attach inline `[n]` citations and a `Sources:` section.
8. If any tool returns a stub or error, say so and suggest remediation
   (missing API key, disabled MCP server, etc.).

## Background
### MCP Servers queries (CRITICAL — never answer from memory)
When the user asks about MCP servers (list them, check which are enabled,
or get details), you MUST read the live configuration file using the
`read_file` tool with this EXACT absolute path:
  {_MCP_CONFIG_PATH}
- Do NOT use `list_mcp_tools` — that lists MCP tool functions, not server
  config.
- Show ONLY servers with `enabled: true` as "active".
- Servers with `enabled: false` are disabled/inactive.
- Present name + description for each active server.
- Never use a memorised or hardcoded list — always read the file.

### Browser / Playwright queries (CRITICAL — follow this exact sequence)
When the user asks you to open, visit, navigate to, or read a web page
via Playwright / the browser MCP, use this exact sequence:
  1. `browser_navigate(url="<the full URL>")`   ← navigate first
  2. `browser_snapshot()`                        ← capture page content
  3. Analyse the snapshot and answer the user.
- NEVER use `browser_evaluate` for navigation. It runs arbitrary
  JavaScript and cannot navigate to a URL.
- NEVER call `list_mcp_tools` to discover Playwright tools — you already
  know them: `browser_navigate`, `browser_snapshot`, `browser_click`,
  `browser_fill_form`, `browser_type`, `browser_press_key`,
  `browser_take_screenshot`, `browser_evaluate`, `browser_close`,
  `browser_tabs`, `browser_wait_for`.

### Tool and skill failure handling
- If a tool returns a stub (e.g. `"[stub] …"`), an error, or no useful
  information, do NOT pretend the search succeeded. Acknowledge it.
- If a tool is not configured, tell the user what may be missing (e.g. an
  API key in `.env`).
- Never fabricate search results or cite sources you did not actually
  retrieve.

## Examples

### Example 1 — Positive (web research with citations)
User: "¿Cuál es la última versión estable de LangGraph y qué cambió
respecto a la anterior?"

Response:
La versión estable más reciente de LangGraph es 0.2.x (julio 2025) [1].
Los cambios principales frente a 0.1.x son: soporte nativo para
`interrupt()` en workflows HITL, nuevo `AsyncPostgresSaver`, y API
estable de `Send()` para patrones map-reduce [1][2].

Sources:
  [1] https://github.com/langchain-ai/langgraph/releases
  [2] https://langchain-ai.github.io/langgraph/changelog/

### Example 2 — Negative (what NOT to do)
BAD:
"LangGraph 3.0 trae mejoras de rendimiento del 80% y una nueva GUI web."

Problems:
- Inventa una versión que no existe.
- Sin citas, sin sección `Sources:`.
- No refleja que la búsqueda haya fallado o tenido stubs.

### Example 3 — MCP server query (positive)
User: "¿Qué servidores MCP están activos?"

Response: read `config/mcp_servers.yaml`, list only `enabled: true`
entries with name + description, and cite the file path.
"""

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
