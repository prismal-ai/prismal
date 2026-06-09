# Prismal — Runtime Composition Root (unified host injection and configuration)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Phase** | R — Runtime Composition Root (capstone of Phase Y + Phase Z) |
| **Depends on** | Phase Y (`specs/tool-provider-injection/`), Phase Z (`specs/vector-store-port/`) |

---

## 1. Executive Summary

Phases Y and Z invert two core dependencies into hexagonal ports: `ToolProviderPort` (MCP + Skills) and `VectorStorePort` (interchangeable vector database). But each one exposes its own injection point (`set_tool_provider()`, `set_vector_store_provider()`/`VectorStoreFactory`), its own config, and its own lifecycle. Today **there is no single point where the host (`prismal-server`) composes the entire runtime** from `settings` + tenant context. Without that point, building the missing components of the ecosystem (`prismal-server`, `prismal-dashboard`) means rewiring each port by hand in each component.

This phase defines a **Runtime Composition Root** in the core: `build_runtime(settings, *, org_id=None) -> RuntimeContext`, a single *facade* that **composes and injects** all of the core's ports — tool provider (Y), vector store provider (Z), embeddings, checkpointer, and audit — reading the existing config sources (`config/mcp_servers.yaml`, skills state, `vector_store_backend`) and applying per-tenant resolution (`org_id`). It is the contract that `prismal-server` invokes in its *lifespan* and that `prismal-dashboard` uses to read/edit config.

The change is **additive and opt-in**: anyone who does not use the composition root keeps the individual injection points of Y/Z or the defaults. The `RuntimeContext` does not replace the ports — it **orchestrates** them. Result: `prismal-server` starts with **one call**, multi-tenancy is formalized (per-`org_id` collection isolation), and the ecosystem has a single versioned composition contract.

---

## 2. Context and Problem

### 2.1 Current Situation

- **Fragmented injection.** After Y/Z there are ≥ 5 pieces the host must wire separately: `set_tool_provider(build_default_tool_provider(settings))`, vector store (factory or `set_vector_store_provider`), embeddings (`EmbeddingsFactory`), checkpointer (`build_checkpointer`), audit (`AuditLogger`). There is no single assembly.
- **Scattered config.** MCP is read from `config/mcp_servers.yaml`; skills from directories; vector store from env/`settings`; each subsystem with its own loader. The host must know all of them.
- **Informal multi-tenancy.** The roadmap calls for per-`org_id` Chroma collection isolation, but there is no point that derives the `collection_name` per tenant consistently for RAG **and** memory.
- **Missing components blocked.** `prismal-server` and `prismal-dashboard` are "Planned". Without a composition contract, each one would reinvent the wiring → divergence and bugs.
- **The lifecycle has no single owner.** MCP connection, vector store opening, checkpointer: today they are initialized at different moments without a coordinated *teardown*.

### 2.2 Problem

1. **Without unified composition**, the host repeats and can get wrong the wiring of 5+ ports.
2. **Without centralized loaders**, the config (MCP yaml, skills, vector backend, per-org overrides) is interpreted inconsistently between server and dashboard.
3. **Without tenant resolution**, per-`org_id` isolation is implemented ad-hoc at each call site.
4. **Without a coordinated lifecycle**, there is no clean `startup`/`shutdown` (hanging connections, unreleased resources).

### 2.3 Opportunity

The pieces already exist: `build_default_tool_provider` (Y), `VectorStoreFactory`/provider (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`, `get_settings()`. What is missing is **an assembler** that unites them under a contract (`RuntimeContext`) with loaders and tenant resolution. Low effort (a facade module that orchestrates the already-existing pieces), high impact: it unblocks `prismal-server`/`prismal-dashboard` and formalizes multi-tenancy.

---

## 3. Target Users

### Persona 1: `prismal-server` (Platform Host)
- **Need:** Start the runtime with one call in the *lifespan*; obtain the ready graph and the injected context; close cleanly on *shutdown*.
- **Frequency:** 1 time per startup (global mode) or 1 per session/tenant (context mode).

### Persona 2: Multi-Tenant Operator
- **Need:** That each `org_id` is isolated (its own vector collection, matching providers) without shared state between tenants.
- **Frequency:** Per request/tenant.

### Persona 3: `prismal-dashboard` (Config UI)
- **Need:** Read/edit the config that the composition root consumes (MCP servers, active skills, vector backend, settings) with a stable schema.
- **Frequency:** Admin interaction.

### Persona 4: Library User / Test Author
- **Need:** Compose a test runtime with fakes (`FakeToolProvider`, `FakeVectorStore`) in one call, without standing up services.
- **Frequency:** Daily.

---

## 4. Objectives and Success Metrics

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Host startup in 1 call | LoC of wiring in `prismal-server` lifespan | ≤ 5 | Phase R |
| Unified composition | Ports wired by `build_runtime` | tools, vector store, embeddings, checkpoint, audit | Phase R |
| Formal multi-tenant | Per-`org_id` collection isolation for RAG **and** memory | Yes | Phase R |
| Coordinated lifecycle | `RuntimeContext` with `aclose()`/teardown | Yes | Phase R |
| Backward compatibility | Individual Y/Z injection still valid | 100% | Global |
| Testability | Test runtime with fakes in 1 call | Yes | Phase R |
| Coverage | Branch coverage of the new module | ≥ 85% | Global |

---

## 5. Scope

### 5.1 In Scope (Phase R)

**R1 — `RuntimeConfig` + `RuntimeContext` (`prismal/composition.py`):**
- [x] `RuntimeContext`: container of the composed ports (tool provider, vector store provider, embeddings, checkpointer, audit) + `org_id` + `aclose()`.
- [x] `RuntimeConfig`: resolved view of config (paths, backend, mcp config path, skills source, per-org overrides).

**R2 — `build_runtime` (composition root):**
- [x] `async build_runtime(settings=None, *, org_id=None, overrides=None) -> RuntimeContext` that composes all ports reusing `build_default_tool_provider` (Y), `VectorStoreFactory`/provider (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`.
- [x] Injects the global providers (`set_tool_provider`, `set_vector_store_provider`) or returns a per-session bound context (context mode).

**R3 — Centralized config loaders (`prismal/composition/config_sources.py`):**
- [x] `load_mcp_config(path)`, `resolve_skills_source(settings)`, `resolve_vector_store(settings, org_id)`, `apply_org_overrides(settings, org_id, overrides)`.

**R4 — Tenant resolution:**
- [x] Derive `collection_name` per `org_id` for RAG and memory consistently (`f"{base}_{org_id}"`).
- [x] Per-tenant provider policy (tools/skills) opt-in.

**R5 — Global vs context modes:**
- [x] `runtime_mode: Literal["global","context"]`: global = injects singletons; context = `RuntimeContext` per session without global state (aligned with Phase Y var. B and Phase Z var. B).

**R6 — Lifecycle:**
- [x] `RuntimeContext.aclose()` closes MCP, vector store, checkpointer; `build_runtime` usable as an async context manager.

**R7 — Host and dashboard contract:**
- [x] Document the *lifespan* of `prismal-server` and the config schema that `prismal-dashboard` reads/edits.

**R8 — Tests + example:**
- [x] `build_test_runtime(...)` with fakes; example `examples/composition_root.py`; docs `docs/composition-root.md`.

### 5.2 Out of Scope

- Implementing `prismal-server` / `prismal-dashboard` themselves (they live in other packages; here only the contract they consume).
- Rewriting Y/Z (they are reused; this feature orchestrates them).
- Per-tenant with a **different vector backend per org** (expensive; the backend stays per-process, isolation by collection).
- Management of host secrets/credentials (server's responsibility).

### 5.3 Future Considerations

- Pool of runtimes per tenant with *eviction* (if a per-org backend is required).
- Hot-reload of config (MCP/skills) without restart.
- `prismal.composition` as an entry point so plugins can contribute sub-providers.

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-CR-001 | `RuntimeContext` groups tool provider, vector store provider, embeddings, checkpointer, audit | `MUST` |
| RF-CR-002 | `build_runtime(settings, *, org_id=None)` composes all ports in one call | `MUST` |
| RF-CR-003 | Reuses `build_default_tool_provider` (Y) and `VectorStoreFactory`/provider (Z), without duplicating logic | `MUST` |
| RF-CR-004 | Global mode (injects singletons) and context mode (per-session context) | `MUST` |
| RF-CR-005 | Centralized loaders for MCP yaml, skills, vector store, per-org overrides | `MUST` |
| RF-CR-006 | Resolution of `collection_name` per `org_id` for RAG and memory | `MUST` |
| RF-CR-007 | `RuntimeContext.aclose()` releases resources (MCP, vector store, checkpointer) | `MUST` |
| RF-CR-008 | `runtime_mode` + `org_id` in settings/parameters | `SHOULD` |
| RF-CR-009 | `build_test_runtime` with fakes for tests | `SHOULD` |
| RF-CR-010 | Backward-compat: the individual injection of Y/Z still works | `MUST` |
| RF-CR-011 | Documented contract for `prismal-server` (lifespan) and `prismal-dashboard` (config) | `SHOULD` |
| RF-CR-012 | Observability: composition span/log with backend/providers/org | `SHOULD` |

---

## 7. Non-Functional Requirements

### Performance
- `build_runtime` global: 1 time per startup (cost of MCP connection + store opening).
- Context mode: reuse shareable resources (embeddings, checkpointer) between tenants; only the `collection_name` changes.

### Security
- Strict isolation between tenants: no `RuntimeContext` of one `org_id` exposes another's data (separate collection; no shared global state in context mode).
- Server backend (Qdrant/pg) and MCP credentials are not logged.
- The L1–L5 layers still apply downstream (the composition root only composes, it does not execute tools).

### Compatibility
- `prismal/` PEP 420 namespace. Additive: without touching the signature of the nodes or the RAG patterns.
- `filterwarnings=error`: imports of optional subsystems deferred.

### Maintainability
- Coverage ≥ 85%; `ruff`/`mypy --strict`/`bandit` clean.
- `RuntimeContext`/`build_runtime` are versioned public API (SemVer).

---

## 8. Constraints and Dependencies

| Dependency | Type | Use |
|---|---|---|
| Phase Y — `build_default_tool_provider`, `set_tool_provider` | Prerequisite | Sub-composition of tools |
| Phase Z — `VectorStoreFactory`, `set_vector_store_provider` | Prerequisite | Sub-composition of vector store |
| `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger` | Existing | Sub-composition |
| `config/mcp_servers.yaml`, skills dirs, `settings` | Existing | Config sources |

Ordering constraint: **Phase R requires Y and Z** (at least their ports and factories). It can be specified in parallel, but the implementation comes afterward.

---

## 9. User Stories

**US-CR-001:** As `prismal-server`, I start the runtime in the lifespan with one call.
```python
async def lifespan(app):
    ctx = await build_runtime(get_settings())     # composes and injects everything
    app.state.graph = await get_async_compiled_graph()
    yield
    await ctx.aclose()
```

**US-CR-002:** As a Multi-Tenant Operator, each tenant is isolated.
```python
ctx = await build_runtime(settings, org_id="acme")   # collection = "<base>_acme"
```

**US-CR-003:** As a dashboard, I read/edit the config that the runtime consumes.
- [x] Stable schema of MCP servers / skills / vector_store_backend / settings.

**US-CR-004:** As a Test Author, I compose a runtime with fakes.
```python
ctx = build_test_runtime(tool_provider=FakeToolProvider({...}),
                         vector_store=FakeVectorStore({...}))
```

---

## 10. Risks and Mitigations

| Risk | Prob. | Impact | Mitigation |
|---|---|---|---|
| The composition root duplicates Y/Z logic | Medium | Medium | Strictly orchestrates; reuses `build_default_*`/factories; non-duplication test |
| Leakage between tenants | Low | Critical | Context mode without global state; per-collection isolation; isolation test |
| Unreleased resources | Medium | Medium | `aclose()` + context manager; teardown test |
| Coupling with a non-existent `prismal-server` | Medium | Low | The feature lives in the core; the server only calls it. Documented contract |
| Per-tenant backend tempts over-engineering | Medium | Medium | Explicit out of scope; per-collection isolation covers the roadmap |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| R1 — Context/Config | 0.4 wk | `RuntimeContext`, `RuntimeConfig` |
| R2 — build_runtime | 0.6 wk | global + context composition |
| R3 — Loaders | 0.5 wk | centralized config_sources |
| R4 — Tenant resolution | 0.3 wk | collection_name per org (RAG+memory) |
| R5 — Modes | 0.2 wk | global/context |
| R6 — Lifecycle | 0.3 wk | aclose + context manager |
| R7 — Host/dashboard contracts | 0.3 wk | contract docs |
| R8 — Tests + example | 0.5 wk | fakes + example + docs |
| Hardening | 0.4 wk | coverage, mypy/bandit, validation |
| **Total** | **~3.5 wk** | composition root ready for `prismal-server`/`dashboard` |

---

## 12. Definition of Done (Global for Phase R)

- [x] `RuntimeContext` + `RuntimeConfig` + `build_runtime` implemented.
- [x] Composes tools (Y), vector store (Z), embeddings, checkpointer, audit — without duplicating logic.
- [x] Global and context modes; resolution of `collection_name` per `org_id` (RAG + memory).
- [x] `aclose()` releases resources; usable as an async context manager.
- [x] `build_test_runtime` with fakes; individual Y/Z injection still valid.
- [x] Documented contract for `prismal-server` (lifespan) and `prismal-dashboard` (config).
- [x] `docs/composition-root.md` + `examples/composition_root.py`.
- [x] Coverage ≥ 85%; `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [x] `CLAUDE.md` + `README.md` + Obsidian notes updated.
- [x] PR merged with review.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial version — composition root unifying Phase Y + Z |
| 1.1 | 2026-06-09 | Ernesto Crespo | **IMPLEMENTED** in v3.1.3 — see `CHANGELOG.md` and `docs/composition-root.md`. |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
| Security Lead | — | | ☐ Pending |
