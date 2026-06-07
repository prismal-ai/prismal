# Prismal — Tool Provider Injection (MCP & Skills as an injectable port)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Phase** | Y — Tool Provider Injection (natural successor to Phase X — Extension Surface) |

---

## 1. Executive Summary

Today the tools that agents consume come from three sources (**MCP** servers, active **Skills**, and static *stubs*) and are merged into a single *facade*: `prismal/agents/tool_registry.py`. The problem is the **direction of the dependency**: the agents layer (the orchestration core) builds and directly knows its integration subsystems — it imports `prismal.mcp.client.MCPClientManager` and `prismal.skills.manager.SkillsManager`, maintains the lifecycle in module *singletons* (`_mcp_manager`, `_mcp_initialized`, `_mcp_lock`), and decides the configuration (`init_mcp(config_path)`).

This is inconsistent with the design established in **Phase X (Extension Surface)**, where `CheckpointPort`, `AuditPort`, and `EmbeddingsPort` are already inverted as hexagonal ports: the host composes and injects, the core only consumes. **MCP and Skills are the exception that remains to be normalized.**

This phase defines a **`ToolProviderPort`** and moves the *construction* of MCP/Skills out of the core, toward the component that composes the application (`prismal-sdk`, `prismal-web`). The core stops importing `prismal.mcp` and `prismal.skills`; the host instantiates the providers and injects them via `set_tool_provider()`. The change is **opt-in, additive, and backward-compatible**: the 20+ agent nodes keep calling `get_tools_for_agent("coder")` unchanged, and if no one injects a provider the system degrades to *stubs* with a structured *warning* (just as `get_mcp_tools()` returns `[]` today when MCP was not initialized).

The deliverable enables three capabilities that are impossible today without patching the core: (1) **per-session/per-user toolsets** for multi-tenant `prismal-web`; (2) a **publishable and testable `prismal` core** without MCP servers or skills on disk; (3) **integration lifecycle in the host**, where it belongs.

---

## 2. Context and Problem

### 2.1 Current Situation

- **`tool_registry.py` is the single integration point.** It mixes MCP + Skills + stubs per call (`get_tools_for_agent`), applying priority (MCP → Skills → stubs), name dedupe, and token caps (`_MAX_MCP_TOOLS = 60`, `_MAX_TOTAL_TOOLS = 120`).
- **The core reaches down into its peripherals.** `get_mcp_tools()` imports `prismal.mcp.client.MCPClientManager`; `get_skill_tools()` instantiates `prismal.skills.manager.SkillsManager()`. The agents layer depends on the integration layers.
- **Mutable global module state.** The `_mcp_manager` *singleton* and its flags/lock live inside the core. The caller cannot substitute the *manager* (one with user auth, a *mock* one in tests) without patching the module.
- **Non-injectable construction.** `init_mcp()` instantiates `MCPClientManager(config_path or _DEFAULT_MCP_CONFIG)` internally; the core decides the config choice and the moment of connection.
- **Opaque skill activation.** `SkillsManager().get_active_tools()` reads disk/process state; the host cannot decide which skills each user/session sees.
- **The boundary already half exists.** `grep` confirms that **no module inside `prismal/` calls `init_mcp()`**: it is already assumed that startup is triggered by an external component. The boundary is implicit but not formalized.

### 2.2 Problem

Without a tool provider port:

1. **The core cannot be published cleanly**: it drags along dependencies and imports of MCP and Skills that a framework consumer might not want.
2. **There is no real multi-tenancy**: the global *singleton* prevents two `prismal-web` users from seeing distinct *toolsets* (server allowlist, skills by plan).
3. **Coupled tests**: testing an agent requires either real MCP/skills or patching module globals.
4. **The host does not control the lifecycle**: when to connect, with which credentials, which servers — all of it is buried in the core.

### 2.3 Opportunity

Almost all the primitives already exist:

- **`ToolPort`** (`prismal/agents/extension/ports.py`, Phase X) already models an executable tool (`name`, `description`, `ainvoke`) — the unit that MCP and Skills produce.
- The "**port + external implementation that conforms to the shape**" pattern is already proven with `CheckpointPort`/`AuditPort`/`EmbeddingsPort`.
- **`discover_plugins()`** (Phase X) already establishes the precedent "the host discovers/registers, the core consumes".
- **`DEFAULT_CAPABILITY_MAP`** and the `required_capabilities` parameter (Phase E) already route capabilities per agent; they just need to flow down to the provider.

What is missing is **declaring the provider contract and moving the construction to the host**. Low effort (one new port + refactor of a single file + provider extraction), high impact on publishability, testability, and multi-tenancy, without breaking anything.

---

## 3. Target Users

### Persona 1: Platform Host (prismal-sdk / prismal-web)
- **Description:** The component that composes and starts the application on top of the `prismal` core.
- **Main need:** Build and inject the tool providers (MCP, Skills) with the lifecycle, credentials, and configuration the host decides.
- **Usage frequency:** Once per startup (global variant) or once per session/user (context variant).

### Persona 2: Multi-Tenant Web Operator
- **Description:** Operates `prismal-web` with multiple users/organizations.
- **Main need:** That each session sees its own *toolset* (authorized MCP servers, skills enabled by plan) without shared global state.
- **Usage frequency:** Per request/session.

### Persona 3: Framework Consumer / Library User
- **Description:** Imports `prismal` as a library to build their own agent, without needing MCP or Skills.
- **Main need:** That the core works (degraded to stubs) without forcing them to install/configure MCP or Skills.
- **Usage frequency:** Continuous.

### Persona 4: Core Maintainer / Test Author
- **Description:** Writes tests for the agents core.
- **Main need:** Inject a deterministic `FakeToolProvider` without patching module *singletons* or standing up services.
- **Usage frequency:** Daily.

---

## 4. Objectives and Success Metrics

### 4.1 Business Objectives

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Dependency inversion | Imports of `prismal.mcp` / `prismal.skills` within `prismal/agents/**` | 0 | Phase Y |
| Isolated publishable core | `import prismal.agents.graph` without `prismal.mcp`/`prismal.skills` installed | OK (degrades to stubs) | Phase Y |
| Multi-tenant | Distinct provider per session without global state | Supported (variant B) | Phase Y stage 2 |
| Backward compatibility | Existing tests passing without changes | 100% | Global |
| Testability | Agent tests requiring real MCP/skills | 0 (via `FakeToolProvider`) | Phase Y |
| Test coverage | Branch coverage of new modules | ≥ 85% | Global |

### 4.2 User Objectives

| User Objective | Indicator |
|---|---|
| Inject providers from the host in 1 call | `set_tool_provider(CompositeToolProvider([...]))` |
| Per-session/per-user toolset | Provider resolvable from the graph config (variant B) |
| Core without MCP/Skills works | Fallback to `StubToolProvider` + warning, no exception |
| Replace the merge with a custom one | Implement `ToolProviderPort` and conform to the shape |
| Deterministic tests | `FakeToolProvider` replaces the real wiring |

---

## 5. Scope

### 5.1 In Scope (Included — Phase Y)

**Y1 — `ToolProviderPort` (`prismal/agents/extension/ports.py`):**
- [x] `Protocol` `ToolProviderPort` with `get_tools(*, agent_name, capabilities) -> list[ToolPort]`.
- [x] Re-export from `prismal/agents/extension/__init__.py`.
- [x] Conformance helper reusing `conforms_to(obj, port)`.

**Y2 — Concrete providers (`prismal/agents/extension/providers.py`):**
- [x] `McpToolProvider` — wraps `MCPClientManager`; moves the logic of `get_mcp_tools()` + cap `_MAX_MCP_TOOLS`.
- [x] `SkillToolProvider` — wraps `SkillsManager.get_active_tools()`.
- [x] `StubToolProvider` — per-agent *fallbacks* from `tools.py` (the core default).
- [x] `CompositeToolProvider` — merges N providers with priority + dedupe + cap `_MAX_TOTAL_TOOLS` + respect for `_FIXED_TOOL_AGENTS`.

**Y3 — Global injection (variant A — backward-compatible):**
- [x] `tool_registry.set_tool_provider(p: ToolProviderPort)` and `get_tool_provider()`.
- [x] `get_tools_for_agent()` delegates to the injected provider; if there is none, uses `StubToolProvider` + warning.
- [x] `tool_registry` stops importing `prismal.mcp` and `prismal.skills`.
- [x] `init_mcp()` / `get_mcp_tools()` / `get_skill_tools()` are kept as deprecated *shims* that delegate to providers (1 deprecation release).

**Y4 — Context injection (variant B — multi-tenant, stage 2):**
- [x] The provider can be passed in the graph config (`get_async_compiled_graph(..., tool_provider=...)`) and resolved per session.
- [x] Per-node resolution from `RunnableConfig` without globals (`resolve_provider` + `get_tools_for_agent_ctx`).

**Y5 — Composition in the host:**
- [x] Function `build_default_tool_provider(settings)` that assembles the standard `CompositeToolProvider` (MCP + Skills + stubs).
- [x] Documented so that `prismal-sdk` / `prismal-web` invoke it in their *lifespan* (`docs/tool-providers.md` §1).

**Y6 — Settings and observability:**
- [x] `settings.tool_provider_mode: Literal["global","context"] = "global"`.
- [x] `settings.tool_provider_strict: bool = False` (if `True`, absence of a provider is an error instead of a silent fallback).
- [x] Metrics: `prismal_tool_provider_resolved_total{provider}`, `prismal_tools_injected_total{agent}`, `prismal_tool_provider_fallback_total` (+ `subprovider_errors_total`).
- [x] OTel spans: `prismal.tools.resolve{agent}`.

**Y7 — Documentation and examples:**
- [x] `docs/tool-providers.md` — composition quickstart + recipes (per-user allowlist, mock in tests).
- [x] `examples/tool_provider_custom.py` — custom provider.
- [x] `examples/tool_provider_host.py` — host-style composition (MCP + Skills + stubs) + injection.

**Y8 — Tests:**
- [x] `FakeToolProvider` for fixtures.
- [x] Parity tests: the output of `get_tools_for_agent` before/after the refactor is identical with the default provider.

### 5.2 Out of Scope (Excluded)

- **Rewriting `MCPClientManager` or `SkillsManager`** — they are only wrapped; their internal API does not change.
- **Full DI container** — the "inject provider + `settings: Settings | None = None`" pattern is enough (consistent with DD-EXT-005).
- **Changing the signature of the 20+ agent nodes in variant A** — the nodes keep calling `get_tools_for_agent(name)`.
- **Distributed persistence/cache of per-user toolsets** — the host's responsibility.
- **Hot reload of providers** — restarting the process (or a new session in variant B) is acceptable.
- **Moving `_MAX_MCP_TOOLS`/`_MAX_TOTAL_TOOLS` out of the platform policy** — the caps remain in the official `CompositeToolProvider` so a host cannot break them by accident.

### 5.3 Future Considerations (Phase Y+)

- Providers with per-session TTL cache.
- Aggregated telemetry per MCP server / skill (latency, errors, per-user usage).
- Per-tenant quotas (number of tools, number of calls).
- Dynamic capability negotiation (the agent declares, the provider resolves the minimal set).
- Remote provider (gRPC/HTTP) for toolsets served by an external service.

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-TPI-001 | `ToolProviderPort` declares `get_tools(*, agent_name, capabilities)` as a `Protocol` | `MUST` |
| RF-TPI-002 | `McpToolProvider` wraps `MCPClientManager` and applies the `_MAX_MCP_TOOLS` cap | `MUST` |
| RF-TPI-003 | `SkillToolProvider` wraps `SkillsManager.get_active_tools()` | `MUST` |
| RF-TPI-004 | `StubToolProvider` provides the `tools.py` fallbacks per agent | `MUST` |
| RF-TPI-005 | `CompositeToolProvider` merges with MCP→Skills→stubs priority, dedupe, and total cap | `MUST` |
| RF-TPI-006 | `set_tool_provider()` / `get_tool_provider()` inject/read the provider (variant A) | `MUST` |
| RF-TPI-007 | `get_tools_for_agent()` delegates to the provider; falls back to stubs if there is no provider | `MUST` |
| RF-TPI-008 | `prismal/agents/**` does not import `prismal.mcp` or `prismal.skills` | `MUST` |
| RF-TPI-009 | `_FIXED_TOOL_AGENTS` (cron_manager, critic) keep receiving stubs only | `MUST` |
| RF-TPI-010 | `required_capabilities` flows down to `provider.get_tools(capabilities=...)` | `MUST` |
| RF-TPI-011 | Deprecated shims `init_mcp/get_mcp_tools/get_skill_tools` delegate to providers | `SHOULD` |
| RF-TPI-012 | Variant B: provider resolvable per session via the graph config | `SHOULD` |
| RF-TPI-013 | `build_default_tool_provider(settings)` assembles the standard composite for the host | `MUST` |
| RF-TPI-014 | Settings `tool_provider_mode` / `tool_provider_strict` | `SHOULD` |
| RF-TPI-015 | Metrics and spans for tool resolution | `SHOULD` |
| RF-TPI-016 | Runnable examples: custom provider + host composition | `MUST` |

---

## 7. Non-Functional Requirements

### Performance
- Per-node tool resolution (`get_tools`) ≤ 5 ms of overhead over the current merge.
- Variant B must not add > 1 ms per node when reading the provider from the config.
- Host composition (`build_default_tool_provider`) runs once per startup (variant A) — no per-request impact.

### Security
- The injected provider **cannot bypass** the L1–L5 layers: execution still passes through `react_loop` + the `@prismal_node` *middleware* (`SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`).
- The token caps (`_MAX_MCP_TOOLS`, `_MAX_TOTAL_TOOLS`) remain in the official composite; a host must not be able to exceed them.
- In multi-tenant (variant B), one user's provider must not expose another user's tools (per-session isolation, no global state).

### Availability
- Absence of a provider (non-strict mode) degrades to stubs with a warning — never an exception.
- Failure of a sub-provider (e.g. MCP down) in the composite does not prevent returning the rest (skills + stubs), just as `get_mcp_tools()` returns `[]` on error today.

### Scalability
- Support N providers in the composite without appreciable degradation.
- Variant B: support thousands of concurrent sessions with independent providers (no global lock per resolution).

### Observability
- OTel span `prismal.tools.resolve{agent}` with `provider`, `n_tools`, `fallback`.
- Metrics listed in Y6.
- Structured log per resolution: `agent`, `provider`, `live`, `stubs_kept`, `total` (parity with the current `tool_registry.tools_resolved` log).

### Maintainability
- Coverage ≥ 85% in new modules.
- `ruff` + `mypy --strict` + `bandit` clean.
- Deferred imports: the core must not import MCP/Skills at module import time (respects `filterwarnings=error`).

### Compatibility
- `prismal/` remains a PEP 420 namespace package (do not add `__init__.py`).
- Public API (`ToolProviderPort`, providers) versioned; breaking requires a minor bump + 1-release deprecation.

---

## 8. Constraints and Dependencies

### Technical Constraints
- Python 3.13+, `uv`.
- Do not add new mandatory dependencies to the core.
- Providers that touch external SDKs (MCP, skills) must keep imports **deferred** (inside methods), not at the core module level.

### External Dependencies

| Dependency | Type | Use | Status |
|---|---|---|---|
| `prismal/agents/extension/ports.py` | Existing (Phase X) | Base for `ToolProviderPort` (extends `ToolPort`) | ✅ Present |
| `prismal.mcp.client.MCPClientManager` | Existing | Wrapped by `McpToolProvider` | ✅ Present |
| `prismal.skills.manager.SkillsManager` | Existing | Wrapped by `SkillToolProvider` | ✅ Present |
| `langchain_core.tools.BaseTool` | Existing | Conforms to `ToolPort` | ✅ Present |
| `opentelemetry-api` / `structlog` | Existing | Resolution spans + logs | ✅ Present |

**No new dependencies** — all on the already-installed stack.

---

## 9. User Stories

### Epic Y: Inject the toolset from the host

**US-TPI-001:** As a Platform Host, I want to compose and inject the tool providers at startup without the core knowing about MCP or Skills.
```python
# in prismal-sdk / prismal-web (NOT in prismal/agents)
from prismal.agents.extension.providers import (
    McpToolProvider, SkillToolProvider, StubToolProvider, CompositeToolProvider,
)
from prismal.agents.tool_registry import set_tool_provider
from prismal.mcp.client import MCPClientManager
from prismal.skills.manager import SkillsManager

provider = CompositeToolProvider([
    McpToolProvider(MCPClientManager("config/mcp_servers.yaml")),
    SkillToolProvider(SkillsManager()),
    StubToolProvider(),
])
set_tool_provider(provider)
```
- [ ] The nodes keep calling `get_tools_for_agent("coder")` unchanged.
- [ ] `prismal/agents/**` does not import `prismal.mcp` or `prismal.skills`.

### Epic Y: Core without MCP/Skills

**US-TPI-002:** As a Framework Consumer, I want to use the agents core without installing MCP or Skills.
- [ ] With no provider injected, `get_tools_for_agent` returns stubs + emits a warning.
- [ ] No import exception from MCP/Skills being absent.

### Epic Y: Multi-tenant per session

**US-TPI-003:** As a Multi-Tenant Web Operator, I want each user to see their own toolset.
```python
provider = CompositeToolProvider([
    McpToolProvider(mgr_for_user(user)),   # allowlist of the user's servers
    SkillToolProvider(skills_for_plan(user.plan)),
    StubToolProvider(),
])
graph = await get_async_compiled_graph(tool_provider=provider)  # variant B
```
- [ ] Two concurrent users see distinct toolsets with no global state.

### Epic Y: Deterministic tests

**US-TPI-004:** As a Core Maintainer, I want to inject a fake provider in tests.
```python
set_tool_provider(FakeToolProvider({"researcher": [echo_tool]}))
```
- [ ] The agent test does not require real MCP or skills.

---

## 10. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Silent regression if no one injects a provider | Medium | High | Fallback to `StubToolProvider` + structured warning; `tool_provider_strict=True` for environments that require a provider |
| Parity change in the merge (order/dedupe/caps) | Medium | High | Byte-for-byte parity tests of `get_tools_for_agent` before/after; the caps stay in the official composite |
| Startup order: a node runs before injection | Low | High | Document injection in the host's lifespan; in variant B resolution is lazy per node |
| Accidental import of MCP/Skills at module level breaks `filterwarnings=error` | Medium | Medium | Deferred imports; architecture test that forbids `prismal.mcp`/`prismal.skills` in `prismal/agents/**` |
| Multi-tenant: tool leakage between sessions | Low | Critical | Variant B without global state; per-session provider; isolation test |
| Deprecated shims used indefinitely | Medium | Low | `DeprecationWarning` + removal announced for 1 minor |
| Over-engineering of the port | Low | Medium | Keep `get_tools` as the only method; no DI container (DD-EXT-005) |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| Y1 — `ToolProviderPort` | 0.2 week | Protocol + re-export |
| Y2 — Concrete providers | 1 week | McpToolProvider, SkillToolProvider, StubToolProvider, CompositeToolProvider + tests |
| Y3 — Global injection (variant A) | 0.8 week | `set_tool_provider`, `tool_registry` refactor, deprecated shims |
| Y4 — Context injection (variant B) | 1 week | Per-session resolution via graph config + isolation tests |
| Y5 — Host composition | 0.3 week | `build_default_tool_provider(settings)` |
| Y6 — Settings + metrics | 0.3 week | Toggles + counters + spans |
| Y7 — Docs + examples | 0.6 week | `docs/tool-providers.md` + 2 examples |
| Y8 — Tests + parity | 0.5 week | `FakeToolProvider` + parity tests |
| Hardening | 0.5 week | Coverage ≥ 85%, architecture test, security audit |
| **Total** | **~5 weeks** | MCP/Skills injectable from the host + optional multi-tenant |

---

## 12. Definition of Done (Global for Phase Y)

- [x] `ToolProviderPort` declared and re-exported.
- [x] `McpToolProvider`, `SkillToolProvider`, `StubToolProvider`, `CompositeToolProvider` implemented and tested.
- [x] `set_tool_provider()` / `get_tool_provider()` working; `get_tools_for_agent()` delegates.
- [x] `prismal/agents/**` without imports of `prismal.mcp` / `prismal.skills` (verified by architecture test; documented exemptions: `extension/providers.py`, `skill_manager.py`).
- [x] Parity: identical output of `get_tools_for_agent` with the default provider (test).
- [x] `_FIXED_TOOL_AGENTS` and token caps preserved.
- [x] Optional variant B available and with a per-session isolation test.
- [x] `build_default_tool_provider(settings)` documented for the host.
- [x] Shims `init_mcp/get_mcp_tools/get_skill_tools` deprecated with `DeprecationWarning`.
- [x] `docs/tool-providers.md` + 2 runnable examples.
- [x] Coverage ≥ 85% in new modules (providers 100%, tool_registry 85%).
- [x] Green suite within the phase scope (2604 passed; ~50 pre-existing failures unrelated to Phase Y, identical at baseline).
- [x] `ruff` + `mypy --strict` + `bandit` clean (in `prismal/` and `tests/`; 20 pre-existing ruff issues in `examples/` multimodal/rag/subgraphs remain out of scope).
- [x] `CLAUDE.md` and `README.md` updated.
- [ ] PR merged to `main` with code review approved.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial version — injection of MCP/Skills via `ToolProviderPort` from an external layer |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
| DX Lead | — | | ☐ Pending |
