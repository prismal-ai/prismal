# Prismal Tool Provider Injection — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/tool-provider-injection/PLAN.md` |
| **Architecture** | `specs/tool-provider-injection/ARCHITECTURE.md` |
| **SPEC** | `specs/tool-provider-injection/SPEC.md` |

---

## 1. Implementation Summary

Phase Y inverts the dependency between the agents layer and the MCP/Skills subsystems by introducing a `ToolProviderPort` and concrete providers that the host (`prismal-sdk`/`prismal-web`) composes and injects. The work is concentrated in:

- **Additive:** `ports.py` (+1 Protocol), `providers.py` (new), settings, exceptions, docs, examples, tests.
- **Low-risk refactor:** `tool_registry.py` (one file) moves from importing MCP/Skills to delegating to the provider.
- **Opt-in:** variant B (multi-tenant) touches `graph.py` only if `tool_provider_mode="context"` is enabled.

Guiding principle: **behavioral parity**. With the default composite, `get_tools_for_agent` must produce results identical to today (order, dedupe, caps, fixed-tool agents). Verified by a parity test.

---

## 2. Prerequisites

- Phase X (Extension Surface) implemented: `prismal/agents/extension/ports.py` with `ToolPort` and `conforms_to`. ✅ Present in repo.
- Access to `MCPClientManager.get_all_langchain_tools(capabilities=...)` and `get_server_status()`. ✅ Present.
- Access to `SkillsManager().get_active_tools()`. ✅ Present.
- Current `tool_registry.py` as the parity reference (stub_map, `_MAX_MCP_TOOLS`, `_MAX_TOTAL_TOOLS`, `_FIXED_TOOL_AGENTS`). ✅ Present.

---

## 3. Implementation Phases

### PHASE Y1 — `ToolProviderPort`

#### Y1-01 — Declare the port
- [x] Add `ToolProviderPort` (`@runtime_checkable Protocol`) to `prismal/agents/extension/ports.py` with `get_tools(*, agent_name, capabilities=None)`.
- [x] Add to `__all__` of `ports.py`.
- **Done:** `conforms_to(obj, ToolProviderPort)` distinguishes objects with/without `get_tools`. ✅ (`tests/unit/agents/extension/test_tool_provider_port.py`)

#### Y1-02 — Re-export
- [x] Re-export `ToolProviderPort` from `prismal/agents/extension/__init__.py`.
- **Done:** `from prismal.agents.extension import ToolProviderPort` works. ✅

---

### PHASE Y2 — Concrete providers

#### Y2-01 — `StubToolProvider`
- [x] Create `prismal/agents/extension/providers.py`.
- [x] Implement `StubToolProvider` migrating the `stub_map` of `get_tools_for_agent` (imports of `tools.py`, `SANDBOX_TOOLS`, `ML_PIPELINE_TOOLS` deferred).
- **Done:** returns the correct set per agent; unknown agents → `[]`. ✅ (`tests/unit/agents/extension/test_providers.py`)

#### Y2-02 — `McpToolProvider`
- [x] Implement a wrapper of `MCPClientManager` with cap `max_tools=60`; deferred import; exception capture → `[]`.
- **Done:** parity with `get_mcp_tools(capabilities=...)[:60]`. ✅

#### Y2-03 — `SkillToolProvider`
- [x] Implement a wrapper of `SkillsManager.get_active_tools()`; lazy manager; capture → `[]`.
- **Done:** parity with `get_skill_tools()`. ✅

#### Y2-04 — `CompositeToolProvider`
- [x] Implement the full merge strategy (fixed-tool agents → stubs only; live = MCP+Skills; filter stubs by name; truncate to `max_total=120`; log `tool_provider.tools_resolved`).
- [x] Per-sub-provider capture (`tool_provider.subprovider_error`).
- **Done:** parity test against the current implementation passes. ✅ (full byte-for-byte parity in Y8-02)

#### Y2-05 — `FakeToolProvider`
- [x] Implement a deterministic provider for tests (`mapping` + `default`).
- **Done:** no I/O, no heavy imports. ✅

#### Y2-06 — `build_default_tool_provider`
- [x] Implement the standard async assembly (optional MCP with `load_from_config`, Skills, Stubs → Composite).
- [x] Log active sub-providers (parity with `mcp_initialized`).
- **Done:** `provider = await build_default_tool_provider(settings)` returns a valid `CompositeToolProvider`. ✅

#### Y2-07 — Provider re-exports
- [x] Re-export the 5 classes + `build_default_tool_provider` from `extension/__init__.py`. ✅

---

### PHASE Y3 — Global injection (variant A) + registry refactor

#### Y3-01 — State and setters
- [x] Replace `_mcp_manager`/`_mcp_initialized`/`_mcp_lock` with `_provider: ToolProviderPort | None`.
- [x] Implement `set_tool_provider()` / `get_tool_provider()`.
- [x] Add the stub fallback (`_get_default_stub_provider()` — lazy singleton so as not to import `extension/` at the registry's import time).

#### Y3-02 — Delegation in `get_tools_for_agent`
- [x] Rewrite the body: delegate to `_provider`; fall back to stubs + warning `tool_registry.no_provider`; `raise ToolProviderNotConfigured` if `tool_provider_strict`.
- [x] **Keep the signature intact** (`agent_name`, `required_capabilities`).
- **Done:** the 20+ nodes keep working without touching them. ✅ (signature test + 1519 agents/mcp/core tests green)

#### Y3-03 — Remove mcp/skills imports
- [x] Remove `from prismal.mcp.client import MCPClientManager` and `from prismal.skills.manager import SkillsManager` from `tool_registry.py`.
- **Done:** `grep` does not find `prismal.mcp`/`prismal.skills` in `tool_registry.py`. ✅

#### Y3-04 — Deprecated shims
- [x] `init_mcp`, `get_mcp_tools`, `get_skill_tools` emit `DeprecationWarning` and delegate to providers (`init_mcp` injects the default composite if there is no provider; the getters resolve the Mcp/Skill sub-provider within the composite).
- [x] The shim tests use `pytest.warns(DeprecationWarning)` (no need to touch filterwarnings).
- **Done:** old calls keep working with a warning. ✅

#### Y3-05 — Architecture test
- [x] `tests/unit/agents/extension/test_no_mcp_skills_imports.py`: AST-walk of `prismal/agents/**` (excluding `extension/providers.py` and `skill_manager.py` — see note) → fails if it imports `prismal.mcp`/`prismal.skills`.
- **Done:** test green after the refactor. ✅
- **Note:** `skill_manager.py` (the skills-administration agent) imports `prismal.skills` by design — administering the subsystem IS its function; it is out of scope for Phase Y ("they are only wrapped, not rewritten") and is explicitly exempted with justification in the test.

---

### PHASE Y4 — Context injection (variant B, opt-in)

#### Y4-01 — Graph config
- [x] `get_async_compiled_graph(..., tool_provider=None)`: if `mode=="context"`, returns the singleton graph **bound** via `with_config({"configurable": {"tool_provider": ...}})` (per-session view; the graph and the checkpointer remain singletons). In `global` mode the parameter is ignored with warning `tool_provider_ignored_global_mode`.

#### Y4-02 — Per-node resolution
- [x] `resolve_provider(config)` reads from the config or falls back to the global (lives in `tool_registry.py` to avoid an import cycle; re-exported from `graph.py` as the SPEC dictates).
- [x] Access variant for nodes in context mode (helper `get_tools_for_agent_ctx(agent_name, config, required_capabilities)`).

#### Y4-03 — Isolation test
- [x] Two providers in parallel do not share tools; no shared global state.
- **Done:** isolation test green. ✅ (`tests/unit/agents/extension/test_context_provider.py` — 14 tests: resolve, ctx helper, isolation with `asyncio.gather`, independent per-session bindings)

---

### PHASE Y5 — Settings + Exceptions

#### Y5-01 — Settings
- [x] `tool_provider_mode: Literal["global","context"] = "global"`.
- [x] `tool_provider_strict: bool = False`.

#### Y5-02 — Exception
- [x] `ToolProviderNotConfigured` in `core/exceptions.py` (inherits from `ExtensionError(PrismalError)` — Phase X/Y family; name without `Error` suffix mandated by SPEC-TPI-010, with `noqa: N818`).

---

### PHASE Y6 — Observability

#### Y6-01 — Metrics
- [x] `prismal_tool_provider_resolved_total{provider}`, `prismal_tools_injected_total{agent}`, `prismal_tool_provider_fallback_total`, `prismal_tool_provider_subprovider_errors_total{provider}` — registered in `OTelManager._register_standard_metrics` (repo's OTel convention: `prismal.<name>_total`).

#### Y6-02 — Spans + logs
- [x] Span `prismal.tools.resolve` with `prismal.agent`, `prismal.tool_provider` (label `composite|mcp|skill|stub|fake`), `prismal.n_tools`, `prismal.fallback`, and `prismal.capabilities` — emitted by `_observed_get_tools()` in `tool_registry` (covers variant A, fallback, and the ctx branch of variant B).
- [x] Log `tool_provider.tools_resolved` with field parity (`agent`, `live`, `stubs_kept`, `total`) — verified with `structlog.testing.capture_logs`.
- **Done:** ✅ (`tests/unit/agents/extension/test_observability.py` — 8 tests)

---

### PHASE Y7 — Docs + Examples

#### Y7-01 — Documentation
- [x] `docs/tool-providers.md`: composition quickstart, variant A vs B (comparison table), custom provider, mock in tests (`FakeToolProvider` + reset fixture), parity table, deprecated shims, and observability. Linked from `docs/extension.md` (ports table) and `examples/README.md`.

#### Y7-02 — Runnable examples
- [x] `examples/tool_provider_host.py` — host-style composition + `set_tool_provider` (variant A) + per-session toolsets (variant B); optional MCP via `EXAMPLE_USE_MCP=1` so it runs offline by default. ✅ executed
- [x] `examples/tool_provider_custom.py` — custom provider that conforms to `ToolProviderPort` structurally (+ `conforms_to`). ✅ executed

---

### PHASE Y8 — Tests + Parity

#### Y8-01 — Provider unit tests
- [x] `test_providers.py`: each provider in isolation with mock managers (34 tests, `providers.py` at 100% coverage).

#### Y8-02 — Registry parity
- [x] `test_registry_delegation.py`: output of `get_tools_for_agent` with default composite == golden list derived from the current implementation (order, dedupe, caps, fixed agents) — `TestParityWithDefaultComposite` + `TestPolicyConstantsParity`.

#### Y8-03 — Port
- [x] `test_tool_provider_port.py` + `test_providers.py::TestConformanceAndReExports`: structural conformance of the 5 providers.

#### Y8-04 — Fallback / strict
- [x] No provider → stubs + warning; `strict=True` → `ToolProviderNotConfigured` (`test_registry_delegation.py::TestFallback`).

---

### HARDENING — Coverage, Host Migration, Audit

- [x] Coverage ≥ 85% in `providers.py` and the new paths of `tool_registry.py` — `providers.py` **100%**, `tool_registry.py` **85%** (the remaining lines are edge branches of the pre-existing `react_loop`; all Phase Y code covered).
- [ ] Migrate the startup of `prismal-sdk`/`prismal-web` from `init_mcp()` to `build_default_tool_provider + set_tool_provider` (coordinated outside this repo; recipe documented in `docs/tool-providers.md` §1 and §5 — shims with `DeprecationWarning` keep the old startup for 1 minor).
- [x] `ruff` + `mypy --strict` (239 files) + `bandit` (0 Medium/High by severity, parity with baseline) clean in `prismal/` and `tests/`. (20 pre-existing ruff issues remain in `examples/{multimodal,rag,subgraphs}` — prior to Phase Y, out of scope.)
- [x] `uv run pytest -m "not live_api"` — **2604 passed**; the remaining ~50 failures are pre-existing/flaky (memory/mongodb, rag/crag, scheduler, security), verified via `git stash` that they fail identically without the Phase Y changes. Zero regressions introduced.
- [x] Update `CLAUDE.md` (section "Tool provider injection (Phase Y)" + critical rule 9) and `README.md` (feature bullet, Phase Y section, ports table, architecture tree).

---

## 4. Inter-Task Dependencies

```
Y1 (port)
  └─▶ Y2 (providers)
        ├─▶ Y3 (variant A + registry refactor)  ──▶ Y3-05 (architecture test)
        │      └─▶ Y8-02 (parity)
        ├─▶ Y4 (variant B)  [requires Y5-01 settings]
        └─▶ Y2-06 build_default  ──▶ HARDENING (host migration)
Y5 (settings + exception) ──▶ Y3-02 (strict), Y4-01 (mode)
Y6 (observability)  [after Y3]
Y7 (docs/examples)   [after Y2, Y3]
Y8 (tests)           [cross-cutting; parity after Y3]
```

Critical path: **Y1 → Y2 → Y3 → Y8-02 (parity)**. Variant B (Y4) and observability (Y6) are parallelizable after Y3.

---

## 5. Risk and Mitigation Matrix

| Risk | Mitigation | Task |
|---|---|---|
| Loss of parity in the merge | Byte-for-byte golden test | Y8-02 |
| Residual import of mcp/skills in the core | AST architecture test | Y3-05 |
| Regression from missing provider | Fallback to stubs + warning; strict opt-in | Y3-02, Y5 |
| `DeprecationWarning` breaks `filterwarnings=error` | Ignore only our own warning in the shim tests | Y3-04 |
| Multi-tenant leakage | No global state; isolation test | Y4-03 |
| Host does not migrate startup | Deprecated shims 1 minor | Y3-04, HARDENING |

---

## 6. Definition of Done (Global for Phase Y)

- [x] `ToolProviderPort` declared, re-exported, with conformance of the 5 providers.
- [x] `providers.py` complete (Mcp/Skill/Stub/Composite/Fake + `build_default_tool_provider`).
- [x] `tool_registry.get_tools_for_agent` delegates; signature intact; no mcp/skills imports.
- [x] Architecture test (no forbidden imports) green.
- [x] Parity test green (identical output with default composite).
- [x] `_FIXED_TOOL_AGENTS` and token caps preserved.
- [x] Variant B available (opt-in) + isolation test.
- [x] Settings + `ToolProviderNotConfigured`.
- [x] Metrics + span + parity log.
- [x] `docs/tool-providers.md` + 2 runnable examples (verified).
- [x] Coverage ≥ 85% in new modules (providers 100%, tool_registry 85%).
- [x] Green suite within the phase scope: 2604 passed; ~50 pre-existing failures unrelated to Phase Y (verified against baseline with `git stash`). `ruff`/`mypy --strict`/`bandit` clean in `prismal/` and `tests/`.
- [x] `CLAUDE.md` + `README.md` updated.
- [ ] PR merged with review approved.
- [ ] Host migration (`prismal-sdk`/`prismal-web`) — external repo; recipe in `docs/tool-providers.md`.

---

## 7. Effort Estimate per Sub-Phase

| Sub-phase | Effort |
|---|---|
| Y1 — Port | 0.2 wk |
| Y2 — Providers | 1.0 wk |
| Y3 — Variant A + refactor | 0.8 wk |
| Y4 — Variant B | 1.0 wk |
| Y5 — Settings + exception | 0.2 wk |
| Y6 — Observability | 0.3 wk |
| Y7 — Docs + examples | 0.6 wk |
| Y8 — Tests + parity | 0.5 wk |
| Hardening | 0.5 wk |
| **Total** | **~5 wk** |

---

## 8. Operational Success Metrics

- 0 imports of `prismal.mcp`/`prismal.skills` in `prismal/agents/**` (excl. `extension/providers.py`).
- 0 regressions in the existing suite.
- 100% parity in `get_tools_for_agent` with the default composite.
- `prismal_tool_provider_fallback_total == 0` in deployments with a migrated host.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial implementation plan — tool provider injection |
