# Analysis: injecting MCP and Skills from an external layer (prismal-sdk / prismal-web)

> Status: architecture proposal. Does not modify code yet.
> Scope: the agents layer (`prismal/agents`), the `prismal/mcp` and `prismal/skills` subsystems, and the extension surface (`prismal/agents/extension`).

## 1. Question

Is it feasible for the incorporation of **MCP** and **Skills** tools to be done by **injection from a different component** (e.g. `prismal-sdk`, `prismal-web`) and **not directly from the agent architecture layer**?

Short answer: **yes, it is feasible and, moreover, it is the natural direction of the current design.** The repository already has almost all the pieces (a tool *facade*, hexagonal ports, and an `init_mcp` startup point). What is missing is to invert a dependency: today the agents layer *reaches down* to build MCP and Skills; the proposal is for an external component to *build and inject* them.

## 2. How it is coupled today

The single integration point is `prismal/agents/tool_registry.py`. It works as a *facade* that mixes three tool sources per call:

1. **MCP** — via a module *singleton* `_mcp_manager: MCPClientManager`.
2. **Skills** — via `SkillsManager().get_active_tools()`.
3. **Static stubs** — from `tools.py`, only as a *fallback*.

Each agent-node consumes tools with a static call by name:

```python
# prismal/agents/researcher.py:220, coder.py:169, rag_agent.py:236, ...
tools = get_tools_for_agent("researcher")
```

And the *registry* reaches **down** to the concrete subsystems:

```python
# tool_registry.py
from prismal.mcp.client import MCPClientManager      # get_mcp_tools()
from prismal.skills.manager import SkillsManager      # get_skill_tools()
```

Consequences of the current design:

- **Wrong dependency direction.** The agents layer (orchestration core) depends on `prismal.mcp` and `prismal.skills` (integration subsystems). The core knows its peripherals.
- **Mutable global state.** `_mcp_manager`, `_mcp_initialized`, `_mcp_lock` are module globals. The lifecycle (which servers, which config, when to connect) stays inside the core, not in whoever starts the app.
- **Non-injectable construction.** `init_mcp()` instantiates `MCPClientManager(config_path)` internally. The caller cannot substitute the *manager* (e.g. one with a web user's auth, or a *mock* in tests) without patching the module.
- **Opaque skill activation.** `get_skill_tools()` instantiates `SkillsManager()` per call and returns `get_active_tools()`; which skills are active is a disk/process state, not something the host controls per session.
- **Coupling by name.** The agent→stubs map and `DEFAULT_CAPABILITY_MAP` live inside the registry; adding a *host* with a different tool set forces touching the core.

Relevant fact: `grep` confirms that **nobody inside `prismal/` calls `init_mcp()`**. It is already expected that startup is triggered by an external component (the sibling app/SDK). That is, the boundary already exists implicitly; it is only half-formalized.

## 3. What already works in favor

The repo has a hexagonal extension surface (Phase X, `specs/extension-surface/`) with almost everything needed:

- **`prismal/agents/extension/ports.py`** already defines structural `Protocol`s (`CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`). The "port + external implementation that conforms to the shape" pattern is established and tested.
- **`ToolPort`** already models an executable tool (`name`, `description`, `ainvoke`) — the unit that MCP and Skills produce.
- **`PrismalStateGraphBuilder.add_node(..., capabilities=[...])`** already accepts per-node capabilities, and `DEFAULT_CAPABILITY_MAP` already routes capabilities per agent (Phase E).
- **`discover_plugins(settings)`** already injects third-party subgraphs/nodes/tools/rag-engines via *entry points*, with allowlist/denylist. The precedent of "the host registers, the core consumes" already exists.

In other words: for the *checkpoint*, *audit*, and *embeddings* ports the dependency inversion **is already done**. MCP and Skills are the exception that remains to be normalized.

## 4. Proposal: a `ToolProviderPort` injected at composition

The core idea is to introduce a **tool provider port** and move the *construction* of MCP/Skills out of the core, to the component that composes the application (`prismal-sdk` / `prismal-web`).

### 4.1 New port (in `extension/ports.py`)

```python
@runtime_checkable
class ToolProviderPort(Protocol):
    """Tool source resolvable by agent/capability, at runtime."""

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
    ) -> list[ToolPort]: ...
```

Implementations that conform to this shape (all live **outside** the agents core):

- `McpToolProvider` — wraps `MCPClientManager` (moves the `get_mcp_tools` logic + the `_MAX_MCP_TOOLS` cap).
- `SkillToolProvider` — wraps `SkillsManager.get_active_tools()`.
- `StubToolProvider` — the `tools.py` *fallbacks* (can stay as the core's *default*).
- `CompositeToolProvider` — merges N providers applying the priority and dedupe strategy that `get_tools_for_agent` does today (MCP → Skills → stubs, with `_MAX_TOTAL_TOOLS`).

### 4.2 Injection by context, not by singleton

Replace the module *singleton* with a provider resolved from the composition context. Two variants, from smaller to larger change:

**(a) Injectable provider registry (minimal change, backward-compatible).**
`tool_registry` stops importing `prismal.mcp` and `prismal.skills`. Instead it exposes a *setter*:

```python
# tool_registry.py (core) — without imports of mcp/ or skills/
_provider: ToolProviderPort | None = None

def set_tool_provider(p: ToolProviderPort) -> None: ...

def get_tools_for_agent(agent_name, required_capabilities=None):
    if _provider is None:
        return _default_stub_provider.get_tools(agent_name=agent_name)  # fallback
    return _provider.get_tools(agent_name=agent_name, capabilities=required_capabilities)
```

The host (`prismal-sdk`/`prismal-web`) does, once at startup:

```python
# in prismal-sdk / prismal-web, NOT in prismal/agents
from prismal_sdk.tools import McpToolProvider, SkillToolProvider, CompositeToolProvider
from prismal.agents.tool_registry import set_tool_provider

provider = CompositeToolProvider([
    McpToolProvider(MCPClientManager("config/mcp_servers.yaml")),
    SkillToolProvider(SkillsManager()),
    StubToolProvider(),
])
set_tool_provider(provider)
```

This inverts the dependency: the core **no longer knows** `prismal.mcp` or `prismal.skills`; the host knows them and injects them. The nodes keep calling `get_tools_for_agent("coder")` unchanged → **migration without touching the 20+ agents**.

**(b) Injection via `AgentState` / graph config (cleaner, more invasive).**
Pass the provider in the compiled graph's config (`get_async_compiled_graph(tool_provider=...)`) and have each node read it from the state/config instead of a global. It eliminates the module state entirely and enables **a different provider per session/user** (key for multi-tenant `prismal-web`). It costs more because it touches the node signature.

Recommendation: start with **(a)** (decouples now, without regressions) and leave **(b)** as an evolution when `prismal-web` needs per-user isolation.

### 4.3 Where each thing lands

| Responsibility | Today | Proposal |
|---|---|---|
| Define the "tool source" contract | implicit in `tool_registry` | `ToolProviderPort` in `extension/ports.py` (core) |
| Build `MCPClientManager`, choose config, connect | `tool_registry.init_mcp` (core) | `prismal-sdk` / `prismal-web` (host) |
| Build `SkillsManager`, decide active skills | `tool_registry.get_skill_tools` (core) | `prismal-sdk` / `prismal-web` (host) |
| Merge / caps / priority strategy | `get_tools_for_agent` (core) | `CompositeToolProvider` (host) or stays in core as default |
| Consume tools per node | `get_tools_for_agent("name")` | same (no changes) |

## 5. Benefits

- **Correct dependency inversion.** `prismal/agents` stops depending on `prismal/mcp` and `prismal/skills`. The core becomes publishable and testable without MCP servers or skills on disk — consistent with the factory-injection pattern that the rest of the repo already uses ("the business accepts *callables*, the defaults wire the provider lazily").
- **Lifecycle in the host's hands.** `prismal-web` can create a provider per user/session (auth, server allowlist, skills enabled by plan). `prismal-sdk` can inject a *mock* provider in tests without patching globals.
- **Real multi-tenant.** Variant (b) allows two web users to see different *toolsets* without shared global state.
- **Consistency with `discover_plugins`.** Same principle that already governs plugins: the host discovers/registers, the core consumes.
- **Clearer security boundary.** The L1–L5 layers (`InputSanitizer`, `ActionInterceptor`, `AuditLogger`) stay in the core and are applied to *any* tool that enters through the port, wherever it comes from. The external provider cannot bypass them because execution still goes through `react_loop` and the `@prismal_node` *middleware*.

## 6. Risks and mitigations

- **Silent regression if nobody injects the provider.** Mitigation: *fallback* to `StubToolProvider` (degraded but functional behavior) and a structured *warning*, just as `get_mcp_tools` today returns `[]` if MCP was not initialized.
- **Startup order.** The host must inject **before** the graph's first turn. Mitigation: document it in the SDK/web *lifespan* (where `init_mcp` would already be expected today).
- **Caps and token limits.** `_MAX_MCP_TOOLS=60` and `_MAX_TOTAL_TOOLS=120` (OpenAI's limit) are platform policy, not the host's. Keep them in the core or in the "official" `CompositeToolProvider` so a host does not break them by accident.
- **`filterwarnings=error` in tests.** Any new deferred import must remain lazy so as not to break the core's import tree without extras installed.
- **Compatibility with the Phase E branch.** `DEFAULT_CAPABILITY_MAP` and `required_capabilities` must keep flowing down to `provider.get_tools(capabilities=...)`; the port signature already accounts for this.

## 7. Suggested incremental plan

1. Add `ToolProviderPort` to `extension/ports.py` (additive, breaking nothing).
2. Extract `McpToolProvider` / `SkillToolProvider` / `StubToolProvider` / `CompositeToolProvider` to a *host* module (ideally in `prismal-sdk`; transitionally in `prismal/agents/extension/providers.py` to avoid blocking).
3. Refactor `tool_registry`: replace `mcp`/`skills` imports with the injected provider + `set_tool_provider()`, keeping `get_tools_for_agent` as the stable API for the nodes.
4. Move the construction and startup (`init_mcp` equivalent) to `prismal-sdk` / `prismal-web`.
5. (Optional, phase 2) Variant (b): per-session provider via graph config for multi-tenant.
6. Tests: a `FakeToolProvider` replaces the real wiring; the core tests stop needing MCP/skills.

## 8. Conclusion

Injecting MCP/Skills from `prismal-sdk` / `prismal-web` is not only possible: it is the way to close an inconsistency in the current design, where *checkpoint*, *audit*, and *embeddings* are already inverted as ports but MCP and Skills are still built inside the core. The change can be made **backward-compatible and without touching the 20+ agent-nodes**, concentrating everything in `tool_registry` (a single file) plus a new port. The result is a `prismal` core that is publishable and testable in isolation, with the integration lifecycle where it belongs: in the component that composes the application.

### Key referenced files

- `prismal/agents/tool_registry.py` — current *facade* and coupling (`get_mcp_tools`, `get_skill_tools`, `init_mcp`, `get_tools_for_agent`).
- `prismal/agents/extension/ports.py` — existing hexagonal ports (`ToolPort`, etc.).
- `prismal/agents/extension/builder.py` / `plugins.py` — precedent of injection by the host.
- `prismal/mcp/client.py` (`MCPClientManager`), `prismal/skills/manager.py` (`SkillsManager`) — subsystems to wrap as providers.
