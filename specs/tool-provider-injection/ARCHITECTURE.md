# Prismal Tool Provider Injection — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Related PLAN** | `specs/tool-provider-injection/PLAN.md` |
| **Related SPEC** | `specs/tool-provider-injection/SPEC.md` |
| **TASKS** | `specs/tool-provider-injection/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |

---

## 1. Context

Prismal resolves each agent's tools in `prismal/agents/tool_registry.py`, a *facade* that merges three sources: **MCP** servers (via a module *singleton* `_mcp_manager: MCPClientManager`), active **Skills** (`SkillsManager().get_active_tools()`), and static *stubs* from `tools.py`. The merge applies priority (MCP → Skills → stubs), name dedupe, a per-server cap (`_MAX_MCP_TOOLS = 60`), and a total cap (`_MAX_TOTAL_TOOLS = 120`, OpenAI's limit), and exempts `_FIXED_TOOL_AGENTS = {cron_manager, critic}`.

The problematic coupling: **the agents core imports and builds its integration subsystems**. This contradicts Phase X (Extension Surface), where `CheckpointPort`/`AuditPort`/`EmbeddingsPort` are already inverted as hexagonal ports. This document describes **Phase Y — Tool Provider Injection**, which introduces a `ToolProviderPort` and moves the construction of MCP/Skills to the host (`prismal-sdk`, `prismal-web`), leaving the core as a pure consumer.

---

## 2. Technical Objectives

- **OT-1:** Invert the dependency: `prismal/agents/**` stops importing `prismal.mcp` and `prismal.skills`.
- **OT-2:** Model the tool sources as a `ToolProviderPort` (structural, no base class).
- **OT-3:** Keep `get_tools_for_agent(name)` as the stable API for the 20+ nodes (zero changes in agents).
- **OT-4:** Maintain exact parity of the merge (priority, dedupe, caps, fixed-tool agents).
- **OT-5:** Enable per-session injection (multi-tenant) without global state (variant B).
- **OT-6:** Preserve the L1–L5 security layers over any injected tool.
- **OT-7:** Degrade to stubs + warning when there is no provider (non-strict) or fail cleanly (strict).

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram — Dependency Inversion

```
BEFORE (current phase):

  prismal/agents/graph.py (nodes)
        │  get_tools_for_agent("coder")
        ▼
  prismal/agents/tool_registry.py  ──imports──▶ prismal/mcp/client.py (MCPClientManager)
        (facade + singleton _mcp_manager)  └──▶ prismal/skills/manager.py (SkillsManager)
                                            └──▶ prismal/agents/tools.py (stubs)

  ⮕ The core (agents) DEPENDS on the integration layers (mcp, skills).


AFTER (Phase Y):

  HOST (prismal-sdk / prismal-web)
        │  builds providers and injects
        │  set_tool_provider(CompositeToolProvider([...]))
        ▼
  prismal/agents/extension/providers.py
        ├─ McpToolProvider  ──▶ prismal/mcp/client.py (MCPClientManager)
        ├─ SkillToolProvider ─▶ prismal/skills/manager.py (SkillsManager)
        └─ StubToolProvider ──▶ prismal/agents/tools.py
        ▲
        │ conforms to ToolProviderPort
  prismal/agents/extension/ports.py  (ToolProviderPort : Protocol)
        ▲
        │ delegates
  prismal/agents/tool_registry.py  (get_tools_for_agent → provider.get_tools)
        ▲
        │ get_tools_for_agent("coder")  (unchanged)
  prismal/agents/graph.py (nodes)

  ⮕ The core (agents) knows nothing about mcp or skills. The host composes and injects.
```

### 3.2 Layer Diagram

```
┌──────────────────────────────────────────────────────────────┐
│  HOST: prismal-sdk / prismal-web                              │
│  - build_default_tool_provider(settings)                     │
│  - builds MCPClientManager, SkillsManager                    │
│  - set_tool_provider(...) (variant A)                         │
│  - get_async_compiled_graph(tool_provider=...) (variant B)   │
└───────────────┬──────────────────────────────────────────────┘
                │ injects
┌───────────────▼──────────────────────────────────────────────┐
│  PUBLISHABLE CORE: prismal/agents                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ extension/ports.py     → ToolProviderPort (contract)   │  │
│  │ extension/providers.py → Mcp/Skill/Stub/Composite      │  │
│  │ tool_registry.py       → get_tools_for_agent (delegate)│  │
│  │ react_loop + @prismal_node → L1–L5 over each tool      │  │
│  └────────────────────────────────────────────────────────┘  │
└───────────────┬──────────────────────────────────────────────┘
                │ uses (via wrappers, deferred imports)
┌───────────────▼──────────────────────────────────────────────┐
│  INTEGRATIONS: prismal/mcp, prismal/skills, agents/tools.py   │
│  (MCPClientManager, SkillsManager — no API changes)           │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 Components per Module

#### Y1 — `ToolProviderPort` (`prismal/agents/extension/ports.py`)
- `@runtime_checkable Protocol` with a single method `get_tools(*, agent_name: str, capabilities: list[str] | None) -> list[ToolPort]`.
- Reuses `ToolPort` (Phase X) as the return type.
- Added alongside the existing ports; re-exported from `extension/__init__.py`.
- Helper `conforms_to(obj, ToolProviderPort)` already available.

#### Y2 — Concrete providers (`prismal/agents/extension/providers.py`)
- **`McpToolProvider(manager: MCPClientManager, *, max_tools: int = 60)`** — `get_tools()` ignores `agent_name`, applies `manager.get_all_langchain_tools(capabilities=...)[:max_tools]`. Import of `MCPClientManager` deferred inside the providers module (which lives outside the forbidden `agents/` path from the architecture test's point of view — see DD-TPI-003).
- **`SkillToolProvider(manager: SkillsManager)`** — `get_tools()` returns `manager.get_active_tools()`; `capabilities` is ignored (parity with today: skills are not filtered).
- **`StubToolProvider()`** — encapsulates the current `stub_map` (researcher→RESEARCHER_TOOLS, coder→CODER_TOOLS+SANDBOX_TOOLS, …) and the `_FIXED_TOOL_AGENTS` exemption.
- **`CompositeToolProvider(providers: list[ToolProviderPort], *, max_total: int = 120, fixed_tool_agents=frozenset({"cron_manager","critic"}))`** — implements the current merge strategy: if `agent_name ∈ fixed_tool_agents`, returns stubs only; otherwise concatenates providers in order (MCP→Skills→stubs), filters stubs whose name already exists in live, and truncates to `max_total`. Emits the `tool_provider.tools_resolved` log (parity with `tool_registry.tools_resolved`).

#### Y3 — Global injection (`prismal/agents/tool_registry.py`, variant A)
- New module state: `_provider: ToolProviderPort | None = None`.
- `set_tool_provider(p)` / `get_tool_provider()`.
- `get_tools_for_agent(agent_name, required_capabilities=None)` → if `_provider` is `None`: uses `StubToolProvider` + warning `tool_registry.no_provider` (or `raise ToolProviderNotConfigured` if `settings.tool_provider_strict`). If it exists: `_provider.get_tools(agent_name=agent_name, capabilities=required_capabilities)`.
- Imports of `prismal.mcp` and `prismal.skills` are removed from the module.
- `init_mcp()`, `get_mcp_tools()`, `get_skill_tools()` are kept as *shims* that emit `DeprecationWarning` and delegate to providers/host.

#### Y4 — Context injection (variant B, multi-tenant)
- `get_async_compiled_graph(..., tool_provider: ToolProviderPort | None = None)` stores the provider in the compiled config.
- Per-node resolution: a helper `resolve_provider(config)` reads the provider from `RunnableConfig["configurable"]["tool_provider"]` or falls back to the global. No global lock per resolution.
- Activated by `settings.tool_provider_mode = "context"`.

#### Y5 — Host composition (`prismal/agents/extension/providers.py::build_default_tool_provider`)
- `build_default_tool_provider(settings) -> CompositeToolProvider` assembles the standard composite (MCP if configured + Skills + Stubs), respecting `settings`. It is the helper that `prismal-sdk`/`prismal-web` call in their *lifespan*. It lives in the extension namespace (host-facing), not in the pure-core path.

#### Y6 — Settings and observability
- `settings.tool_provider_mode: Literal["global","context"] = "global"`.
- `settings.tool_provider_strict: bool = False`.
- Metrics: `prismal_tool_provider_resolved_total{provider}`, `prismal_tools_injected_total{agent}`, `prismal_tool_provider_fallback_total`.
- Span `prismal.tools.resolve{agent}`.

### 3.4 Detailed Data Flows

#### Flow Y-A: Startup and composition in the host (variant A)
```
1. prismal-web lifespan startup
2. build_default_tool_provider(settings)
     ├─ MCPClientManager(config).load_from_config()  (await)
     ├─ SkillsManager()
     └─ CompositeToolProvider([Mcp, Skill, Stub])
3. set_tool_provider(provider)          # module state in tool_registry
4. get_async_compiled_graph()           # core, without knowing mcp/skills
5. first turn → coder node → get_tools_for_agent("coder")
     └─ _provider.get_tools(agent_name="coder", capabilities=None)
```

#### Flow Y-B: Per-session resolution (variant B)
```
1. user request U
2. provider_U = CompositeToolProvider([Mcp(allowlist_U), Skill(plan_U), Stub])
3. graph = await get_async_compiled_graph(tool_provider=provider_U)
4. coder node → resolve_provider(config) → provider_U  (not global)
5. provider_U.get_tools(agent_name="coder", ...)
   ⮕ user V with provider_V in parallel does not share state
```

#### Flow Y-C: Fallback without provider
```
1. core imported as a library, no host
2. node → get_tools_for_agent("researcher")
3. _provider is None
     ├─ strict=False → StubToolProvider().get_tools(...) + warning
     └─ strict=True  → raise ToolProviderNotConfigured
```

---

## 4. Design Decisions

### DD-TPI-001: Structural port (Protocol), not a base class
Consistent with the `ports.py` of Phase X. A host can inject any object with `get_tools(...)` without inheriting from anything or registering. `MCPClientManager`/`SkillsManager` are not modified: they are wrapped in thin adapters.

### DD-TPI-002: Keep `get_tools_for_agent` as a stable facade
The 20+ nodes call `get_tools_for_agent(name)`. Keeping that signature makes the refactor invisible to the agents and reduces the blast radius to a single file (variant A). Discarded alternative: change the signature of each node (pure variant B) — cleaner but invasive; offered as an opt-in stage 2.

### DD-TPI-003: Providers live in `extension/`, not in the pure core
`providers.py` imports `MCPClientManager`/`SkillsManager` (deferred). For the architecture test "agents does not import mcp/skills" to be true and useful, `prismal/agents/extension/providers.py` is excluded from the rule (it is *host-facing* code, equivalent to the `plugins.py` of Phase X which also orchestrates integrations). The **pure core** (graph, supervisor, nodes, tool_registry) stays clean.

### DD-TPI-004: Token caps are platform policy
`_MAX_MCP_TOOLS` and `_MAX_TOTAL_TOOLS` remain in the official `CompositeToolProvider` (not as a free host parameter) so a consumer cannot break OpenAI's limit of 128 by accident. Configurable but with safe defaults.

### DD-TPI-005: Fallback to stubs by default, strict opt-in
So as not to break consumers that use the core as a library without MCP/skills, the absence of a provider degrades to stubs + warning (parity with `get_mcp_tools()` returning `[]`). `tool_provider_strict=True` turns it into an error for deployments that require a real toolset.

### DD-TPI-006: Deprecated shims, not immediate removal
`init_mcp`/`get_mcp_tools`/`get_skill_tools` still exist (with `DeprecationWarning`) delegating to providers, for 1 minor. It avoids breaking `prismal-sdk`/`prismal-web` and the examples before they migrate.

### DD-TPI-007: Security does not move
The L1–L5 layers live in `react_loop` + the `@prismal_node` *middleware*, downstream of the provider. The provider only **provides** tools; **execution** still passes through the barriers. A malicious provider cannot bypass `ActionInterceptor`/`AuditLogger`.

### DD-TPI-008: `required_capabilities` is preserved end-to-end
The signature `get_tools(*, agent_name, capabilities)` keeps the Phase E filter. `CompositeToolProvider` only applies `capabilities` to the MCP sub-provider (parity: skills and stubs are not filtered).

---

## 5. Code Structure

```
prismal/
├── agents/
│   ├── tool_registry.py            # MODIFIED: delegates to provider; no mcp/skills imports
│   ├── extension/
│   │   ├── ports.py                # MODIFIED: + ToolProviderPort
│   │   ├── providers.py            # NEW: Mcp/Skill/Stub/Composite + build_default_tool_provider
│   │   └── __init__.py             # MODIFIED: re-export ToolProviderPort + providers
│   ├── graph.py                    # MODIFIED (variant B): tool_provider in config
│   └── tools.py                    # UNCHANGED (stubs logically relocated into StubToolProvider)
├── core/
│   ├── config.py                   # MODIFIED: tool_provider_mode, tool_provider_strict
│   └── exceptions.py               # MODIFIED: + ToolProviderNotConfigured
docs/
└── tool-providers.md               # NEW
examples/
├── tool_provider_custom.py         # NEW
└── tool_provider_host.py           # NEW
tests/
└── unit/extension/
    ├── test_tool_provider_port.py  # NEW
    ├── test_providers.py           # NEW
    ├── test_registry_delegation.py # NEW (parity)
    └── test_no_mcp_skills_imports.py # NEW (architecture)
```

### Applied Patterns
- **Hexagonal Ports & Adapters** (same as Phase X).
- **Factory injection** (same as Phase A/B/C: the business logic accepts the provider, lazy defaults).
- **Stable facade** (`get_tools_for_agent`) over an interchangeable implementation.
- **Strategy** (`CompositeToolProvider` encapsulates the merge policy).

### Error Handling
- A sub-provider that raises → caught, logged (`tool_provider.subprovider_error`), and the rest is returned (parity with the current `get_mcp_tools()`).
- No provider → fallback or `ToolProviderNotConfigured` depending on `strict`.
- Tool execution errors are still handled by `react_loop` (failure budget, rate-limit backoff) unchanged.

---

## 6. Security

### 6.1 Attack Surface
- **Malicious injected provider:** could return arbitrary tools. Mitigation: execution passes through `ActionInterceptor.check()` and `AuditLogger`; the provider does not execute, it only lists. The host is responsible for which providers it composes (explicit trust, same as Phase X entry points).
- **Multi-tenant (variant B):** tool leakage between sessions. Mitigation: no global state; per-session provider; isolation test.

### 6.2 Cross-Cutting Rules
- Every tool, wherever it comes from, executes inside `react_loop` with the L1–L5 barriers.
- Token caps in the official composite.
- The audit log records the resolved provider per node (not the contents of the tools).
- External SDK imports remain deferred and isolated (not at the core module level).

---

## 7. Observability

### 7.1 OTel Spans
- `prismal.tools.resolve{agent}` — attributes `provider`, `n_tools`, `fallback`, `capabilities`.

### 7.2 Metrics
```
# Resolution
prismal_tool_provider_resolved_total{provider="composite|mcp|skill|stub|fake"}
prismal_tools_injected_total{agent}
prismal_tool_provider_fallback_total          # number of times it fell back to stubs due to missing provider
prismal_tool_provider_subprovider_errors_total{provider}
```

### 7.3 Startup Report (host)
- `build_default_tool_provider` logs which sub-providers ended up active (connected MCP servers, number of active skills) — parity with the current `tool_registry.mcp_initialized` log, but emitted from the host.

---

## 8. Testing Strategy

- **Unit:** each provider in isolation with *mock* managers.
- **Parity:** `test_registry_delegation.py` compares the output of `get_tools_for_agent` with the default composite against a *golden list* derived from the current implementation (same order, dedupe, caps, fixed agents).
- **Architecture:** `test_no_mcp_skills_imports.py` walks the AST of `prismal/agents/**` (excluding `extension/providers.py`) and fails if `import prismal.mcp` or `import prismal.skills` appears.
- **Isolation (variant B):** two providers in parallel do not share tools.
- **Fallback:** no provider → stubs + warning; `strict=True` → exception.

### Mock Strategy
- `FakeToolProvider(mapping: dict[str, list[BaseTool]])` for agent fixtures — replaces real MCP/skills.

---

## 9. Rollout Plan

### 9.1 Adoption Strategy
1. Merge Y1–Y3 (variant A) — identical behavior if the host calls `build_default_tool_provider` + `set_tool_provider` in the lifespan.
2. `prismal-sdk` / `prismal-web` migrate their startup from `init_mcp()` to `set_tool_provider(build_default_tool_provider(settings))`.
3. Y4 (variant B) is adopted only where multi-tenant is needed.

### 9.2 Backward Compatibility
- Nodes unchanged.
- Deprecated shims keep the old startup working for 1 minor.
- If the host does not migrate, the fallback to stubs avoids crashes (degraded mode visible via warning).

### 9.3 API Stability
- `ToolProviderPort` and the providers are versioned public API (SemVer; breaking → minor + deprecation 1 release).

---

## 10. Open Questions

- **PA-1:** Should `build_default_tool_provider` live in `prismal/agents/extension/providers.py` or be physically moved to `prismal-sdk`? (Proposal: start in `extension/` to avoid blocking; move to `prismal-sdk` once it exists as a package.)
- **PA-2:** Should variant B be the default in the medium term and deprecate the global one? (Depends on the priority of multi-tenant in `prismal-web`.)
- **PA-3:** Is it worth exposing `capabilities` to `SkillToolProvider` too (filtering skills by capability) or keeping strict parity with today? (Proposal: parity now, evaluate later.)

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial technical design — `ToolProviderPort` + injection from host |
