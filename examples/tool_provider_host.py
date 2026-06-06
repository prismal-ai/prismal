"""Host-style tool provider composition and injection (Fase Y).

What a host (prismal-sdk / prismal-web) does in its lifespan:

1. Compose the standard providers (MCP + Skills + stub fallback).
2. Inject the composite once with ``set_tool_provider`` (variante A).
3. Optionally hand a per-session provider to the graph (variante B).

By default this demo composes Skills + stubs only so it runs offline and
deterministically. Set ``EXAMPLE_USE_MCP=1`` to use
``build_default_tool_provider`` instead, which also connects the MCP servers
from ``config/mcp_servers.yaml``.

Run::

    python examples/tool_provider_host.py
    EXAMPLE_USE_MCP=1 python examples/tool_provider_host.py
"""

from __future__ import annotations

import asyncio
import os

from langchain_core.tools import StructuredTool

from prismal.agents.extension import (
    CompositeToolProvider,
    FakeToolProvider,
    SkillToolProvider,
    StubToolProvider,
    build_default_tool_provider,
)
from prismal.agents.tool_registry import (
    get_tools_for_agent,
    get_tools_for_agent_ctx,
    set_tool_provider,
)


def _make_tool(name: str) -> StructuredTool:
    def _fn(query: str = "") -> str:
        return f"[{name}] {query}"

    return StructuredTool.from_function(func=_fn, name=name, description=f"demo {name}")


async def variante_a_global() -> None:
    """Compose once at startup, inject globally — what a host lifespan does."""
    if os.environ.get("EXAMPLE_USE_MCP") == "1":
        # Full composite: MCP (from config/mcp_servers.yaml) + Skills + stubs.
        provider = await build_default_tool_provider()
    else:
        # Offline composite: Skills + stub fallback (same merge policy).
        provider = CompositeToolProvider([SkillToolProvider(), StubToolProvider()])

    print("providers:", [type(p).__name__ for p in provider.providers])
    set_tool_provider(provider)

    # The 26 agent nodes keep calling get_tools_for_agent unchanged.
    for agent in ("researcher", "coder", "cron_manager"):
        tools = get_tools_for_agent(agent)
        print(f"{agent:>14}: {len(tools)} tools (first: {[t.name for t in tools[:3]]})")


async def variante_b_per_session() -> None:
    """Per-session toolsets without touching global state (multi-tenant).

    A real host enables ``settings.tool_provider_mode = "context"`` and binds
    the provider into the compiled graph::

        graph = await get_async_compiled_graph(tool_provider=provider_for(user))

    Nodes then resolve through ``get_tools_for_agent_ctx(name, config)``.
    Here we exercise the resolution helper directly with two sessions.
    """
    provider_u = FakeToolProvider(default=[_make_tool("user_u_search")])
    provider_v = FakeToolProvider(default=[_make_tool("user_v_search")])

    config_u = {"configurable": {"tool_provider": provider_u}}
    config_v = {"configurable": {"tool_provider": provider_v}}

    tools_u, tools_v = await asyncio.gather(
        asyncio.to_thread(get_tools_for_agent_ctx, "researcher", config_u),
        asyncio.to_thread(get_tools_for_agent_ctx, "researcher", config_v),
    )
    print("session U sees:", [t.name for t in tools_u])
    print("session V sees:", [t.name for t in tools_v])


async def main() -> None:
    print("— Variante A (global) —")
    await variante_a_global()
    print("\n— Variante B (per-session) —")
    await variante_b_per_session()


if __name__ == "__main__":
    asyncio.run(main())
