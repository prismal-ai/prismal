# Prismal — Extension Surface (LangGraph Passthrough + Plugin SDK)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **Reviewers** | Tech Lead, AI Architect, DX Lead |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Executive Summary

Prismal wraps LangGraph internally: it offers 26 specialist agents, 7 RAG engines, 9 patterns, 11 subgraphs, and a planned multimodal layer. However, **it does not expose a public API for a user to build new patterns directly on top of LangGraph** while benefiting from the existing infrastructure (5-layer security, OTel, audit, `ProviderRegistry`, capability routing, checkpointing). Today, extending prismal requires either forking the repo or duplicating cross-cutting-concern wiring in each new node.

This document defines a **deliberate extension surface** that turns prismal into "LangGraph with batteries included, not LangGraph hidden". Five components:

1. **Official re-export** (`prismal.langgraph`) — a single, versioned entry point for `StateGraph`, `Send`, `interrupt`, `add_messages`.
2. **`@prismal_node` decorator** — wraps any `async (state) → state_update` with security, OTel span, audit, and capability registration.
3. **`PrismalStateGraphBuilder`** — a fluent API over `StateGraph[AgentState]` that applies prismal defaults in `add_node()`.
4. **Plugin discovery via entry points** — `prismal.subgraphs` and `prismal.nodes` allow external packages (`prismal-x-finance`, `prismal-x-healthcare`) to auto-register.
5. **`LangChainRunnableAdapter`** — converts any LangChain `Runnable` / `AgentExecutor` into a valid prismal graph node.

The deliverable is **opt-in and additive**: no existing consumer is affected. It enables an ecosystem of external plugins without touching the core, and lowers the adoption cost for teams with prior LangChain code.

---

## 2. Context and Problem

### 2.1 Current Situation

- **Implicit but undocumented extensibility.** `SubgraphRegistry` (`agents/subgraphs/registry.py`) and the `register_<name>(registry)` convention already allow registering external subgraphs, but there are no docs, examples, or a versioned contract.
- **Cross-cutting by convention, not by contract.** Each node hand-writes its OTel spans, its logger, its calls to `SecurePromptBuilder`/`ActionInterceptor.check()`. Forgetting one is a silent bug (there is no validation).
- **No plugin discovery.** An external package cannot contribute nodes to the supervisor without asking the operator to manually call register at startup. There are no `entry_points`, no plugin namespace.
- **LangGraph "hidden" by convention.** Although `agents/graph.py` imports LangGraph and uses it, the external user does not know which version is compatible, which imports are safe, or how to build a `StateGraph` leveraging `AgentState` and `add_messages`.
- **LangChain `Runnable` cannot be used as a node** without writing the adapter every time.

### 2.2 Problem

Without an extension surface:

1. **Forking is the only option** to add a new pattern not contemplated in Phase A/B/C/F.
2. **Each team reinvents** the security/OTel/audit wrappers in its custom node — with the risk of skipping one.
3. **There is no ecosystem.** There cannot exist a `prismal-x-healthcare` or `prismal-x-finance` as independent pip packages.
4. **High adoption curve.** A team already using LangChain/LangGraph must migrate everything to prismal instead of adapting incrementally.

### 2.3 Opportunity

The necessary primitives **already mostly exist** in the repo:
- Callable injection (Phase B) already proves that the patterns accept extension without coupling.
- `SubgraphRegistry` + `register_<name>()` is already the canonical pattern.
- `ProviderRegistry`, `SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`, `OTelManager` are already-available components.

What is missing is to **declare the contract and package it** as a public API. The effort is low (five small modules), the impact on adoption and ecosystem is high, and it breaks nothing existing.

---

## 3. Target Users

### Persona 1: Framework Integrator
- **Description:** Engineer who integrates prismal into a product and needs a proprietary pattern (e.g. an internal domain workflow with uncommon rules).
- **Main need:** Build custom nodes that participate in the state machine without losing security/observability.
- **Usage frequency:** Weekly/Monthly.

### Persona 2: Plugin Author
- **Description:** Maintains a `prismal-x-<domain>` package distributed on PyPI with domain-specific nodes, subgraphs, and skills.
- **Main need:** That their package auto-registers on install and does not require modifying the prismal core.
- **Usage frequency:** Daily during plugin development.

### Persona 3: LangChain Migrator
- **Description:** Team with LangChain code (chains, `Runnable`, `AgentExecutor`) that wants to adopt prismal without rewriting everything.
- **Main need:** A one-step adapter that takes their `Runnable` and exposes it as a valid node.
- **Usage frequency:** Initial migration + occasional extensions.

### Persona 4: Researcher / Pattern Designer
- **Description:** Wants to experiment with a new pattern (e.g. a variant of MCTS, a router with a custom classifier) without waiting for it to enter the prismal roadmap.
- **Main need:** Access to `StateGraph`, `Send`, `interrupt` with explicit docs and reusable `AgentState`.

---

## 4. Objectives and Success Metrics

### 4.1 Business Objectives

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Automated cross-cutting coverage | % of custom nodes that get security+OTel+audit without writing code | 100% via `@prismal_node` | Phase X |
| Plugin ecosystem | `prismal-x-*` packages on PyPI | ≥ 2 pilot packages | 3 months post-merge |
| Extension "hello world" time | Minutes from `pip install prismal` to first working custom node | ≤ 15 min | Phase X |
| Backward compatibility | Existing tests (~688) passing without changes | 100% | Global |
| LangChain onboarding | Demo migrating an `AgentExecutor` to a prismal node | ≤ 30 LoC | Phase X |
| Test coverage | Branch coverage of new modules | ≥ 85% | Global |

### 4.2 User Objectives

| User Objective | Indicator |
|---|---|
| Build a custom node in minutes | `@prismal_node` documented with a runnable example |
| Auto-register a plugin without touching core | Entry points `prismal.subgraphs` work via `importlib.metadata` |
| Reuse an existing `Runnable` | `LangChainRunnableAdapter(runnable).as_node()` |
| Build a custom subgraph with free security/audit | `PrismalStateGraphBuilder` applies defaults without asking anything of the user |
| Know which LangGraph version prismal uses | `prismal.langgraph.VERSION` + module docstring |

---

## 5. Scope

### 5.1 In Scope (Included — Phase X)

**X1 — Official re-export (`prismal/langgraph.py`):**
- [ ] Module `prismal.langgraph` that re-exports `StateGraph`, `START`, `END`, `Send`, `interrupt`, `add_messages`, `CompiledStateGraph`.
- [ ] `VERSION` constant with the `langgraph` version resolved via `importlib.metadata`.
- [ ] Docstring that documents compatibility and deprecation.

**X2 — `@prismal_node` decorator (`prismal/agents/extension/decorators.py`):**
- [ ] Decorator that wraps `async (state: AgentState) → dict` with an OTel span, structured logger, audit hook, error handling → `PrismalError`.
- [ ] Parameters: `name`, `capabilities`, `security`, `audit`, `retry`, `timeout_s`.
- [ ] Automatic registration in the `tool_registry`'s `DEFAULT_CAPABILITY_MAP` when the module is imported.

**X3 — Fluent builder (`prismal/agents/extension/builder.py`):**
- [ ] `PrismalStateGraphBuilder` that wraps `StateGraph[AgentState]` with methods `.add_node()`, `.add_edge()`, `.add_conditional_edges()`, `.add_supervisor()`, `.add_security_layer()`, `.compile()`.
- [ ] Each `.add_node()` applies the equivalent of `@prismal_node` if the callable is not already.

**X4 — Plugin discovery (`prismal/agents/extension/plugins.py`):**
- [ ] Entry point groups: `prismal.subgraphs`, `prismal.nodes`, `prismal.tools`, `prismal.rag_engines`.
- [ ] `discover_plugins()` that iterates via `importlib.metadata.entry_points()` and calls the `register(registry)` function declared by each plugin.
- [ ] Toggle `settings.plugins_autodiscover` (default `True`) to disable in sandboxed environments.
- [ ] CLI helper (optional): `python -m prismal.plugins list`.

**X5 — `LangChainRunnableAdapter` (`prismal/agents/extension/adapters.py`):**
- [ ] Wrapper that takes a LangChain `Runnable` or `AgentExecutor` and returns an async function `(state) → state_update`.
- [ ] `as_node(name=..., capabilities=...)` for direct registration.
- [ ] Automatic mapping of `state["messages"]` ↔ the Runnable's input/output.

**X6 — Formalized ports and adapters (`prismal/agents/extension/ports.py`):**
- [ ] `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` as explicit `Protocol`s.
- [ ] Existing adapters (SqliteSaver, AuditLogger, etc.) declare conformance.
- [ ] Allows users to substitute implementations without touching the core.

**X7 — Documentation and examples:**
- [ ] `docs/extension.md` with quickstart + recipes.
- [ ] `examples/custom_node.py` — custom node with `@prismal_node`.
- [ ] `examples/custom_subgraph.py` — custom subgraph with `PrismalStateGraphBuilder`.
- [ ] `examples/plugin_template/` — skeleton of a `prismal-x-<name>` package ready for PyPI.
- [ ] `examples/langchain_migration.py` — migration of an `AgentExecutor` to a prismal node.

**X8 — Settings and observability:**
- [ ] `settings.plugins_autodiscover: bool = True`.
- [ ] `settings.plugins_allowlist: list[str] = []` (empty = all discovered).
- [ ] `settings.plugins_denylist: list[str] = []`.
- [ ] Metrics: `prismal_plugins_discovered_total`, `prismal_plugins_loaded_total{status="success|error"}`, `prismal_custom_nodes_invocations_total{node}`.

### 5.2 Out of Scope (Excluded)

- **Full DI container** (in the style of `dependency-injector`) — high overhead vs current benefit; the "inject `settings: Settings | None = None`" pattern suffices.
- **Custom DSL over LangGraph** — would break the "it's standard LangGraph" principle; the user must be able to read LangGraph docs and apply them as-is.
- **Hot reload of plugins** — requires complex infrastructure; restarting the process is acceptable.
- **Plugin marketplace** — outside the framework's scope; would be delegated to an external property (`plugins.prismal.dev`) in later phases.
- **Automated migration of LangChain chains** — the adapter resolves `Runnable`; deeper transformations are the user's responsibility.
- **Cryptographic signing of plugins** — Phase Y; in X we trust the standard PyPI ecosystem + allowlist/denylist by name.

### 5.3 Future Considerations (Phase Y+)

- Plugin signing + optional verification.
- Hot reload via `watchdog`.
- Plugin marketplace UI.
- Declarative schema validation of node inputs/outputs (with optional Pydantic models).
- Quotas/sandboxing per plugin (CPU/memory/network).
- Aggregated telemetry per plugin (latency, errors, usage).

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-EXT-001 | `prismal.langgraph` re-exports LangGraph symbols with a declared version | `MUST` |
| RF-EXT-002 | `@prismal_node` wraps callables with OTel span + logger + audit + error handling | `MUST` |
| RF-EXT-003 | `@prismal_node` registers capabilities automatically on import | `SHOULD` |
| RF-EXT-004 | `PrismalStateGraphBuilder` provides a fluent API over `StateGraph[AgentState]` | `MUST` |
| RF-EXT-005 | `discover_plugins()` iterates entry points and calls `register(registry)` | `MUST` |
| RF-EXT-006 | Toggle `plugins_autodiscover` + allowlist/denylist via settings | `MUST` |
| RF-EXT-007 | `LangChainRunnableAdapter` converts `Runnable`/`AgentExecutor` to a valid node | `SHOULD` |
| RF-EXT-008 | Explicit `Protocol`s for `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` | `SHOULD` |
| RF-EXT-009 | Runnable examples: custom node, custom subgraph, plugin template, LangChain migration | `MUST` |
| RF-EXT-010 | Plugin metrics: `discovered_total`, `loaded_total`, `custom_nodes_invocations_total` | `SHOULD` |

---

## 7. Non-Functional Requirements

### Performance
- `@prismal_node` decorator overhead ≤ 5 ms per invocation (excluding the OTel exporter, which is async).
- Plugin discovery at startup: ≤ 500 ms for 50 installed plugins.
- `PrismalStateGraphBuilder.compile()` must not be slower than `StateGraph.compile()` + 50 ms (wrapping overhead).

### Security
- Plugin allowlist/denylist enforcement before invoking the plugin's `register()`.
- Each node registered via `@prismal_node` must go through `ActionInterceptor` if it declares `tool_calls`.
- `LangChainRunnableAdapter` applies `SecurePromptBuilder` to the input before invoking the `Runnable`.
- Audit log records the load of each plugin with its version, the wheel hash (when available via `importlib.metadata`), and the entry point used.

### Availability
- Failure loading one plugin **does not prevent** the startup of the rest: structured error log + metric `plugins_loaded_total{status="error"}`, normal continuation.
- A plugin with a runtime error must not bring down the main graph — the `@prismal_node` wrapper captures exceptions and emits a `state_update` with the `error=True` flag.

### Scalability
- Support ≥ 50 installed plugins without appreciable degradation.
- Support ≥ 200 custom nodes registered with `@prismal_node`.

### Observability
- OTel spans: `prismal.ext.discover`, `prismal.ext.load_plugin{name}`, `prismal.ext.node{name}`, `prismal.ext.adapter.langchain`.
- Prometheus-compatible metrics already listed in X8.
- Structured logs with `plugin_name`, `node_name`, `entry_point`.

### Maintainability
- Coverage ≥ 85% per new module (a higher target than the 80% global, since it is public API).
- `ruff` + `mypy --strict` + `bandit` clean.
- Versioned API: breaking changes to the extension surface require a minor bump in SemVer and a deprecation warning 1 release ahead.

### Documentation
- A 1-page quickstart.
- Cookbook of common patterns (custom router, conditional gate, post-processor, etc.).
- Plugin template ready for `cookiecutter` or `copier`.
- Migration guide from LangChain.

---

## 8. Constraints and Dependencies

### Technical Constraints
- Python 3.13+, `uv` as the manager.
- Keep `prismal/` as a PEP 420 namespace package.
- Compatibility: the `@prismal_node` contract must work with LangGraph ≥ 0.4 (the repo's current version).
- No new mandatory dependencies for the core (everything new must be stdlib or already present).

### External Dependencies

| Dependency | Type | Use | Status |
|---|---|---|---|
| `langgraph` | Existing | Re-export and builder | ✅ Already included |
| `langchain-core` | Existing | `Runnable` interface for the adapter | ✅ Already included |
| `importlib.metadata` | Stdlib | Plugin discovery | ✅ Stdlib |
| `opentelemetry-api` | Existing | Spans in the decorator | ✅ Already included |
| `structlog` | Existing | Logging | ✅ Already included |

**No new dependencies** — all the work is on the already-installed stack.

---

## 9. User Stories

### Epic X: Build Your Own Node

**US-EXT-001:** As a Framework Integrator, I want to decorate my async function as a prismal node to get security, OTel, and audit without writing the wiring.
```python
from prismal.agents.extension import prismal_node

@prismal_node(name="my_classifier", capabilities=["general"])
async def my_classifier(state):
    return {"messages": [...], "metadata": {"my_node": {"score": 0.8}}}
```
- [ ] Works without further config.
- [ ] OTel span appears in the trace export with `node.name="my_classifier"`.
- [ ] Audit log contains an entry per invocation.

### Epic X: Build Your Own Subgraph

**US-EXT-002:** As a Framework Integrator, I want to assemble a subgraph with a fluent API that applies defaults.
```python
from prismal.agents.extension import PrismalStateGraphBuilder

builder = PrismalStateGraphBuilder("my_pipeline")
builder.add_node("classify", classify_fn)        # auto-wrapped with @prismal_node
builder.add_node("respond", respond_fn)
builder.add_edge("classify", "respond")
subgraph = builder.compile()
```
- [ ] `subgraph` is a registrable `SubgraphDefinition`.

### Epic X: Plugin Ecosystem

**US-EXT-003:** As a Plugin Author, I want to publish `prismal-x-healthcare` that auto-registers on install.
```toml
# the plugin's pyproject.toml
[project.entry-points."prismal.subgraphs"]
healthcare_triage = "prismal_x_healthcare:register_healthcare_pipeline"
```
- [ ] After `pip install prismal-x-healthcare`, calling `discover_plugins()` leaves the subgraph registered.
- [ ] The operator can disable it via `settings.plugins_denylist=["prismal_x_healthcare"]`.

### Epic X: LangChain Bridge

**US-EXT-004:** As a LangChain Migrator, I want to use my `AgentExecutor` as a prismal node without rewriting it.
```python
from prismal.agents.extension import LangChainRunnableAdapter

adapter = LangChainRunnableAdapter(my_agent_executor)
node = adapter.as_node(name="legacy_agent", capabilities=["research"])
```
- [ ] The node participates in the prismal state machine.
- [ ] Input/output `state["messages"]` are mapped correctly.

---

## 10. Risks and Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Malicious plugin executes arbitrary code on load | Medium | Critical | Allowlist/denylist via settings; audit log of each load; document that entry points are the operator's explicit trust |
| Decorator overhead adds perceptible latency | Low | Medium | Benchmark in CI (target ≤ 5 ms); span caching; opt-out via `@prismal_node(otel=False)` |
| Drift between the documented and actual LangGraph version | Medium | High | `prismal.langgraph.VERSION` is resolved dynamically; compatibility tests in CI on each upgrade |
| LangChain adapter breaks with an API change in `Runnable` | Medium | Medium | Minimum version pin in `pyproject.toml`; smoke tests per LangChain release |
| `discover_plugins()` fails and blocks startup | Low | High | Each plugin is loaded in an isolated try/except; individual failure does not affect the rest |
| Name conflict between plugins | Medium | Medium | Registry detects duplicates and rejects with a clear error; recommended namespace `<vendor>_<name>` |
| Plugins installed unknowingly (autodiscover default `True`) | Medium | High | Toggle available; log of loaded plugins visible at startup; allowlist as strict mode |
| `@prismal_node` backward compat breaks between versions | Low | High | API frozen with internal `@frozen_api` decorator; deprecation cycle of 1 minor minimum |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| X1 — Official re-export | 0.2 week | `prismal.langgraph` |
| X2 — `@prismal_node` decorator | 1 week | Decorator + automatic registration + tests |
| X3 — Fluent builder | 0.8 week | `PrismalStateGraphBuilder` + tests |
| X4 — Plugin discovery | 1 week | Entry points + `discover_plugins()` + CLI + tests |
| X5 — LangChain adapter | 0.5 week | `LangChainRunnableAdapter` + tests |
| X6 — Formalized ports | 0.5 week | `Protocol`s + smoke tests |
| X7 — Docs + examples | 1 week | 4 runnable examples + `docs/extension.md` |
| X8 — Settings + metrics | 0.2 week | Toggles + counters |
| Hardening | 0.8 week | Coverage ≥ 85%, security audit, external plugin example on TestPyPI |
| **Total** | **~6 weeks** | Complete extension surface + pilot ecosystem |

---

## 12. Definition of Done (Phase X Global)

- [ ] `prismal.langgraph` re-exports the 7 key symbols + `VERSION`.
- [ ] `@prismal_node` documented and tested with examples.
- [ ] `PrismalStateGraphBuilder` with a functional fluent API.
- [ ] `discover_plugins()` loads plugins from entry points + respects the allow/denylist.
- [ ] `LangChainRunnableAdapter` converts `Runnable` and `AgentExecutor` to valid nodes.
- [ ] 4 port `Protocol`s declared + existing adapters conforming.
- [ ] 4 runnable examples in `examples/`.
- [ ] `docs/extension.md` published.
- [ ] Plugin template (`examples/plugin_template/`) ready for `cookiecutter` or `copier`.
- [ ] Coverage ≥ 85% in `prismal/agents/extension/` and `prismal/langgraph.py`.
- [ ] Decorator overhead benchmark ≤ 5 ms documented.
- [ ] 2 pilot plugins published on TestPyPI (e.g. `prismal-x-hello`, `prismal-x-financial-extra`).
- [ ] `uv run pytest -m "not live_api"` passes at 100%.
- [ ] `ruff` + `mypy --strict` + `bandit` clean.
- [ ] `CLAUDE.md` and `README.md` updated.
- [ ] PR merged to `main` with code review approved.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — LangGraph extension surface + plugin SDK |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
| DX Lead | — | | ☐ Pending |
