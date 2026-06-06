# Prismal Extension Surface — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-05-27 |
| **PLAN** | `specs/extension-surface/PLAN.md` |
| **Architecture** | `specs/extension-surface/ARCHITECTURE.md` |
| **SPEC** | `specs/extension-surface/SPEC.md` |

---

> **Implementation status (2026-05-30):** Phase X is **implemented** in the
> repository. The modules live in `prismal/agents/extension/` (`decorators.py`,
> `_middleware.py`, `_registry.py`, `builder.py`, `plugins.py`, `adapters.py`,
> `ports.py`), plus `prismal/langgraph.py` and the CLI `prismal/plugins.py`. The
> documentation is in `docs/extension.md`, the examples in `examples/extension/`
> and `examples/plugin_template/`, with tests in `tests/unit/agents/extension/`.
> Recorded in `CHANGELOG.md` under `[Unreleased] — Extension Surface (Phase X)`.
> The `☐` checkboxes below are the original plan and were not kept up to date
> during execution; they serve as historical reference, not as live status.

---

## 1. Implementation Summary

Phase X is divided into **8 sequential sub-phases** plus hardening:

- **X1 (0.2 week):** Official re-export `prismal.langgraph`.
- **X2 (1 week):** `@prismal_node` decorator + internal middleware chain.
- **X3 (0.8 week):** `PrismalStateGraphBuilder` fluent API.
- **X4 (1 week):** Plugin discovery via entry points + CLI.
- **X5 (0.5 week):** `LangChainRunnableAdapter`.
- **X6 (0.5 week):** Formalized ports (`Protocol`s) + verification of existing conformance.
- **X7 (1 week):** Documentation + 4 runnable examples + plugin template.
- **X8 (0.2 week):** Settings + metrics + audit hooks.
- **Hardening (0.8 week):** Coverage ≥ 85%, decorator benchmark, security audit, publication of 2 pilot plugins to TestPyPI.

**Total estimated duration:** ~6 weeks
**Minimum team:** 1 senior engineer with experience in LangGraph + Python tooling (entry points, packaging).
**Target date:** 2026-07-10

---

## 2. Prerequisites

| Prerequisite | Owner | Status | Deadline |
|---|---|---|---|
| PLAN.md approved | Tech Lead + DX Lead | ☐ Pending | 2026-06-01 |
| ARCHITECTURE.md approved | Tech Lead + AI Architect | ☐ Pending | 2026-06-01 |
| SPEC.md approved | Tech Lead | ☐ Pending | 2026-06-01 |
| Decision on the default for `@prismal_node(security=...)` | Tech Lead | ☐ Pending | Start of X2 |
| Decision on `cookiecutter` vs `copier` for the template | DX Lead | ☐ Pending | Start of X7 |
| Branch `feature/extension-surface` created | Engineer | ☐ Pending | Start of X1 |
| Existing test suite passes at 100% (688+) | Engineer | ☐ To verify | Start of X1 |
| Name reserved on TestPyPI: `prismal-x-hello` | DevOps | ☐ Pending | Before Hardening |

---

## 3. Implementation Phases

---

### PHASE X1 — Official Re-export

**Duration:** 0.2 week | **File:** `prismal/langgraph.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X1-01 | Create `prismal/langgraph.py` with re-exports + `VERSION` | 0.2d | — | ☐ |
| X1-02 | Complete docstring with example and versioning note | 0.2d | X1-01 | ☐ |
| X1-03 | Unit test: import each symbol, verify identity with upstream | 0.3d | X1-01 | ☐ |
| X1-04 | Test that `VERSION` is not empty and matches `importlib.metadata.version("langgraph")` | 0.1d | X1-01 | ☐ |
| X1-05 | Add to `__init__.py` (if namespace, avoid) or document the import path | 0.1d | X1-01 | ☐ |

**X1 Done criteria:**
- `from prismal.langgraph import StateGraph` works and is identical to upstream.
- `prismal.langgraph.VERSION` returns the dynamically resolved version.
- Docstring includes a runnable example.
- Coverage ≥ 90% (it is a small module).

---

### PHASE X2 — `@prismal_node` Decorator

**Duration:** 1 week | **Files:** `prismal/agents/extension/decorators.py`, `_middleware.py`, `_registry.py`

#### X2-01 — Base structure
**Estimate:** 1 day

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X2-01-01 | Create `prismal/agents/extension/` with `__init__.py` (empty re-exports for now) | 0.1d | — | ☐ |
| X2-01-02 | `decorators.py` with `NodeMetadata`, `RetryPolicy`, `SecurityLevel` dataclasses/types | 0.4d | X2-01-01 | ☐ |
| X2-01-03 | `_registry.py` with thread-safe `_REGISTERED_NODES: dict[str, NodeMetadata]` | 0.3d | X2-01-02 | ☐ |
| X2-01-04 | Tests of the dataclasses (frozen, equality, repr) | 0.2d | X2-01-02 | ☐ |

#### X2-02 — Internal middleware chain
**Estimate:** 2 days | **File:** `_middleware.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X2-02-01 | Signature `Middleware = Callable[[NodeFn, AgentState, NodeMetadata], Awaitable[dict]]` | 0.2d | X2-01 | ☐ |
| X2-02-02 | `security_middleware` that applies `InputSanitizer` + `SecurePromptBuilder` according to level | 0.5d | X2-02-01 | ☐ |
| X2-02-03 | `otel_middleware` with span open/close, standard attrs | 0.3d | X2-02-01 | ☐ |
| X2-02-04 | `logger_middleware` with contextual `structlog.bind()` | 0.2d | X2-02-01 | ☐ |
| X2-02-05 | `retry_middleware` with configurable exponential backoff | 0.4d | X2-02-01 | ☐ |
| X2-02-06 | `timeout_middleware` with `asyncio.wait_for` + mapping to `NodeTimeoutError` | 0.2d | X2-02-01 | ☐ |
| X2-02-07 | `audit_middleware` with xxhash hash of state_update + duration_ms | 0.3d | X2-02-01 | ☐ |
| X2-02-08 | `error_mapping_middleware` that captures BaseException → `NodeExecutionError` or state_update with error | 0.4d | X2-02-01 | ☐ |
| X2-02-09 | `build_pipeline()` that composes middlewares in reverse order | 0.3d | X2-02-02..08 | ☐ |
| X2-02-10 | Unit tests per middleware (≥10 tests, covering on/off of each) | 0.6d | X2-02-09 | ☐ |

#### X2-03 — Public decorator
**Estimate:** 1.5 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X2-03-01 | `prismal_node()` factory that builds `NodeMetadata` and applies `build_pipeline()` | 0.6d | X2-02 | ☐ |
| X2-03-02 | Side effect: registration in `_REGISTERED_NODES` + `DEFAULT_CAPABILITY_MAP` | 0.3d | X2-03-01 | ☐ |
| X2-03-03 | `list_registered_nodes()` + `get_node_metadata()` | 0.2d | X2-03-02 | ☐ |
| X2-03-04 | `__prismal_node__` and `__wrapped__` attributes on the returned callable | 0.2d | X2-03-01 | ☐ |
| X2-03-05 | Decorator tests: with/without params, double decoration (idempotent), introspection | 0.5d | X2-03-01..04 | ☐ |
| X2-03-06 | Exceptions `NodeExecutionError`, `NodeTimeoutError`, `NodeValidationError` in `core/exceptions.py` | 0.2d | — | ☐ |

#### X2-04 — Decorator benchmark
**Estimate:** 0.5 day

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X2-04-01 | `@pytest.mark.bench` test that measures overhead vs bare function | 0.3d | X2-03 | ☐ |
| X2-04-02 | Documented target: ≤ 5 ms p95 per invocation | 0.1d | X2-04-01 | ☐ |
| X2-04-03 | Optional CI step that reports the benchmark on the PR | 0.2d | X2-04-01 | ☐ |

**X2 Global Criteria:**
- Each middleware has isolated tests.
- `@prismal_node` documented with a runnable example in the docstring.
- Benchmark documented in `docs/extension.md`.
- Coverage ≥ 85% in `decorators.py` and `_middleware.py`.

---

### PHASE X3 — Fluent Builder

**Duration:** 0.8 week | **File:** `prismal/agents/extension/builder.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X3-01 | `BuilderDefaults` dataclass | 0.1d | — | ☐ |
| X3-02 | `PrismalStateGraphBuilder.__init__` with internal `StateGraph[AgentState]` | 0.3d | X2 | ☐ |
| X3-03 | `add_node()` with auto-wrap if the callable lacks `__prismal_node__` | 0.5d | X3-02 | ☐ |
| X3-04 | `add_edge()`, `add_conditional_edges()`, `set_entry_point()` (passthrough) | 0.3d | X3-02 | ☐ |
| X3-05 | `add_supervisor_node()` with validation of `valid_next` | 0.4d | X3-02 | ☐ |
| X3-06 | `add_security_layer(at="entry"|"exit")` inserting a dedicated node | 0.3d | X3-02 | ☐ |
| X3-07 | `compile()` returns `SubgraphDefinition` with enriched metadata | 0.4d | X3-02..06 | ☐ |
| X3-08 | `compile_raw()` returns `CompiledStateGraph` (escape hatch) | 0.1d | X3-07 | ☐ |
| X3-09 | Tests: full fluent API, duplicates, edge validation, invalid supervisor | 0.7d | X3-07 | ☐ |

**X3 Global Criteria:**
- Builder runs with no functional difference from a direct `StateGraph` (zero regression).
- Auto-wrap detected via `hasattr(fn, "__prismal_node__")`.
- Coverage ≥ 85%.

---

### PHASE X4 — Plugin Discovery

**Duration:** 1 week | **Files:** `plugins.py`, `prismal/agents/extension/plugins.py`

#### X4-01 — Discovery core
**Estimate:** 2.5 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X4-01-01 | `PluginGroup`, `PluginInfo`, `PluginLoadResult`, `DiscoveryReport` dataclasses | 0.3d | — | ☐ |
| X4-01-02 | `_iter_entry_points(group)` wrapper over `importlib.metadata.entry_points` | 0.3d | X4-01-01 | ☐ |
| X4-01-03 | `_load_subgraph_plugin(ep)` invokes `register(registry)` with try/except | 0.4d | X4-01-02 | ☐ |
| X4-01-04 | `_load_node_plugin(ep)` imports the callable and verifies `__prismal_node__` | 0.3d | X4-01-02 | ☐ |
| X4-01-05 | `_load_tool_plugin(ep)` adds to `tool_registry` respecting cap=120 | 0.4d | X4-01-02 | ☐ |
| X4-01-06 | `_load_rag_engine_plugin(ep)` registers in the new `RAGEngineRegistry` | 0.3d | X4-01-02 | ☐ |
| X4-01-07 | `discover_plugins(settings, registry, groups)` orchestrates | 0.5d | X4-01-03..06 | ☐ |
| X4-01-08 | Allowlist/denylist enforcement with documented precedence | 0.3d | X4-01-07 | ☐ |

#### X4-02 — Audit and observability
**Estimate:** 0.5 day

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X4-02-01 | `AuditLogger.log_event("plugin_loaded", payload)` per load | 0.2d | X4-01-07, X2 | ☐ |
| X4-02-02 | Metrics: `prismal_plugins_loaded_total{name,status,group}`, `plugin_load_duration_seconds` | 0.2d | X4-01-07 | ☐ |
| X4-02-03 | Structured startup report with `loaded/failed/skipped` | 0.1d | X4-01-07 | ☐ |

#### X4-03 — `list_plugins()` and `get_plugin_info()`
**Estimate:** 0.5 day

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X4-03-01 | `list_plugins()` without loading — only inspects entry points | 0.3d | X4-01-02 | ☐ |
| X4-03-02 | `get_plugin_info(name)` returns `PluginInfo` or None | 0.2d | X4-03-01 | ☐ |

#### X4-04 — CLI `python -m prismal.plugins`
**Estimate:** 1 day

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X4-04-01 | `prismal/plugins.py` with `main(argv)` and subcommand dispatch | 0.4d | X4-03 | ☐ |
| X4-04-02 | `list` — table with name, group, version, status | 0.2d | X4-04-01 | ☐ |
| X4-04-03 | `info <name>` — details (module, object, dist_version) | 0.1d | X4-04-01 | ☐ |
| X4-04-04 | `doctor` — attempts to load all and reports formatted errors | 0.3d | X4-04-01 | ☐ |
| X4-04-05 | CLI tests with mocked `argparse` namespace | 0.3d | X4-04-04 | ☐ |

#### X4-05 — Tests
**Estimate:** 1.5 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X4-05-01 | Unit tests with `monkeypatch` of `entry_points` (≥ 15 tests) | 0.8d | X4-01..03 | ☐ |
| X4-05-02 | Integration test: creates a temporary wheel with `build`, installs in an isolated venv, discovers | 0.7d | X4-01 | ☐ |

**X4 Global Criteria:**
- Failure in one plugin does not abort the rest (specific test).
- Allowlist + denylist with edge cases covered.
- Executable CLI: `python -m prismal.plugins list` produces readable output.
- Coverage ≥ 85% in `plugins.py`.

---

### PHASE X5 — LangChain Adapter

**Duration:** 0.5 week | **File:** `prismal/agents/extension/adapters.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X5-01 | `LangChainRunnableAdapter.__init__` with signature detection | 0.3d | — | ☐ |
| X5-02 | `_map_input(state)` with `input_mapping` auto/messages/input_dict | 0.5d | X5-01 | ☐ |
| X5-03 | `_map_output(raw)` with AIMessage / str / dict detection | 0.4d | X5-01 | ☐ |
| X5-04 | `ainvoke(state)` that orchestrates map → runnable → map | 0.3d | X5-02, X5-03 | ☐ |
| X5-05 | `as_node(name, capabilities, security, timeout_s)` applies `@prismal_node` | 0.3d | X5-04, X2 | ☐ |
| X5-06 | Explicit support for `AgentExecutor` (subclass of `Runnable`) | 0.2d | X5-04 | ☐ |
| X5-07 | Tests with `RunnableLambda`, `RunnableSequence`, mocked `AgentExecutor` (≥ 12 tests) | 0.7d | X5-04..06 | ☐ |
| X5-08 | `LangChainAdapterError` exception in `core/exceptions.py` | 0.1d | — | ☐ |

**X5 Global Criteria:**
- Adapter supports the 4 common Runnable types.
- Integration test with a real `AgentExecutor` + mocked LLM (`live_api` optional).
- Coverage ≥ 85%.

---

### PHASE X6 — Formalized Ports

**Duration:** 0.5 week | **File:** `prismal/agents/extension/ports.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X6-01 | Define `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort` as `Protocol` with `@runtime_checkable` | 0.5d | — | ☐ |
| X6-02 | `conforms_to(obj, port)` helper with `isinstance(obj, port)` | 0.1d | X6-01 | ☐ |
| X6-03 | Conformance tests: `AsyncSqliteSaver` ⊨ `CheckpointPort`, `AuditLogger` ⊨ `AuditPort`, Chroma embeddings ⊨ `EmbeddingsPort`, `BaseTool` ⊨ `ToolPort` | 0.5d | X6-01 | ☐ |
| X6-04 | Docstring for each Protocol with examples of conforming implementations | 0.3d | X6-01 | ☐ |
| X6-05 | Test that a mock that does NOT conform → `conforms_to` returns False | 0.2d | X6-02 | ☐ |

**X6 Global Criteria:**
- Existing implementations conform without changes (zero regression).
- Coverage ≥ 90% (small, declarative module).

---

### PHASE X7 — Docs + Examples + Plugin Template

**Duration:** 1 week

#### X7-01 — Documentation
**Estimate:** 2 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X7-01-01 | `docs/extension.md` — Quickstart: hello world in ≤ 15 min | 0.5d | X2 | ☐ |
| X7-01-02 | `docs/extension.md` — Cookbook: custom router, gate, post-processor, supervisor wrapper | 0.5d | X3 | ☐ |
| X7-01-03 | `docs/extension.md` — Plugin lifecycle: declaration, installation, allowlist, troubleshooting | 0.4d | X4 | ☐ |
| X7-01-04 | `docs/extension.md` — LangChain migration guide | 0.4d | X5 | ☐ |
| X7-01-05 | `docs/extension.md` — Ports and adapters: how to substitute the checkpointer | 0.2d | X6 | ☐ |

#### X7-02 — Runnable examples
**Estimate:** 1.5 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X7-02-01 | `examples/custom_node.py` — hello world `@prismal_node` with mocked LLM | 0.3d | X2 | ☐ |
| X7-02-02 | `examples/custom_subgraph.py` — `PrismalStateGraphBuilder` end-to-end | 0.4d | X3 | ☐ |
| X7-02-03 | `examples/langchain_migration.py` — `AgentExecutor` → node via adapter | 0.4d | X5 | ☐ |
| X7-02-04 | `examples/discover_plugins.py` — `discover_plugins()` with in-memory plugin | 0.4d | X4 | ☐ |

#### X7-03 — Plugin Template
**Estimate:** 1.5 days

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X7-03-01 | Decide `cookiecutter` vs `copier` | 0.1d | — | ☐ |
| X7-03-02 | `examples/plugin_template/` with `pyproject.toml`, `src/{{name}}/`, `tests/`, `README.md` | 0.5d | X7-03-01 | ☐ |
| X7-03-03 | Template includes a `register_<name>()` example with `PrismalStateGraphBuilder` | 0.3d | X7-03-02 | ☐ |
| X7-03-04 | Template README with usage instructions | 0.2d | X7-03-02 | ☐ |
| X7-03-05 | Test that the template generates an installable package + discovered by `discover_plugins()` | 0.4d | X7-03-03 | ☐ |

**X7 Global Criteria:**
- Each example runs with `python examples/<name>.py` without error.
- The docs quickstart reproduces the example in ≤ 15 min.
- The plugin template generates an installable package.

---

### PHASE X8 — Settings + Metrics + Audit

**Duration:** 0.2 week | **File:** `prismal/core/config.py`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| X8-01 | Add `plugins_*` settings with env vars `PRISMAL_PLUGINS_*` | 0.3d | — | ☐ |
| X8-02 | Add `extension_default_*` settings | 0.2d | — | ☐ |
| X8-03 | `env.example` updated with the new variables | 0.1d | X8-01 | ☐ |
| X8-04 | Settings validation tests (Pydantic constraints) | 0.2d | X8-01 | ☐ |
| X8-05 | Metrics registered in `monitoring/` (counters + histograms) | 0.3d | X4, X5 | ☐ |

---

### HARDENING — Coverage, Bench, Pilot Plugins

**Duration:** 0.8 week

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H-01 | Coverage audit: new modules ≥ 85% | 0.5d | X1..X8 | ☐ |
| H-02 | `bandit -r prismal -c pyproject.toml` HIGH=0 MEDIUM=0 | 0.2d | X1..X8 | ☐ |
| H-03 | Benchmark published in `docs/extension.md` with decorator overhead | 0.2d | X2-04 | ☐ |
| H-04 | Pilot plugin `prismal-x-hello` published to TestPyPI | 0.5d | X4, X7 | ☐ |
| H-05 | Pilot plugin `prismal-x-financial-extra` published to TestPyPI | 0.5d | X4, X7 | ☐ |
| H-06 | Integration test: both plugins install + are discovered in a clean venv | 0.3d | H-04, H-05 | ☐ |
| H-07 | `pytest -m "not live_api"` passes at 100% (~828 tests expected) | 0.2d | X1..X8 | ☐ |
| H-08 | `ruff check .` + `mypy prismal --strict` clean | 0.2d | X1..X8 | ☐ |
| H-09 | Update `CLAUDE.md` with an "Extension surface" section | 0.2d | X1..X8 | ☐ |
| H-10 | Update `README.md` with features + extension section | 0.3d | X1..X8 | ☐ |
| H-11 | Update `CHANGELOG.md` with a Phase X entry | 0.1d | — | ☐ |
| H-12 | Internal code review (1 reviewer approves the PR) | 0.8d | H-01..11 | ☐ |
| H-13 | Merge to `main` | 0.1d | H-12 | ☐ |

---

## 4. Inter-Task Dependencies

```
X1 (re-export) ─┐
                ▶ X2 (decorator) ─┬──▶ X3 (builder) ──┐
                                  │                    │
                                  │                    ▶ X7 (docs+examples) ─┐
                                  │                    │                       │
                                  ▶ X5 (adapter) ─────┘                       │
                                                                                │
X4 (plugins) ───────────────────────────────────────────────────────────────────┤
                                                                                │
X6 (ports) ─────────────────────────────────────────────────────────────────────┤
                                                                                │
X8 (settings) ──────────────────────────────────────────────────────────────────┤
                                                                                ▼
                                                                         HARDENING
                                                                              │
                                                                              ▼
                                                                            MERGE
```

- X2 → X3 (builder uses the decorator for auto-wrap).
- X2 → X5 (adapter applies `@prismal_node` to the output).
- X4 can start on day 1 in parallel (depends only on stdlib).
- X6 can start on day 1 (only Protocol declarations).
- X7 waits for X2 + X3 + X4 + X5 at minimum.
- X8 can start on day 1 (new settings with no consumers until X4/X2).

---

## 5. Risk and Mitigation Matrix

| Risk | Probability | Impact | Mitigation | Owner |
|---|---|---|---|---|
| Decorator overhead > 5 ms | Medium | Medium | Bench in CI; granular opt-out per param; span caching | Engineer |
| Malicious plugin compromises startup | Medium | Critical | Allowlist in production; audit log; document policy | Tech Lead |
| Entry points API changes in Python | Low | High | Pin min Python 3.13; use the stable interface `importlib.metadata.entry_points(group=)` | Engineer |
| LangChain `Runnable` API breaks between versions | Medium | Medium | Pin min LangChain in pyproject; CI smoke test per release | Engineer |
| Name conflicts between plugins | Medium | Medium | Registry detects and rejects; recommended convention `<vendor>_<name>` | Tech Lead |
| Plugins installed unknowingly (autodiscover True) | Medium | High | Visible toggle at startup; doc recommends allowlist in prod | DX Lead |
| Decorator backward compat breaks | Low | High | Internal `@frozen_api`; CI validates signatures; mandatory deprecation cycle | Engineer |
| LangChain adapter mis-maps complex output | Medium | Medium | Covered cases: AIMessage/str/dict; explicit doc; clear error on fail | Engineer |
| Plugin template diverges from real practices | Low | Low | Template used by both pilot plugins in H-04/H-05; double validation | DX Lead |
| Low coverage of pilot plugins | Medium | Low | Each pilot plugin includes tests; used as a reference for authors | Engineer |

---

## 6. Definition of Done (Phase X Global)

- [ ] `prismal.langgraph` with 7 symbols + `VERSION`.
- [ ] `@prismal_node` decorator + middleware chain (8 middlewares) + `list_registered_nodes()`.
- [ ] `PrismalStateGraphBuilder` with a complete fluent API.
- [ ] `discover_plugins()` + CLI + entry points for 4 groups.
- [ ] `LangChainRunnableAdapter` with support for 4 common types.
- [ ] 4 `Protocol`s (`CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`) + verification of existing conformance.
- [ ] 4 runnable examples in `examples/`.
- [ ] `docs/extension.md` (quickstart + cookbook + migration + ports).
- [ ] Plugin template generator (cookiecutter/copier) in `examples/plugin_template/`.
- [ ] 2 pilot plugins published on TestPyPI (`prismal-x-hello`, `prismal-x-financial-extra`).
- [ ] `uv run pytest -m "not live_api"` passes at 100% (688 prior + ~140 new = ~828+).
- [ ] Coverage ≥ 85% in `prismal/agents/extension/` and `prismal/langgraph.py`.
- [ ] `uv run ruff check .` with no errors.
- [ ] `uv run mypy prismal` with no errors in strict mode.
- [ ] `uv run bandit -r prismal -c pyproject.toml` with no HIGH/CRITICAL.
- [ ] Decorator benchmark published: ≤ 5 ms p95 documented.
- [ ] `CLAUDE.md`, `README.md`, `CHANGELOG.md` updated.
- [ ] PR merged to `main` with 1 reviewer approved.

---

## 7. Effort Estimate per Sub-Phase

| Sub-Phase | Sub-tasks | Days | Weeks |
|---|---|---|---|
| X1 — Re-export | 5 | 1 | 0.2 |
| X2 — Decorator + middleware | 24 | 5 | 1 |
| X3 — Builder | 9 | 4 | 0.8 |
| X4 — Plugin discovery | 18 | 5 | 1 |
| X5 — LangChain adapter | 8 | 2.5 | 0.5 |
| X6 — Ports | 5 | 2 | 0.5 |
| X7 — Docs + examples + template | 13 | 5 | 1 |
| X8 — Settings + metrics | 5 | 1 | 0.2 |
| Hardening | 13 | 4 | 0.8 |
| **Total** | **~100** | **~30** | **~6** |

*Estimate based on 1 senior engineer. With 2 engineers: X1+X4+X6+X8 can run in parallel from day 1.*

---

## 8. Operational Success Metrics

After merging to `main`, monitor the first 4 weeks:

- `prismal_plugins_loaded_total{status="success"}` per deployment — confirms adoption.
- `prismal_plugins_loaded_total{status="error"}` — alert if >0% per plugin.
- `prismal_custom_nodes_invocations_total` per node — visibility into extension usage.
- `prismal_custom_nodes_latency_seconds` p95 — acceptable overhead.
- "hello world" time (DX survey of first 5 plugin authors) — target ≤ 15 min.
- GitHub issues tagged `extension-api` — feedback backlog.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-05-27 | Ernesto Crespo | Initial version — 100 sub-tasks across 9 phases, 6 weeks |
