# Prismal Tool Provider Injection — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/tool-provider-injection/PLAN.md` |
| **Architecture** | `specs/tool-provider-injection/ARCHITECTURE.md` |
| **TASKS** | `specs/tool-provider-injection/TASKS.md` |

---

## Conventions

- `from __future__ import annotations` in all modules.
- Imports of `prismal.mcp` / `prismal.skills` **deferred** (inside methods), never at the core module level.
- Tool return types: `langchain_core.tools.BaseTool` (conforms to `ToolPort`).
- Sync for tool resolution (parity with the current `get_tools_for_agent`, which is sync); the async MCP connection is done by the host before injecting.
- All public symbols are re-exported from `prismal/agents/extension/__init__.py`.

---

## Module Summary

| Module | Status | Content |
|---|---|---|
| `prismal/agents/extension/ports.py` | MODIFIED | `+ ToolProviderPort` |
| `prismal/agents/extension/providers.py` | NEW | `McpToolProvider`, `SkillToolProvider`, `StubToolProvider`, `CompositeToolProvider`, `FakeToolProvider`, `build_default_tool_provider` |
| `prismal/agents/extension/__init__.py` | MODIFIED | re-exports |
| `prismal/agents/tool_registry.py` | MODIFIED | `set_tool_provider`, `get_tool_provider`, delegation, deprecated shims |
| `prismal/agents/graph.py` | MODIFIED | `tool_provider` in config (variant B) |
| `prismal/core/config.py` | MODIFIED | `tool_provider_mode`, `tool_provider_strict` |
| `prismal/core/exceptions.py` | MODIFIED | `+ ToolProviderNotConfigured` |

---

## SPEC-TPI-001: `ToolProviderPort` (in `ports.py`)

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class ToolProviderPort(Protocol):
    """Source of tools resolvable per agent and capability, at runtime.

    Conforming to this shape: McpToolProvider, SkillToolProvider, StubToolProvider,
    CompositeToolProvider, FakeToolProvider and any host provider.
    The core only invokes get_tools(); it never constructs providers.
    """

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[ToolPort]: ...
```

Rules:
- `get_tools` is **sync** and **must not raise** when a source is down: it returns what it can (an empty list at minimum).
- `agent_name` allows a provider (e.g. `StubToolProvider`) to select tools per agent; providers that do not use it (MCP, Skills) ignore it.
- `capabilities` is the Phase E filter; `None` = no filter (full pool).

---

## SPEC-TPI-002: `McpToolProvider` (in `providers.py`)

```python
class McpToolProvider:
    def __init__(self, manager: "MCPClientManager", *, max_tools: int = 60) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Tools from connected MCP servers, filtered by capability and capped to max_tools.

        Equivalent to the current logic of tool_registry.get_mcp_tools():
            manager.get_all_langchain_tools(capabilities=capabilities)[:max_tools]
        Catches any exception from the manager and returns [] (parity).
        agent_name is ignored.
        """
```

- `max_tools` defaults to `60` (= current `_MAX_MCP_TOOLS`).
- Import of `MCPClientManager` **deferred** inside the method.

---

## SPEC-TPI-003: `SkillToolProvider` (in `providers.py`)

```python
class SkillToolProvider:
    def __init__(self, manager: "SkillsManager | None" = None) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Tools from active skills. Equivalent to SkillsManager().get_active_tools().

        capabilities and agent_name are ignored (parity: skills are not filtered today).
        Catches exceptions and returns [].
        """
```

- If `manager is None`, instantiates `SkillsManager()` lazily (deferred import).

---

## SPEC-TPI-004: `StubToolProvider` (in `providers.py`)

```python
class StubToolProvider:
    def __init__(
        self,
        *,
        fixed_tool_agents: frozenset[str] = frozenset({"cron_manager", "critic"}),
    ) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Static stubs from tools.py for agent_name (current stub_map).

        Encapsulates the stub_map of get_tools_for_agent:
            researcher → RESEARCHER_TOOLS
            coder → CODER_TOOLS + SANDBOX_TOOLS
            rag_agent → RAG_AGENT_TOOLS
            critic → CRITIC_TOOLS
            data_analyst → DATA_ANALYST_TOOLS + SANDBOX_TOOLS
            file_manager → FILE_MANAGER_TOOLS
            planner → [read_file, write_file, *CRON_MANAGER_TOOLS]
            cron_manager → CRON_MANAGER_TOOLS
            data_ingester/eda_analyst/feature_engineer/model_trainer/
              model_evaluator/model_exporter → ML_PIPELINE_TOOLS
            market_data_collector/technical_analyst/fundamental_analyst/
              risk_sentiment_analyst/report_generator → []
        Unknown agents → [].
        tools.py imports deferred.
        """
```

- `fixed_tool_agents` is exposed so that `CompositeToolProvider` decides the exemption (see SPEC-TPI-005). `StubToolProvider` itself always returns the agent's stub set.

---

## SPEC-TPI-005: `CompositeToolProvider` (in `providers.py`)

```python
class CompositeToolProvider:
    def __init__(
        self,
        providers: list[ToolProviderPort],
        *,
        max_total: int = 120,
        fixed_tool_agents: frozenset[str] = frozenset({"cron_manager", "critic"}),
    ) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Merges providers reproducing get_tools_for_agent EXACTLY:

        1. If agent_name ∈ fixed_tool_agents:
              return ONLY the stubs (the last stub-type provider), without MCP or skills.
        2. live = concat(p.get_tools(...) for p in providers except the final stub)
           respecting order (MCP → Skills).
        3. stubs = stub_provider.get_tools(agent_name=...)
           filtered_stubs = [s for s in stubs if s.name not in {t.name for t in live}]
        4. merged = live + filtered_stubs
        5. if len(merged) > max_total: truncate the tail (drop lowest priority).
        6. log tool_provider.tools_resolved(agent, live, stubs_kept, total).
        """
```

Ordering convention: the **last** provider in the list must be the `StubToolProvider` (stubs are the fallback). The earlier ones are "live" sources (MCP, Skills). `CompositeToolProvider` identifies the stub provider by `isinstance(p, StubToolProvider)`; if there are several, the last one wins as the fallback source.

- `max_total` defaults to `120` (= `_MAX_TOTAL_TOOLS`).
- A sub-provider that raises is caught, logged (`tool_provider.subprovider_error`), and skipped.

---

## SPEC-TPI-006: `FakeToolProvider` (in `providers.py`, for tests)

```python
class FakeToolProvider:
    def __init__(self, mapping: dict[str, list[BaseTool]] | None = None, *, default: list[BaseTool] | None = None) -> None: ...

    def get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]:
        """Returns mapping.get(agent_name, default or []). Deterministic, no I/O."""
```

---

## SPEC-TPI-007: `build_default_tool_provider` (in `providers.py`)

```python
async def build_default_tool_provider(
    settings: "Settings | None" = None,
    *,
    mcp_config_path: "Path | None" = None,
) -> CompositeToolProvider:
    """Assembles the standard CompositeToolProvider for host use.

    - If there is an MCP config: builds MCPClientManager(config), await load_from_config(),
      wraps it in McpToolProvider. If the connection fails: logs and skips MCP.
    - Builds SkillToolProvider(SkillsManager()).
    - Adds StubToolProvider() as the final fallback.
    - Returns CompositeToolProvider([mcp?, skill, stub]).

    Intended for the lifespan of prismal-sdk / prismal-web:
        provider = await build_default_tool_provider(settings)
        set_tool_provider(provider)
    """
```

- Async because it connects MCP. It is the **only** async piece of the feature; the rest of `get_tools` is sync.
- Lives in `extension/` (host-facing); it is not imported by the pure core.

---

## SPEC-TPI-008: Registry — injection and delegation (in `tool_registry.py`)

```python
# Module state (replaces _mcp_manager / _mcp_initialized / _mcp_lock)
_provider: ToolProviderPort | None = None

def set_tool_provider(provider: ToolProviderPort) -> None:
    """Injects the global provider. Idempotent; the host calls it once at startup."""

def get_tool_provider() -> ToolProviderPort | None:
    """Returns the injected global provider, or None."""

def get_tools_for_agent(
    agent_name: str,
    required_capabilities: list[str] | None = None,
) -> list[BaseTool]:
    """STABLE API (no signature changes). Delegates:

    provider = get_tool_provider()
    if provider is None:
        if get_settings().tool_provider_strict:
            raise ToolProviderNotConfigured(agent_name)
        logger.warning("tool_registry.no_provider", agent=agent_name)
        return _DEFAULT_STUB_PROVIDER.get_tools(agent_name=agent_name)
    return provider.get_tools(agent_name=agent_name, capabilities=required_capabilities)
    """

# _DEFAULT_STUB_PROVIDER: StubToolProvider  (fallback singleton, no MCP/skills)
```

Deprecated shims (delegate, emit `DeprecationWarning`):
```python
async def init_mcp(config_path: Path | None = None) -> None:
    """DEPRECATED. Use build_default_tool_provider(settings) + set_tool_provider() in the host."""

def get_mcp_tools(capabilities: list[str] | None = None) -> list[BaseTool]:
    """DEPRECATED. Resolved by the injected provider."""

def get_skill_tools() -> list[BaseTool]:
    """DEPRECATED. Resolved by the injected provider."""
```

`DEFAULT_CAPABILITY_MAP`, `get_recommended_capabilities`, `react_loop`, and all `react_loop` constants **remain unchanged**.

---

## SPEC-TPI-009: Variant B — context provider (in `graph.py`)

```python
async def get_async_compiled_graph(
    *,
    tool_provider: ToolProviderPort | None = None,
    # ... rest of the existing parameters unchanged ...
) -> CompiledStateGraph:
    """If tool_provider is passed and settings.tool_provider_mode == 'context',
    it is stored in the compilable config under configurable.tool_provider.
    """

# Resolution helper used by nodes in context mode:
def resolve_provider(config: "RunnableConfig | None") -> ToolProviderPort | None:
    """Reads config['configurable']['tool_provider'] or falls back to the global get_tool_provider()."""
```

In `context` mode, `get_tools_for_agent` accepts an optional `config` or the nodes call a helper `get_tools_for_agent_ctx(agent_name, config, required_capabilities)`. Variant A (global) does not require `config`.

---

## SPEC-TPI-010: Exceptions (in `core/exceptions.py`)

```python
class ToolProviderNotConfigured(PrismalError):
    """No ToolProviderPort injected and settings.tool_provider_strict is True."""
    def __init__(self, agent_name: str) -> None:
        super().__init__(
            f"No tool provider configured for agent '{agent_name}'. "
            "Call set_tool_provider(...) at startup, or set "
            "settings.tool_provider_strict=False to fall back to stubs."
        )
```

---

## SPEC-TPI-011: Settings (extension, in `core/config.py`)

```python
# Tool provider injection
tool_provider_mode: Literal["global", "context"] = "global"
tool_provider_strict: bool = False
```

- `global`: `set_tool_provider()` is used (variant A).
- `context`: the provider is resolved per session from the graph config (variant B).
- `strict`: if `True`, the absence of a provider raises `ToolProviderNotConfigured` instead of falling back to stubs.

---

## SPEC-TPI-012: Re-exports (in `extension/__init__.py`)

```python
from prismal.agents.extension.ports import ToolProviderPort
from prismal.agents.extension.providers import (
    CompositeToolProvider,
    FakeToolProvider,
    McpToolProvider,
    SkillToolProvider,
    StubToolProvider,
    build_default_tool_provider,
)
```

---

## Host Contract (prismal-sdk / prismal-web)

### Standard startup (variant A)
```python
from prismal.agents.extension import build_default_tool_provider
from prismal.agents.tool_registry import set_tool_provider
from prismal.core.config import get_settings

async def on_startup() -> None:
    provider = await build_default_tool_provider(get_settings())
    set_tool_provider(provider)
```

### Per-user toolset (variant B)
```python
from prismal.agents.extension import (
    CompositeToolProvider, McpToolProvider, SkillToolProvider, StubToolProvider,
)
from prismal.agents.graph import get_async_compiled_graph

async def graph_for_user(user) -> CompiledStateGraph:
    provider = CompositeToolProvider([
        McpToolProvider(await mcp_manager_for(user)),
        SkillToolProvider(skills_for_plan(user.plan)),
        StubToolProvider(),
    ])
    return await get_async_compiled_graph(tool_provider=provider)
```

### Custom provider (replaces the merge)
```python
class MyToolProvider:
    def get_tools(self, *, agent_name, capabilities=None):
        return my_lookup(agent_name, capabilities)   # conforms to ToolProviderPort
set_tool_provider(MyToolProvider())
```

---

## Compatibility and Versioning

- `ToolProviderPort` + providers are **public API**; breaking changes require a minor bump + `DeprecationWarning` 1 release in advance.
- `get_tools_for_agent` keeps its signature and semantics (parity verified by test).
- Shims `init_mcp`/`get_mcp_tools`/`get_skill_tools` are removed no earlier than version `X+1` (1 minor of deprecation).

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial interface specification — `ToolProviderPort`, providers, registry delegation |
