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
# Each tool schema costs ~200-300 tokens; 51 tools ≈ 12k tokens just in
# definitions, which combined with conversation history easily exceeds the
# 30k tokens/min rate limit on the free Anthropic tier.
_MAX_MCP_TOOLS: int = 20

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
        DATA_ANALYST_TOOLS,
        FILE_MANAGER_TOOLS,
        RAG_AGENT_TOOLS,
        RESEARCHER_TOOLS,
    )

    stub_map: dict[str, list[BaseTool]] = {
        "researcher": RESEARCHER_TOOLS,
        "coder": CODER_TOOLS,
        "rag_agent": RAG_AGENT_TOOLS,
        "critic": CRITIC_TOOLS,
        "data_analyst": DATA_ANALYST_TOOLS,
        "file_manager": FILE_MANAGER_TOOLS,
    }

    mcp_tools = get_mcp_tools()[:_MAX_MCP_TOOLS]  # cap to avoid token explosion
    skill_tools = get_skill_tools()
    live_tools: list[BaseTool] = mcp_tools + skill_tools
    live_names = {t.name for t in live_tools}

    stubs = stub_map.get(agent_name, [])
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


__all__ = ["get_mcp_tools", "get_skill_tools", "get_tools_for_agent", "init_mcp"]
