"""Dynamic tool registry for LightAgent sub-agents.

Merges three tool sources at call time:

1. **MCP tools** — from connected MCP servers via ``MCPClientManager``
   (initialised once as a module-level singleton).
2. **Skill tools** — from active skills via ``SkillsManager.get_active_tools()``.
3. **Fallback stubs** — the static tools from ``tools.py``, used only when
   no real tool with the same name is available from MCP or skills.

Usage::

    from lightagent.agents.tool_registry import get_tools_for_agent, init_mcp

    # Call once at app startup (e.g. FastAPI lifespan or graph init)
    await init_mcp()

    # Call inside each agent node to get live tools
    tools = get_tools_for_agent("researcher")
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = get_logger("lightagent.agents.tool_registry")

# ---------------------------------------------------------------------------
# MCP singleton
# ---------------------------------------------------------------------------

_mcp_manager: object | None = None  # MCPClientManager | None
_mcp_initialized: bool = False
_mcp_lock = asyncio.Lock()

_DEFAULT_MCP_CONFIG = Path("config/mcp_servers.yaml")


async def init_mcp(config_path: Path | None = None) -> None:
    """Initialise the module-level MCPClientManager singleton.

    Safe to call multiple times — subsequent calls are no-ops.
    Should be awaited once at application startup.

    Args:
        config_path: Override config path (defaults to
            ``config/mcp_servers.yaml``).
    """
    global _mcp_manager, _mcp_initialized  # globals needed for module-level singleton

    async with _mcp_lock:
        if _mcp_initialized:
            return

        try:
            from lightagent.mcp.client import MCPClientManager

            mgr = MCPClientManager(config_path or _DEFAULT_MCP_CONFIG)
            await mgr.load_from_config()
            _mcp_manager = mgr
            connected = [s.name for s in mgr.get_server_status() if s.connected]
            logger.info(
                "tool_registry.mcp_initialized",
                servers=connected,
                count=len(connected),
            )
        except Exception as exc:
            logger.warning(
                "tool_registry.mcp_init_failed",
                error=str(exc),
                hint="MCP tools will not be available",
            )
        finally:
            _mcp_initialized = True


def get_mcp_tools() -> list[BaseTool]:
    """Return all tools currently available from connected MCP servers.

    Returns:
        Empty list when MCP has not been initialised or no servers are
        connected.
    """
    if _mcp_manager is None:
        return []
    try:
        from lightagent.mcp.client import MCPClientManager

        if isinstance(_mcp_manager, MCPClientManager):
            return _mcp_manager.get_all_langchain_tools()
    except Exception as exc:
        logger.warning("tool_registry.get_mcp_tools_error", error=str(exc))
    return []


def get_skill_tools() -> list[BaseTool]:
    """Return all tools from currently active skills.

    Returns:
        Empty list when no skills are active or the SkillsManager fails.
    """
    try:
        from lightagent.skills.manager import SkillsManager

        return SkillsManager().get_active_tools()
    except Exception as exc:
        logger.warning("tool_registry.get_skill_tools_error", error=str(exc))
    return []


# Maximum number of MCP tools injected per agent call.
# The cap is applied globally across all connected MCP servers.  Setting it
# too low silently drops tools from servers that appear later in the list
# (e.g. playwright browser_navigate being cut out when filesystem already
# consumes 10+ slots out of 20).  Claude Sonnet 4.6 handles 60 tool schemas
# (~15 k tokens) without issue.
_MAX_MCP_TOOLS: int = 60

# Agents that operate on a fixed, minimal tool set and must NOT receive MCP
# or skill tools.  Loading 60+ MCP schemas + unbounded skill schemas into
# these agents causes the prompt to exceed Anthropic's 30 k token/min rate
# limit.  These agents only need their own stub tools to function correctly.
_FIXED_TOOL_AGENTS: frozenset[str] = frozenset({"cron_manager", "critic"})

# ---------------------------------------------------------------------------
# Per-agent tool merge
# ---------------------------------------------------------------------------


def get_tools_for_agent(agent_name: str) -> list[BaseTool]:
    """Return the merged tool list for a named agent.

    Merge strategy (highest priority first):

    1. MCP tools — real tools from connected servers.
    2. Skill tools — real tools from active skills.
    3. Stub fallbacks — static stubs filtered to the agent's relevant subset,
       only included when no live tool with the same name exists.

    Args:
        agent_name: One of the known agent names (``"researcher"``,
            ``"coder"``, etc.).

    Returns:
        Deduplicated list of ``BaseTool`` instances.
    """
    from lightagent.agents.tools import (
        CODER_TOOLS,
        CRITIC_TOOLS,
        CRON_MANAGER_TOOLS,
        DATA_ANALYST_TOOLS,
        FILE_MANAGER_TOOLS,
        RAG_AGENT_TOOLS,
        RESEARCHER_TOOLS,
        read_file,
        write_file,
    )
    from lightagent.sandbox.tools import SANDBOX_TOOLS

    stub_map: dict[str, list[BaseTool]] = {
        "researcher": RESEARCHER_TOOLS,
        "coder": CODER_TOOLS + SANDBOX_TOOLS,
        "rag_agent": RAG_AGENT_TOOLS,
        "critic": CRITIC_TOOLS,
        "data_analyst": DATA_ANALYST_TOOLS + SANDBOX_TOOLS,
        "file_manager": FILE_MANAGER_TOOLS,
        # Planner needs file I/O to persist generated specs to disk;
        # SDD skill tools (guide, read_reference, validate_specs) are
        # injected automatically via get_skill_tools() when activated.
        # Cron management tools are also available to the planner so it
        # can schedule recurring agent tasks on behalf of the user.
        "planner": [read_file, write_file, *CRON_MANAGER_TOOLS],
        "cron_manager": CRON_MANAGER_TOOLS,
    }

    stubs = stub_map.get(agent_name, [])

    # Fixed-tool-set agents skip MCP and skill loading entirely.
    # Their prompts must stay small to avoid rate-limit errors.
    if agent_name in _FIXED_TOOL_AGENTS:
        logger.debug(
            "tool_registry.tools_resolved",
            agent=agent_name,
            live=0,
            stubs_kept=len(stubs),
            total=len(stubs),
        )
        return stubs

    mcp_tools = get_mcp_tools()[:_MAX_MCP_TOOLS]  # cap to avoid token explosion
    skill_tools = get_skill_tools()
    live_tools: list[BaseTool] = mcp_tools + skill_tools
    live_names = {t.name for t in live_tools}

    filtered_stubs = [t for t in stubs if t.name not in live_names]

    merged = live_tools + filtered_stubs

    logger.debug(
        "tool_registry.tools_resolved",
        agent=agent_name,
        live=len(live_tools),
        stubs_kept=len(filtered_stubs),
        total=len(merged),
    )
    return merged


# ---------------------------------------------------------------------------
# Shared ReAct execution loop
# ---------------------------------------------------------------------------

_MAX_REACT_ITERATIONS: int = 5

# After this many consecutive *permanent* failures a tool is considered
# permanently unavailable for the current session and subsequent LLM requests
# to use it are answered with an "unavailable" ToolMessage without calling
# the service.  Rate-limit (429) errors do NOT count toward this budget.
_MAX_TOOL_FAILURES: int = 2

# Delay (seconds) injected after a rate-limit (429) response to respect the
# API's back-off window.  Keeps the loop alive without hammering the service.
_RATE_LIMIT_BACKOFF: float = 1.2

# Pattern to detect rate-limit errors by message content.
_RATE_LIMIT_RE = re.compile(r"429|rate.?limit|too.many.requests", re.IGNORECASE)


async def react_loop(
    llm: object,
    tools: list[BaseTool],
    messages: list[object],
    *,
    agent_name: str = "agent",
    max_iterations: int = _MAX_REACT_ITERATIONS,
    session_id: str | None = None,
) -> object:
    """Execute a ReAct (Reason + Act) tool loop until the LLM returns a final answer.

    Calls the LLM, executes any requested tool calls, feeds results back as
    ``ToolMessage`` objects, and repeats until the LLM produces a response with
    no pending tool calls or *max_iterations* is reached.

    The last message in *messages* must satisfy the provider constraint of
    ending on a ``HumanMessage`` — callers are responsible for this invariant.

    **Failure resilience** — two mechanisms prevent the loop from hammering a
    permanently broken tool (e.g. Tavily 400 caused by a bad/missing API key):

    1. *Per-tool failure budget* — once a tool has failed
       ``_MAX_TOOL_FAILURES`` times consecutively it is marked unavailable for
       the remainder of the session.  Subsequent LLM requests to call it are
       answered with an "unavailable" ToolMessage without making the real call.

    2. *All-tools-failed early exit* — if every tool call in an iteration
       fails (success count == 0) the loop terminates immediately and asks
       the LLM to synthesise a best-effort answer from what it already knows,
       rather than burning additional iterations against broken services.

    Args:
        llm: A LangChain chat model already bound with tools via
            ``llm.bind_tools(tools)``.
        tools: Tool list (used to build the dispatch map by name).
        messages: Conversation slice to send on the first iteration.
            Subsequent iterations extend this list with intermediate results.
        agent_name: Agent name, used only for structured log entries.
        max_iterations: Maximum number of LLM + tool-execution cycles.
        session_id: Optional session ID for log correlation.

    Returns:
        The final :class:`~langchain_core.messages.AIMessage` produced by
        the LLM (either because it had no tool calls, because all tools
        failed, or because the iteration cap was reached).
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    tool_map: dict[str, BaseTool] = {t.name: t for t in tools}
    loop_messages: list[object] = list(messages)
    response = AIMessage(content="")

    # Consecutive failure counter per tool name — reset to 0 on success.
    _tool_fail_counts: dict[str, int] = {}

    for iteration in range(max_iterations):
        response = await llm.ainvoke(loop_messages)  # type: ignore[assignment]

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            break  # Final answer — no more tool calls requested

        logger.debug(
            "react_loop.tool_calls",
            agent=agent_name,
            iteration=iteration,
            tools=[tc["name"] for tc in tool_calls],
            session_id=session_id,
        )

        loop_messages.append(response)
        successful_calls = 0

        # Tools rate-limited within THIS iteration (transient — do not count
        # toward the permanent failure budget; the service is just throttling).
        _rate_limited_this_iter: set[str] = set()

        for tc in tool_calls:
            tool_name: str = tc["name"]
            tool_fn = tool_map.get(tool_name)

            if tool_fn is None:
                logger.warning(
                    "react_loop.tool_not_found",
                    agent=agent_name,
                    tool_name=tool_name,
                    available_tools=sorted(tool_map.keys()),
                    session_id=session_id,
                )
                result = f"Tool '{tool_name}' not found."

            elif _tool_fail_counts.get(tool_name, 0) >= _MAX_TOOL_FAILURES:
                # Tool exhausted its permanent failure budget — skip without calling.
                logger.warning(
                    "react_loop.tool_skipped_unavailable",
                    agent=agent_name,
                    tool_name=tool_name,
                    failures=_tool_fail_counts[tool_name],
                    session_id=session_id,
                )
                result = (
                    f"Tool '{tool_name}' is currently unavailable "
                    f"(failed on {_tool_fail_counts[tool_name]} previous attempts). "
                    "Please use a different approach or inform the user that this "
                    "service cannot be reached right now."
                )

            elif tool_name in _rate_limited_this_iter:
                # Already hit the rate limit for this tool in this iteration.
                # Skip without an API call so we don't keep hammering the quota.
                logger.debug(
                    "react_loop.tool_skipped_rate_limited",
                    agent=agent_name,
                    tool_name=tool_name,
                    session_id=session_id,
                )
                result = (
                    f"Tool '{tool_name}' was rate-limited earlier in this step "
                    "and has been skipped to avoid exceeding the API quota. "
                    "Please space out search calls or use fewer concurrent queries."
                )

            else:
                try:
                    result = str(await tool_fn.ainvoke(tc.get("args", {})))
                    _tool_fail_counts[tool_name] = 0  # reset on success
                    successful_calls += 1
                except Exception as exc:
                    if _RATE_LIMIT_RE.search(str(exc)):
                        # Transient rate-limit (429): mark for this iteration only.
                        # Do NOT burn the permanent failure budget so the tool
                        # remains available in the next iteration after the
                        # back-off window resets.
                        _rate_limited_this_iter.add(tool_name)
                        logger.warning(
                            "react_loop.tool_rate_limited",
                            agent=agent_name,
                            tool_name=tool_name,
                            error=str(exc),
                            backoff_seconds=_RATE_LIMIT_BACKOFF,
                            session_id=session_id,
                        )
                        await asyncio.sleep(_RATE_LIMIT_BACKOFF)
                        result = (
                            f"Tool '{tool_name}' is temporarily rate-limited "
                            f"(HTTP 429). A {_RATE_LIMIT_BACKOFF}s back-off was "
                            "applied. Please reduce the number of concurrent "
                            "requests to this tool in your next step."
                        )
                    else:
                        # Permanent-style failure: increment the failure budget.
                        _tool_fail_counts[tool_name] = (
                            _tool_fail_counts.get(tool_name, 0) + 1
                        )
                        logger.warning(
                            "react_loop.tool_error",
                            agent=agent_name,
                            tool_name=tool_name,
                            error=str(exc),
                            total_failures=_tool_fail_counts[tool_name],
                            session_id=session_id,
                        )
                        result = f"Tool error: {exc}"

            # Cap individual tool results to avoid token explosion.
            if len(result) > 4_000:
                result = result[:4_000] + "\n…[truncated]"
            loop_messages.append(
                ToolMessage(content=result, tool_call_id=tc["id"])
            )

        # ── All-tools-failed early exit ───────────────────────────────────
        # Fire only when every call in this iteration had a *permanent* error
        # (bad API key, server down, …).  Rate-limit (429) failures are
        # transient — the service is reachable, just throttling — so they do
        # not count as a permanent failure and the loop must continue to the
        # next iteration where the back-off window has likely reset.
        if successful_calls == 0 and not _rate_limited_this_iter:
            logger.warning(
                "react_loop.all_tools_failed",
                agent=agent_name,
                iteration=iteration,
                session_id=session_id,
            )
            loop_messages.append(
                HumanMessage(
                    content=(
                        "All tool calls in this step failed — the required external "
                        "services may be temporarily unavailable or misconfigured. "
                        "Based on what you already know, please provide your best "
                        "answer and clearly state which information you could not "
                        "retrieve and why."
                    )
                )
            )
            response = await llm.ainvoke(loop_messages)  # type: ignore[assignment]
            break

    else:
        logger.warning(
            "react_loop.iteration_cap_reached",
            agent=agent_name,
            max_iterations=max_iterations,
            session_id=session_id,
        )
        # The last `response` still has pending tool_calls and no useful content.
        # Append the last tool results already in loop_messages and ask the LLM
        # to synthesise a final answer from everything gathered so far.
        loop_messages.append(response)
        loop_messages.append(
            HumanMessage(
                content=(
                    "You have reached the maximum number of tool call iterations. "
                    "Based on everything you have gathered so far, please provide "
                    "your best final answer to the user's original question. "
                    "If you were unable to retrieve the needed information, say so "
                    "clearly and explain what you found instead."
                )
            )
        )
        response = await llm.ainvoke(loop_messages)  # type: ignore[assignment]

    return response


__all__ = [
    "get_mcp_tools",
    "get_skill_tools",
    "get_tools_for_agent",
    "init_mcp",
    "react_loop",
]
