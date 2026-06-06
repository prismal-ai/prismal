# Prismal Tool Providers (Fase Y)

Tool resolution is a **hexagonal port**: the agent core asks an injected
`ToolProviderPort` for tools; the host (prismal-sdk / prismal-web / your app)
composes the concrete providers (MCP, Skills, stubs) and injects them at
startup. The core never imports `prismal.mcp` or `prismal.skills` — that
inversion is enforced by an architecture test.

All public symbols are importable from `prismal.agents.extension`. Runnable
copies of the snippets below live at
[`examples/tool_provider_host.py`](../examples/tool_provider_host.py) and
[`examples/tool_provider_custom.py`](../examples/tool_provider_custom.py).

---

## 1. Quickstart — host composition (variante A, global)

Call this once in your application lifespan (FastAPI startup or equivalent):

```python
from prismal.agents.extension import build_default_tool_provider
from prismal.agents.tool_registry import set_tool_provider
from prismal.core.config import get_settings

async def on_startup() -> None:
    provider = await build_default_tool_provider(get_settings())
    set_tool_provider(provider)
```

`build_default_tool_provider` assembles the standard composite:

- **`McpToolProvider`** — connects `config/mcp_servers.yaml` (skipped, with a
  log, when the file is missing or the connection fails).
- **`SkillToolProvider`** — tools from active skills.
- **`StubToolProvider`** — static fallbacks from `tools.py`, last.

Agent nodes are unaffected: they keep calling
`get_tools_for_agent("coder")` and receive exactly the same merged list as
before the refactor (see the parity table below).

### Explicit composition

When you need control over each source (custom MCP manager, per-plan skills):

```python
from prismal.agents.extension import (
    CompositeToolProvider, McpToolProvider, SkillToolProvider, StubToolProvider,
)
from prismal.mcp.client import MCPClientManager      # host code may import this
from prismal.skills.manager import SkillsManager

provider = CompositeToolProvider([
    McpToolProvider(MCPClientManager("config/mcp_servers.yaml")),
    SkillToolProvider(SkillsManager()),
    StubToolProvider(),          # convention: stub provider goes last (fallback)
])
set_tool_provider(provider)
```

---

## 2. Variante A vs variante B

| | **A — global** | **B — context (multi-tenant)** |
|---|---|---|
| Injection | `set_tool_provider(provider)` once at startup | `get_async_compiled_graph(tool_provider=provider)` per session |
| Resolution | `get_tools_for_agent(name)` | `get_tools_for_agent_ctx(name, config)` / `resolve_provider(config)` |
| Setting | `tool_provider_mode="global"` (default) | `tool_provider_mode="context"` |
| State | One provider per process | One provider per session; no shared global state |
| Use case | Single-tenant apps, CLIs, workers | prismal-web with per-user toolsets |

### Variante B — per-session toolsets

```python
from prismal.agents.extension import (
    CompositeToolProvider, McpToolProvider, SkillToolProvider, StubToolProvider,
)
from prismal.agents.graph import get_async_compiled_graph

async def graph_for_user(user) -> CompiledStateGraph:
    provider = CompositeToolProvider([
        McpToolProvider(await mcp_manager_for(user)),   # user's server allowlist
        SkillToolProvider(skills_for_plan(user.plan)),
        StubToolProvider(),
    ])
    # Requires settings.tool_provider_mode == "context".
    return await get_async_compiled_graph(tool_provider=provider)
```

The returned object is a lightweight `with_config` view of the shared compiled
graph — the graph and its checkpointer remain a singleton. Inside a node, the
session provider is resolved from the `RunnableConfig`:

```python
from prismal.agents.tool_registry import get_tools_for_agent_ctx

async def my_node(state, config):           # LangGraph passes config to nodes
    tools = get_tools_for_agent_ctx("researcher", config)
    ...
```

Two concurrent sessions never share tools: resolution is a pair of dict
lookups with no lock and no global mutation (covered by an isolation test).

---

## 3. Custom provider — replace the merge entirely

Any object with the right `get_tools` shape conforms `ToolProviderPort`
structurally — no base class, no registration:

```python
class MyToolProvider:
    def get_tools(self, *, agent_name, capabilities=None):
        return my_lookup(agent_name, capabilities)    # -> list[BaseTool]

set_tool_provider(MyToolProvider())
```

Contract rules:

- `get_tools` is **sync** and must **not raise** on a degraded source —
  return `[]` (the async part, connecting MCP, happens in the host before
  injection).
- `agent_name` selects tools per agent; ignore it if your source is global.
- `capabilities` is the Fase E filter; `None` means "no filter".
- Security is **not** your concern: every tool still executes through
  `react_loop` / `@prismal_node` middleware (L1–L5) downstream of the
  provider. A provider lists tools; it cannot bypass `ActionInterceptor`.

---

## 4. Tests — mock the wiring with `FakeToolProvider`

```python
from prismal.agents.extension import FakeToolProvider
from prismal.agents.tool_registry import set_tool_provider

def test_my_agent(monkeypatch):
    set_tool_provider(FakeToolProvider({"researcher": [echo_tool]}))
    ...
```

`FakeToolProvider(mapping, default=...)` is deterministic, does no I/O and
imports nothing heavy. In test suites prefer resetting the global between
tests:

```python
@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch):
    monkeypatch.setattr("prismal.agents.tool_registry._provider", None)
```

### No provider injected?

Without a provider the registry degrades to **stubs only** plus a structured
warning (`tool_registry.no_provider`). Set `tool_provider_strict=True` to turn
that into a `ToolProviderNotConfigured` exception for deployments that demand
a real toolset.

> Behaviour note vs the pre-Fase-Y world: previously, skills were loaded even
> when MCP was never initialised. Now, *no provider* means *stubs only* — the
> host opts back into skills/MCP with one `set_tool_provider` call.

---

## 5. Parity table — `get_tools_for_agent` semantics preserved

With the default composite, the output is identical to the historical
implementation (verified by parity tests in
`tests/unit/agents/extension/test_registry_delegation.py`):

| Rule | Old (`tool_registry`) | New (`CompositeToolProvider`) |
|---|---|---|
| Priority order | MCP → Skills → stubs | providers in list order → stub fallback last |
| Dedupe | stubs dropped when a live tool has the same name | same |
| MCP cap | `_MAX_MCP_TOOLS = 60` | `McpToolProvider(max_tools=60)` |
| Total cap | `_MAX_TOTAL_TOOLS = 120`, tail truncated | `CompositeToolProvider(max_total=120)` |
| Fixed-tool agents | `cron_manager`, `critic` → stubs only, no MCP/skills | `fixed_tool_agents=frozenset({"cron_manager", "critic"})` |
| Capability filter | only the MCP pool is filtered | only `McpToolProvider` receives `capabilities` |
| Source failure | `get_mcp_tools()` → `[]` on error | sub-provider error logged + skipped |
| Resolution log | `tool_registry.tools_resolved` | `tool_provider.tools_resolved` (same fields: `agent`, `live`, `stubs_kept`, `total`) |

### Deprecated shims (removed in the next minor)

| Old call | Replacement |
|---|---|
| `await init_mcp(path)` | `set_tool_provider(await build_default_tool_provider(mcp_config_path=path))` |
| `get_mcp_tools(caps)` | resolved by the injected provider |
| `get_skill_tools()` | resolved by the injected provider |

The shims still work (delegating to the injected provider) but emit
`DeprecationWarning`.

---

## 6. Observability

- Span **`prismal.tools.resolve`** per resolution, with attributes
  `prismal.agent`, `prismal.tool_provider` (`composite|mcp|skill|stub|fake`),
  `prismal.n_tools`, `prismal.fallback`, `prismal.capabilities`.
- Counters: `prismal.tool_provider_resolved_total{provider}`,
  `prismal.tools_injected_total{agent}`,
  `prismal.tool_provider_fallback_total`,
  `prismal.tool_provider_subprovider_errors_total{provider}`.

A healthy migrated deployment shows `tool_provider_fallback_total == 0`.
