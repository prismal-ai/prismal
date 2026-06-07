# Prismal Extension Surface — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **Related PLAN** | `specs/extension-surface/PLAN.md` |
| **Related SPEC** | `specs/extension-surface/SPEC.md` |
| **TASKS** | `specs/extension-surface/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |

---

## 1. Context

Today, prismal offers implicit extensibility (callable injection in Phase B, `SubgraphRegistry` in Phase C), but without a public contract or plugin discovery. This document describes the implementation of **Phase X — Extension Surface**, which turns that extensibility into a deliberate API with five components: LangGraph re-export, `@prismal_node` decorator, fluent builder, plugin discovery via entry points, and a LangChain adapter.

Guiding principle: **prismal is LangGraph with batteries included, not LangGraph hidden**. The extension surface must preserve the readability of user code in terms of standard LangGraph — everything prismal adds is opt-in and observable.

---

## 2. Technical Objectives

- **Zero-friction compatibility with upstream LangGraph:** a user who knows LangGraph should be able to build a node in ≤ 15 minutes without reading all of prismal's code.
- **Automated cross-cutting:** security, OTel, audit, logging are applied via decorator/builder without the user asking for them.
- **Declarative plugin auto-discovery:** standard Python `entry_points`; zero magic.
- **Failure isolation:** a broken plugin must not break startup or the main graph.
- **Frozen API:** the extension surface is versioned with SemVer; a deprecation cycle is mandatory.
- **No new mandatory dependencies:** everything is built on the already-installed stack.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram — New Modules

```
prismal/
├── langgraph.py                       ← [NEW] official re-export
│
├── agents/
│   └── extension/                     ← [NEW public subdirectory]
│       ├── __init__.py                ← re-exports: prismal_node, builder, plugins, adapters, ports
│       ├── decorators.py              ← @prismal_node + helpers
│       ├── builder.py                 ← PrismalStateGraphBuilder
│       ├── plugins.py                 ← discover_plugins() + PluginRegistry
│       ├── adapters.py                ← LangChainRunnableAdapter
│       ├── ports.py                   ← CheckpointPort, AuditPort, EmbeddingsPort, ToolPort
│       └── _middleware.py             ← internal middleware chain (not public)
│
└── core/
    └── [EXTENSION] config.py          ← plugins_autodiscover, plugins_allowlist, plugins_denylist

examples/
├── custom_node.py                     ← [NEW] hello world: @prismal_node
├── custom_subgraph.py                 ← [NEW] PrismalStateGraphBuilder
├── langchain_migration.py             ← [NEW] AgentExecutor → node
└── plugin_template/                   ← [NEW] cookiecutter skeleton
    ├── pyproject.toml
    ├── src/prismal_x_<name>/
    │   ├── __init__.py
    │   └── plugin.py                  ← register_<name>(registry)
    └── README.md

docs/
└── extension.md                        ← [NEW] quickstart + cookbook
```

### 3.2 Layer Diagram

```
                       ┌──────────────────────────────────────┐
                       │       USER / PLUGIN CODE             │
                       │  ┌─────────────────────────────────┐ │
                       │  │ @prismal_node                   │ │
                       │  │ PrismalStateGraphBuilder        │ │
                       │  │ LangChainRunnableAdapter        │ │
                       │  │ register_<name>(registry)       │ │
                       │  └─────────────────────────────────┘ │
                       └──────────────┬───────────────────────┘
                                      │
                                      ▼
                       ┌──────────────────────────────────────┐
                       │   prismal.agents.extension (public)  │
                       │  decorators • builder • plugins      │
                       │  adapters • ports                    │
                       └──────┬───────────────────────────────┘
                              │ applies middleware chain
                              ▼
                       ┌──────────────────────────────────────┐
                       │  _middleware.py (internal)          │
                       │  [security → otel → audit →          │
                       │   retry → execute → format_output]   │
                       └──────┬───────────────────────────────┘
                              │ uses
   ┌──────────────────────────┼──────────────────────────┐
   ▼                          ▼                          ▼
┌──────────────┐    ┌──────────────────┐      ┌────────────────────┐
│ security/    │    │ monitoring/      │      │ providers/         │
│ SecurePrompt │    │ OTelManager      │      │ ProviderRegistry   │
│ ActionInter  │    │ get_logger()     │      │                    │
│ AuditLogger  │    │                  │      │                    │
└──────────────┘    └──────────────────┘      └────────────────────┘
                              │
                              ▼
                       ┌──────────────────────────────────────┐
                       │  prismal.langgraph (re-export)       │
                       │  StateGraph, Send, interrupt,        │
                       │  add_messages, START, END, ...       │
                       └──────────────┬───────────────────────┘
                                      │
                                      ▼
                              langgraph upstream
```

### 3.3 Components by Module

#### X1 — Official re-export

| Symbol | Origin | Reason |
|---|---|---|
| `StateGraph` | `langgraph.graph.StateGraph` | graph construction |
| `START`, `END` | `langgraph.graph` | sentinel nodes |
| `Send` | `langgraph.constants` | fan-out |
| `interrupt` | `langgraph.types` | HITL |
| `add_messages` | `langgraph.graph.message` | message reducer |
| `CompiledStateGraph` | `langgraph.graph.state` | compiled graph type |
| `VERSION` | `importlib.metadata.version("langgraph")` | version traceability |

In addition, the prismal-specific types that are inseparable from usage are re-exported: `AgentState`, `SubgraphDefinition`, `SubgraphRegistry`.

#### X2 — `@prismal_node` Decorator

```
@prismal_node(name=..., capabilities=..., security=..., audit=..., retry=..., timeout_s=...)
async def my_node(state: AgentState) -> dict:
    ...
```

Internally it applies the **middleware chain** (in `_middleware.py`):

```
1. SECURITY    — optional: if security="standard", applies InputSanitizer and SecurePromptBuilder
                  to state["messages"][-1].content before passing to the user.
                  if security="strict", it also calls ActionInterceptor.check() before
                  any write_files / execute_code emitted in the state_update.
2. OTEL        — opens span "prismal.ext.node.<name>" with attributes session_id, node_name,
                  state_keys; closes at the end with status=OK|ERROR.
3. LOGGER      — contextual bind with node_name, session_id, capabilities.
4. RETRY       — if retry={"max_attempts": N, "backoff_s": [0.1, 0.5, 1.0]}, retries
                  with exponential backoff on transient exceptions.
5. TIMEOUT     — wrap with asyncio.wait_for(timeout_s).
6. EXECUTE     — invokes the user's function.
7. AUDIT       — if audit=True, AuditLogger.log_node(name, session_id, status,
                  hash(state_update), duration_ms).
8. ERROR MAP   — captures non-PrismalError exceptions and maps them to NodeExecutionError,
                  returning a state_update with metadata["error"]={...} instead of raising.
9. FORMAT      — validates that the state_update is a dict and returns.
```

The decorator also performs **side registration**:

- Adds `name` to `DEFAULT_CAPABILITY_MAP` in `tool_registry.py` (via the new public API `register_node_capabilities()`).
- If `capabilities` is defined, declares which MCP tools it should receive.
- Maintains an internal registry `_REGISTERED_NODES: dict[str, NodeMetadata]` for introspection (`list_registered_nodes()`).

#### X3 — Fluent builder

`PrismalStateGraphBuilder` wraps `StateGraph[AgentState]` and exposes:

```
builder = PrismalStateGraphBuilder(name="my_pipeline", settings=...)
builder.add_node(name, fn, *, capabilities=..., security=..., audit=...)
builder.add_supervisor_node(routing_fn, *, valid_next=...)
builder.add_security_layer(at="entry"|"exit")
builder.add_edge(from_, to)
builder.add_conditional_edges(from_, decision_fn, mapping)
builder.set_entry_point(name)
builder.compile() -> SubgraphDefinition
builder.compile_raw() -> CompiledStateGraph     # escape hatch without SubgraphDefinition
```

`add_node()` detects whether the callable already has `@prismal_node` applied (via the `__prismal_node__: NodeMetadata` attribute); if not, it applies it with the defaults passed to the builder. This means a user can pass plain functions and receive all the cross-cutting concerns without knowing it.

#### X4 — Plugin discovery

```
[project.entry-points."prismal.subgraphs"]
my_pipeline = "prismal_x_mypkg:register_my_pipeline"

[project.entry-points."prismal.nodes"]
my_node = "prismal_x_mypkg.nodes:my_node"

[project.entry-points."prismal.tools"]
my_tool = "prismal_x_mypkg.tools:my_tool"

[project.entry-points."prismal.rag_engines"]
my_engine = "prismal_x_mypkg.rag:MyRAGEngine"
```

`discover_plugins(settings)` iterates over the four groups, applies allowlist/denylist, and for each entry point:

- If the group is `subgraphs` or `nodes`: imports the callable and invokes it as `register(registry)` or adds it to the node registry.
- If the group is `tools`: adds to the `tool_registry`, respecting the cap of 120.
- If the group is `rag_engines`: instantiates and registers in a new `RAGEngineRegistry` (also introduced in X4).

Each load is isolated in `try/except` with log + metric. An individual failure does not abort the rest.

Optional CLI helper:
```
python -m prismal.plugins list                 # list all discovered plugins
python -m prismal.plugins info <name>          # details (version, hash, entry points)
python -m prismal.plugins doctor               # diagnosis of load errors
```

#### X5 — `LangChainRunnableAdapter`

```python
adapter = LangChainRunnableAdapter(runnable)
adapter.as_node(name="legacy", capabilities=[...])  # returns @prismal_node-decorated callable
```

Internally:
- Inspects the `Runnable` to detect whether it expects a `dict` with keys (`input`, `chat_history`) or `BaseMessage[]`.
- Maps `state["messages"]` → the Runnable's input.
- Maps output → `{"messages": [AIMessage(content=output)]}` or respects `state_update` if the Runnable already returns it in prismal format.
- Supports `AgentExecutor`, `RunnableSequence`, `RunnableLambda`, `RunnableParallel`.

#### X6 — Formalized ports

```python
class CheckpointPort(Protocol):
    async def aget(self, config: dict) -> Checkpoint | None: ...
    async def aput(self, config: dict, checkpoint: Checkpoint, metadata: dict) -> None: ...

class AuditPort(Protocol):
    def log_event(self, event_type: str, payload: dict) -> None: ...

class EmbeddingsPort(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...

class ToolPort(Protocol):
    name: str
    description: str
    async def ainvoke(self, args: dict) -> Any: ...
```

The existing implementations (`AsyncSqliteSaver`, `AuditLogger`, ChromaDB embeddings, LangChain `BaseTool` tools) already conform to these protocols structurally. The change is to declare them explicitly so that users can substitute their own adapters.

### 3.4 Detailed Data Flows

#### Flow X-A: Node invocation with `@prismal_node`

```
LangGraph dispatcher ─▶ wrapper(state)
                     ─▶ [security_mw if enabled]
                          └─ sanitize last message
                     ─▶ [otel_mw] open span "prismal.ext.node.<name>"
                     ─▶ [logger_mw] bind {node_name, session_id}
                     ─▶ [retry_mw] try N times
                          └─ [timeout_mw] asyncio.wait_for
                              └─ user_fn(state) ─▶ state_update
                     ─▶ [audit_mw if enabled] log hash(state_update), duration_ms
                     ─▶ [error_map_mw] if exception → {"metadata":{"error":{...}}}
                     ─▶ return state_update
                     ─▶ [otel_mw] close span with status
```

#### Flow X-B: Plugin discovery at startup

```
app startup ─▶ get_settings() ─▶ if not plugins_autodiscover: skip
            ─▶ discover_plugins(settings)
                  ├─ entry_points(group="prismal.subgraphs")
                  ├─ filter by allowlist / denylist
                  └─ for each ep:
                        try:
                            fn = ep.load()
                            fn(SubgraphRegistry())     # plugin registers itself
                            metric: plugins_loaded_total{status="success"} ++
                            audit: log_event("plugin_loaded", {name, version, ep})
                        except Exception as e:
                            metric: plugins_loaded_total{status="error"} ++
                            log.error("plugin_load_failed", name=..., error=str(e))
                            continue
            ─▶ same for groups: nodes, tools, rag_engines
            ─▶ return DiscoveryReport(loaded=[...], failed=[...])
```

#### Flow X-C: LangChain adapter at runtime

```
state ─▶ adapter.as_node returns wrapped_fn
       ─▶ wrapped_fn(state)
              ─▶ extract messages: state["messages"]
              ─▶ build runnable input: detect signature (BaseMessage[] vs dict)
              ─▶ apply SecurePromptBuilder to content if security="standard"
              ─▶ runnable.ainvoke(input)
              ─▶ map output:
                    if AIMessage → {"messages": [output]}
                    if str       → {"messages": [AIMessage(content=output)]}
                    if dict with "messages" → respect
              ─▶ return state_update
```

---

## 4. Design Decisions

### DD-EXT-001: Decorator over Base Class

- **Decision:** Cross-cutting concerns are applied via the `@prismal_node` decorator, not via a `BaseNode` base class that the user inherits from.
- **Context:** Idiomatic Python favors composition over inheritance; LangGraph nodes are simply `async (state) → dict` callables, not objects.
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **Decorator (chosen)** | Idiomatic Python; allows simple functions; opt-in per-parameter | Hidden magic if the wrapper is not read |
| Base class `BaseNode` | Explicit OOP; documented methods | Forces a style; less flexible for pure functions |
| Explicit middleware list | Maximum transparency; no magic | Verbose; lots of boilerplate in each node |

- **Justification:** The decorator is the canonical Python pattern for cross-cutting (FastAPI, Click, Flask) and fits the callable-injection convention already established in Phase B.

### DD-EXT-002: Plugin Discovery via Entry Points (no scan)

- **Decision:** Plugins are discovered via `importlib.metadata.entry_points()`, not by directory scanning or naming conventions.
- **Context:** Entry points are the Python standard (PEP 621); they work with any installer (pip, uv, poetry).
- **Alternatives evaluated:**

| Option | Pros | Cons |
|---|---|---|
| **Entry points (chosen)** | Standard; no scanning; declarative | Plugin must be installed (does not work with loose code) |
| Scanned `plugins/` directory | Works with non-installed code | Non-standard; ambiguous with dev installs; security |
| Manual YAML config | Operator has 100% control | Bureaucratic; does not leverage PyPI |

- **Justification:** Entry points are the only option that scales to a PyPI ecosystem; the cost (plugin must be installed) is acceptable and desirable (audit trail).

### DD-EXT-003: Builder returns `SubgraphDefinition`, not `CompiledStateGraph` by default

- **Decision:** `builder.compile()` returns a `SubgraphDefinition` (registrable in `SubgraphRegistry`). There is a `compile_raw()` escape hatch that returns a `CompiledStateGraph` directly.
- **Context:** The 95% usage path is to build a subgraph to register; only advanced cases want the raw `CompiledStateGraph` (testing, embedding in other systems).
- **Consequences:** The builder integrates naturally with the `register_<name>(registry)` convention. The escape hatch avoids lock-in.

### DD-EXT-004: Plugin failure isolation

- **Decision:** Each plugin is loaded in an independent `try/except`. An individual failure produces a log + metric but does not prevent the rest.
- **Context:** A plugin ecosystem will necessarily have heterogeneous quality; failing startup because of one broken plugin is unacceptable.
- **Consequences:** `DiscoveryReport` aggregates `loaded` and `failed`; the `prismal.plugins doctor` CLI helps diagnose.

### DD-EXT-005: No full DI container

- **Decision:** The current pattern `settings: Settings | None = None` and resolution via `get_settings()` is kept. No `dependency-injector` or equivalent is introduced.
- **Context:** At the repo's current scale, a DI container adds complexity without a clear benefit. The formalized ports (X6) cover the "substitute implementation" case.
- **Consequences:** If in the future the dependency graph grows (>50 services), it is reevaluated.

### DD-EXT-006: Allowlist/Denylist as settings (not in code)

- **Decision:** The toggles `plugins_autodiscover`, `plugins_allowlist`, `plugins_denylist` live in `core/config.py` (Pydantic Settings) and are configurable via env vars.
- **Context:** Different deployments (dev, staging, prod, sandboxed) need a different posture; hardcoding violates configurability.
- **Consequences:** Variables `PRISMAL_PLUGINS_AUTODISCOVER`, `PRISMAL_PLUGINS_ALLOWLIST`, etc.

### DD-EXT-007: `prismal.langgraph` re-exports, does not wrap

- **Decision:** The `prismal.langgraph` module re-exports the symbols verbatim from `langgraph.*`; it does not wrap or modify them.
- **Context:** Wrapping creates drift; the user must be able to copy code from LangGraph docs without translating it.
- **Consequences:** `from prismal.langgraph import StateGraph` is 100% equivalent to `from langgraph.graph import StateGraph`. Prismal's addition is `AgentState` (which the user wants anyway) and `VERSION` (traceability).

### DD-EXT-008: LangChain adapter as an optional module (no extra)

- **Decision:** `LangChainRunnableAdapter` lives in `agents/extension/adapters.py` without requiring an additional extra — `langchain-core` is already a core dep of prismal.
- **Context:** Almost all prismal users come from the LangChain ecosystem; asking them for an additional extra (`[langchain-bridge]`) is unnecessary friction.
- **Consequences:** The adapter is lazy-imported if `Runnable` is not used; no overhead for those who don't need it.

---

## 5. Code Structure

```
prismal/
│
├── langgraph.py                         ← re-exports + VERSION
│
├── agents/
│   └── extension/
│       ├── __init__.py
│       ├── decorators.py                ← @prismal_node, NodeMetadata
│       ├── builder.py                   ← PrismalStateGraphBuilder
│       ├── plugins.py                   ← discover_plugins, DiscoveryReport, PluginRegistry
│       ├── adapters.py                  ← LangChainRunnableAdapter
│       ├── ports.py                     ← CheckpointPort, AuditPort, EmbeddingsPort, ToolPort
│       ├── _middleware.py               ← (internal) security_mw, otel_mw, retry_mw, etc.
│       └── _registry.py                 ← (internal) _REGISTERED_NODES
│
├── plugins.py                           ← CLI entry: python -m prismal.plugins (list|info|doctor)
│
tests/
├── unit/
│   ├── test_langgraph_reexport.py
│   ├── agents/extension/
│   │   ├── test_decorators.py
│   │   ├── test_builder.py
│   │   ├── test_plugins.py
│   │   ├── test_adapters_langchain.py
│   │   └── test_ports.py
│   └── test_plugins_cli.py
└── integration/
    ├── test_custom_node_e2e.py
    ├── test_custom_subgraph_e2e.py
    ├── test_plugin_discovery_e2e.py     ← installs an in-memory test plugin
    └── test_langchain_adapter_e2e.py    ← real AgentExecutor with mocked LLM

examples/
├── custom_node.py
├── custom_subgraph.py
├── langchain_migration.py
└── plugin_template/
    ├── pyproject.toml
    ├── src/prismal_x_hello/
    │   ├── __init__.py
    │   └── plugin.py
    └── README.md

docs/
└── extension.md
```

### Patterns Applied

| Pattern | Where | Why |
|---|---|---|
| **Decorator** | `@prismal_node` | Cross-cutting without inheritance; idiomatic Python |
| **Fluent Builder** | `PrismalStateGraphBuilder` | Declarative API over `StateGraph` |
| **Plugin / Registry** | `discover_plugins` + entry points | Extensible ecosystem without touching core |
| **Adapter** | `LangChainRunnableAdapter` | Bridge between two contracts (`Runnable` ↔ async node) |
| **Ports & Adapters (Hexagonal)** | `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` | Substitution of implementations |
| **Middleware Chain** | `_middleware.py` | Ordered composition of cross-cutting |
| **Strategy** | `security="standard"\|"strict"\|"off"` in decorator | Configurable behavior |
| **Composite** | Subgraph as node | Native LangGraph; prismal documents and provides a helper |
| **Template Method** | (implicit in `_middleware.py`) | Fixed pipeline with `execute` hook provided by the user |
| **Open/Closed** | Plugin system | Extensible without modifying the core |

### Error Handling

```python
class ExtensionError(PrismalError): ...
class NodeExecutionError(ExtensionError): ...
class NodeTimeoutError(NodeExecutionError): ...
class NodeValidationError(NodeExecutionError): ...
class PluginLoadError(ExtensionError):
    plugin_name: str
    entry_point: str
    cause: BaseException
class PluginConflictError(ExtensionError): ...     # duplicate name
class AdapterError(ExtensionError): ...
class LangChainAdapterError(AdapterError): ...
```

Policy: the decorator captures user exceptions and **maps** them to `NodeExecutionError`, returning a `state_update` with `metadata["error"]={...}` by default. The user can opt in to `@prismal_node(raise_on_error=True)` to propagate.

---

## 6. Security

### 6.1 Attack Surface

| Vector | Mitigation |
|---|---|
| Malicious plugin registers backdoor nodes | Allowlist/denylist via settings; audit log of each load; entry points are the operator's explicit trust (signed by PyPI publisher) |
| Custom node bypasses `SecurePromptBuilder` | `@prismal_node(security="standard")` applies it automatically; opt-out is explicit |
| LangChain adapter executes unauthorized tools | `ActionInterceptor.check()` applied before `Runnable.ainvoke()` if `security="strict"` |
| Plugin loads code at import time | Isolated try/except; failure does not affect the rest; warning if import time > 100 ms |
| Name conflict between plugins | Registry detects and raises `PluginConflictError` with a clear message |
| Entry point points to a nonexistent callable | `ep.load()` fails, captured, structured log |
| Audit log grows with each plugin load | One entry per load (not per node); rotation inherited from the `AuditLogger` |

### 6.2 Cross-cutting Rules

1. **Plugins are explicit trust** — installing a plugin is equivalent to installing Python code; the operator is responsible.
2. **Allowlist is preferred over denylist in production** — strict mode.
3. **`@prismal_node` defaults to `security="standard"`** — explicit opt-out required.
4. **The LangChain adapter applies `SecurePromptBuilder`** to the input before the Runnable.
5. **Audit log of loads** with version and entry point — full traceability.

---

## 7. Observability

### 7.1 OTel Spans

| Component | Spans |
|---|---|
| Decorator | `prismal.ext.node.<name>` with attrs `node_name`, `session_id`, `capabilities`, `status` |
| Builder | `prismal.ext.builder.compile` with attrs `subgraph_name`, `node_count`, `edge_count` |
| Plugin discovery | `prismal.ext.discover` (overall), `prismal.ext.load_plugin.<name>` per plugin |
| Adapter | `prismal.ext.adapter.langchain.ainvoke` with attrs `runnable_type`, `input_chars` |

### 7.2 Metrics

```
# Plugins
prismal_plugins_discovered_total{group="subgraphs|nodes|tools|rag_engines"}
prismal_plugins_loaded_total{name, status="success|error", group}
prismal_plugin_load_duration_seconds{name}

# Custom nodes
prismal_custom_nodes_registered_total{node}
prismal_custom_nodes_invocations_total{node, status="success|error|timeout"}
prismal_custom_nodes_latency_seconds{node}

# Adapters
prismal_adapter_langchain_invocations_total{runnable_type}
prismal_adapter_langchain_latency_seconds{runnable_type}

# Builder
prismal_builder_compile_total{subgraph}
```

### 7.3 Startup Report

When the app boots with active plugins, the logger emits a structured summary:

```
INFO prismal.extension.discovery
  loaded=15 failed=2 skipped_by_denylist=1 duration_ms=287
  groups={subgraphs: 5, nodes: 8, tools: 2, rag_engines: 0}
  failed_plugins=[
    {name: "broken_plugin", error: "ImportError: ..."},
    {name: "another", error: "SyntaxError: ..."}
  ]
```

---

## 8. Testing Strategy

| Level | Coverage | Tools | What it covers |
|---|---|---|---|
| Unit | ≥ 85% per module | pytest + `AsyncMock` | Decorator, builder, plugin loader, adapter, ports |
| Integration | Critical flows | pytest + in-memory test plugin (via `pkg_resources` mock) | End-to-end discovery; custom node inside a graph |
| Live API | `@pytest.mark.live_api` | Skipped by default | Adapter with a real LangChain `AgentExecutor` |
| Bench | `@pytest.mark.bench` | pytest-benchmark | Decorator overhead ≤ 5 ms p95 |
| Plugin lifecycle | `tests/integration/test_plugin_discovery_e2e.py` | creates a temporary wheel with `build` + installs in an isolated venv | Verifies that a "real" plugin is discovered |

### Mock Strategy

- **Entry points:** `monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None: [...])` to inject test plugins without touching the filesystem.
- **LangChain Runnable:** built with `RunnableLambda(lambda x: AIMessage(content="ok"))` for fast tests.
- **OTel:** `OTelManager` mocked to verify that the correct span was opened/closed.
- **Audit:** `AuditLogger.log_event` mocked to verify that loads were logged.

---

## 9. Rollout Plan

### 9.1 Adoption Strategy

Phase X is **additive and opt-in**:

1. `prismal.langgraph` is published as a new module — it affects no one.
2. `@prismal_node`, builder, adapters are new APIs — they do not affect existing nodes.
3. `discover_plugins()` is invoked explicitly from the operator's startup; `settings.plugins_autodiscover=True` by default but with no plugins installed it is a no-op.
4. Existing nodes (26 textual) **are not decorated retroactively** in Phase X. Migration is a later follow-up (Phase X.1) if consolidating the behavior is desired.

### 9.2 Backward Compatibility

- Zero changes to the existing public API.
- `SubgraphRegistry` and `register_<name>()` continue to work identically.
- Existing tests (~688) pass without modification.
- New exceptions inherit from `PrismalError` (known hierarchy).

### 9.3 API Stability

The extension surface is public API with a SemVer commitment:

- **Breaking changes** require a **minor** bump (pre-1.0) or **major** (post-1.0).
- **Deprecations** via `warnings.warn(DeprecationWarning, stacklevel=2)` with a minimum of 1 minor release of notice.
- The internal `@frozen_api` decorator marks the public contract's functions; a pre-commit hook validates that they do not change without documented justification.

---

## 10. Open Questions

- [ ] **`@prismal_node` defaults:** `security="standard"` by default or `security="off"` to avoid silent overhead? — Owner: Tech Lead, Deadline: start of X2.
- [ ] **Entry point for `rag_engines`:** really useful or feature creep? The current 7 engines are in-tree. — Owner: AI Architect, Deadline: start of X4.
- [ ] **Plugin sandboxing:** do we allow running a plugin in an isolated subprocess (future `prismal.plugins exec --sandbox`) or do we trust the current security hierarchy? — Owner: Tech Lead, Deadline: Phase Y.
- [ ] **Compatibility with `langgraph-checkpoint-redis`** and other external checkpointers — declare `CheckpointPort` with the exact contract they use? — Owner: AI Architect.
- [ ] **CLI `python -m prismal.plugins`:** include in Phase X or defer to a follow-up? Low cost but scope creep. — Owner: DX Lead, Deadline: start of X4.
- [ ] **Plugin templates:** `cookiecutter` or `copier`? Copier has a better modern UX but lower adoption. — Owner: DX Lead.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — LangGraph extension surface + plugin SDK |
