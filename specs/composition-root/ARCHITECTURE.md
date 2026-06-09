# Prismal Runtime Composition Root — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Related PLAN** | `specs/composition-root/PLAN.md` |
| **Related SPEC** | `specs/composition-root/SPEC.md` |
| **TASKS** | `specs/composition-root/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Context

After Phase Y (`ToolProviderPort` for MCP+Skills) and Phase Z (`VectorStorePort` for an interchangeable vector database), the core exposes several independent injection points. The missing host (`prismal-server`, FastAPI, multi-tenant by `org_id`) would need to wire them one by one. This document describes **Phase R — Runtime Composition Root**: a composition *facade* (`build_runtime`) that orchestrates all the core ports under a single contract (`RuntimeContext`), with config loaders and tenant resolution. It is consistent with the Phase X/Y/Z port family and with the ecosystem's layer model (core → server → sdk → dashboard) documented in the Obsidian notes.

---

## 2. Technical Objectives

- **OT-1:** A single `build_runtime(settings, *, org_id=None)` that composes tools (Y), vector store (Z), embeddings, checkpoint, and audit.
- **OT-2:** Do not duplicate logic: orchestrate `build_default_tool_provider`, `VectorStoreFactory`/provider, `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`.
- **OT-3:** Formalize multi-tenancy by **collection** isolation (`org_id`), not by backend.
- **OT-4:** Coordinated lifecycle (`aclose()` / async context manager).
- **OT-5:** Two modes: global (injects singletons) and context (per-session context without global state).
- **OT-6:** Maintain backward-compat: the individual injection of Y/Z still valid.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram

```
                        prismal-server (FastAPI, lifespan)         ← HOST
                                   │  await build_runtime(settings, org_id=?)
                                   ▼
        ┌──────────────────────  prismal/composition.py  ──────────────────────┐
        │  build_runtime():                                                     │
        │    1. RuntimeConfig = resolve(settings, org_id, overrides)            │
        │    2. tool_provider   = build_default_tool_provider(settings)   [Y]   │
        │    3. vstore_provider = VectorStoreFactory / provider(settings) [Z]   │
        │    4. embeddings      = EmbeddingsFactory.create(settings)            │
        │    5. checkpointer    = build_checkpointer(settings)                  │
        │    6. audit           = AuditLogger(...)                              │
        │    7. (global mode) set_tool_provider(...) ; set_vector_store_provider│
        │       (context mode) leaves everything inside the RuntimeContext     │
        │    → RuntimeContext(tool_provider, vstore_provider, embeddings,       │
        │                     checkpointer, audit, org_id, aclose())           │
        └───────────────────────────────────────────────────────────────────────┘
                                   │ consumes
                  prismal core (agents, rag, memory)  ← only uses the ports
```

### 3.2 Layer Diagram (ecosystem)

```
┌───────────────────────────────────────────────────────────┐
│ prismal-dashboard (Reflex)  → EDITS config (MCP/skills/    │
│                               settings/vector backend)     │
└───────────────┬───────────────────────────────────────────┘
                │ persists config
┌───────────────▼───────────────────────────────────────────┐
│ prismal-server (FastAPI)  → COMPOSES: build_runtime(...)   │
│   lifespan startup → build_runtime ; shutdown → aclose()   │
│   multi-tenant: build_runtime(org_id=...)                  │
└───────────────┬───────────────────────────────────────────┘
                │ build_runtime
┌───────────────▼───────────────────────────────────────────┐
│ prismal core                                              │
│   composition.py (R) ── orchestrates ──► Y (tools) · Z (vstore)│
│                                       · embeddings · ckpt  │
│                                       · audit              │
│   ports in extension/ports.py ; consumers in agents/      │
│   rag/ memory/ (no signature changes)                     │
└───────────────────────────────────────────────────────────┘
        prismal-sdk = CLIENT of the API (does not compose, does not inject)
```

### 3.3 Components

#### R1 — `RuntimeContext` / `RuntimeConfig` (`prismal/composition.py`)
- `RuntimeConfig`: resolved immutable view — `mcp_config_path`, `skills_source`, `vector_store_backend`, `collection_base`, `org_id`, applied overrides.
- `RuntimeContext`: dataclass with `tool_provider: ToolProviderPort`, `vector_store_provider`, `embeddings: EmbeddingsPort`, `checkpointer: CheckpointPort`, `audit: AuditPort`, `org_id: str | None`, and `async aclose()`.

#### R2 — `build_runtime` (composition root)
`async def build_runtime(settings=None, *, org_id=None, overrides=None, mode=None) -> RuntimeContext`. Resolves config (R3), composes sub-ports reusing the Y/Z builders and the existing factories, and depending on `mode`:
- **global:** `set_tool_provider(tp)` + `set_vector_store_provider(vsp)` (process singletons).
- **context:** does not touch globals; the `RuntimeContext` is passed to `get_async_compiled_graph(tool_provider=..., vector_store_provider=...)` (bound per session).

#### R3 — Config loaders (`prismal/composition/config_sources.py`)
- `load_mcp_config(path) -> McpConfig`: parses `config/mcp_servers.yaml`.
- `resolve_skills_source(settings)`: directories/state of active skills.
- `resolve_vector_store(settings, org_id)`: backend + derived `collection_name`.
- `apply_org_overrides(settings, org_id, overrides) -> Settings`: effective per-tenant settings.

#### R4 — Tenant resolution
`collection_for(base, org_id) -> str` = `base` if `org_id is None`, otherwise `f"{base}_{org_id}"`. It is applied **identically** in RAG (`RAGEngine`) and memory (`LongTermMemory`) so that a tenant sees its collection in both.

#### R5 — Modes
`settings.runtime_mode: Literal["global","context"] = "global"`. Mirror of the Phase Y/Z modes; the composition root unifies them into a single parameter.

#### R6 — Lifecycle
`RuntimeContext.aclose()` closes MCP (disconnects servers), vector store (closes server connections if applicable), and checkpointer. `build_runtime` is also usable as `async with`.

### 3.4 Data Flows

#### Flow R-A: Global startup (prismal-server lifespan)
```
1. startup → ctx = await build_runtime(get_settings())     # mode=global
2. build_runtime composes Y+Z+emb+ckpt+audit
3. set_tool_provider / set_vector_store_provider (singletons)
4. get_async_compiled_graph() uses the injected providers
5. shutdown → await ctx.aclose()
```

#### Flow R-B: Per-tenant (context)
```
1. request org=acme → ctx = await build_runtime(settings, org_id="acme")   # mode=context
2. resolve_vector_store → collection_name = "<base>_acme"
3. graph = await get_async_compiled_graph(tool_provider=ctx.tool_provider,
                                          vector_store_provider=ctx.vector_store_provider)
4. isolated execution; another tenant in parallel does not share state
5. end of session → await ctx.aclose()  (or reuse shareable resources)
```

---

## 4. Design Decisions

### DD-CR-001: Orchestrate, do not reimplement
`build_runtime` **calls** `build_default_tool_provider` (Y) and `VectorStoreFactory`/provider (Z); it does not reproduce their logic. A test verifies there is no duplication (the sub-builders are the sources of truth).

### DD-CR-002: `RuntimeContext` as a container, not a God-object
The context only **groups references** to already-composed ports + `aclose()`. It adds no business behavior; the RAG/agent patterns keep consuming the ports directly.

### DD-CR-003: Multi-tenant by collection, backend per process
Data isolation by `org_id` = derived `collection_name` (cheap, already supported by the constructors). The vector backend is fixed per process (Phase Z). Per-tenant backend is out of scope (a stateful resource, singleton memory).

### DD-CR-004: Two modes inherited from Y/Z, unified
Instead of exposing `tool_provider_mode` and a future `vector_store_mode` separately, the composition root exposes **one** `runtime_mode` that it propagates to both. Less config surface.

### DD-CR-005: The feature lives in the core, the server only calls it
`prismal/composition.py` belongs to the core (publishable); `prismal-server` contributes the *lifespan* and the persisted config. This way the contract exists before the server (which is "Planned").

### DD-CR-006: Full backward-compat
Anyone already using `set_tool_provider`/`VectorStoreFactory` directly keeps working the same. `build_runtime` is **opt-in**; it does not change defaults or the signatures of nodes/patterns.

### DD-CR-007: Explicit lifecycle
`aclose()` avoids hanging connections (MCP, Qdrant/pg). Async context manager for ergonomic use in tests and scripts.

---

## 5. Code Structure

```
prismal/
├── composition.py                 # NEW: build_runtime, RuntimeContext, RuntimeConfig
├── composition/                   # (if it grows) submodule
│   └── config_sources.py          # NEW: loaders MCP/skills/vstore/overrides
├── agents/
│   ├── extension/providers.py     # Y: build_default_tool_provider (reused)
│   └── graph.py                   # accepts vector_store_provider in context (Z)
├── rag/
│   ├── vector_store_factory.py    # Z: VectorStoreFactory (reused)
│   └── ...
├── core/
│   ├── config.py                  # + runtime_mode (unifies modes)
│   └── exceptions.py              # + RuntimeCompositionError
docs/composition-root.md           # NEW
examples/composition_root.py       # NEW
tests/unit/composition/            # composition root tests
```

### Applied Patterns
- **Composition Root** (classic DI pattern: a single place assembles the object graph).
- **Facade** (`build_runtime` over existing builders).
- **Hexagonal** (orchestrates ports; does not know concrete implementations beyond the builders).

### Error Handling
- Failure of a sub-builder → `RuntimeCompositionError` with the cause (which port failed), after attempting `aclose()` of what was already created (do not leave resources hanging).
- Invalid config (bad MCP yaml, unknown backend) → clear error from the corresponding loader (reuses those of Y/Z).

---

## 6. Security

- **Isolation between tenants:** in context mode, two `RuntimeContext`s do not share state; the vector collections are separated by `org_id`. Isolation test mandatory.
- **Credentials:** DSN/keys of server backends and MCP are not logged; `RuntimeConfig` marks sensitive fields as secrets.
- **The L1–L5 barriers do not move:** the composition root composes; tool execution still passes through `react_loop` + the `@prismal_node` middleware.

---

## 7. Observability

- Span `prismal.composition.build_runtime` with `mode`, `org_id`, `vector_store_backend`, `n_mcp_servers`, `n_skills`.
- Log `composition.runtime_built` (parity with `mcp_initialized`/`vector_store.created`).
- Metric `prismal_runtime_built_total{mode}`, `prismal_runtime_active{}` (gauge), `prismal_runtime_teardown_total`.

---

## 8. Testing Strategy

- **Composition:** `build_runtime` produces a `RuntimeContext` with the 5 ports non-null; each sub-port is the one produced by the Y/Z builders (not a reimplementation).
- **Tenant:** `collection_for(base, org)` correct; RAG and memory of the same tenant see the same collection; different tenants, different.
- **Isolation (context):** two runtimes in parallel (`asyncio.gather`) do not share providers or collection.
- **Lifecycle:** `aclose()` closes MCP/vstore/checkpointer (mocks that verify the call); async context manager.
- **Backward-compat:** using `set_tool_provider`/`VectorStoreFactory` without `build_runtime` keeps working.
- **Fakes:** `build_test_runtime` assembles a context with `FakeToolProvider`/`FakeVectorStore`.

---

## 9. Rollout Plan

1. R1–R2 (context + global build_runtime) — additive; the server can start using it.
2. R3–R4 (loaders + tenant) — formalizes config and multi-tenancy.
3. R5–R6 (modes + lifecycle).
4. R7–R8 (contracts + tests + docs).

Backout: `build_runtime` is opt-in; removing its use reverts to the individual injection of Y/Z.

---

## 10. Open Questions

- **PA-1:** Are `embeddings`/`checkpointer` shared between tenants in context mode (recommended) or isolated? (Proposal: share; only the collection changes.)
- **PA-2:** Should `RuntimeContext` also expose the compiled graph, or does the server request it separately? (Proposal: separately; the context groups ports, not the graph.)
- **PA-3:** Should `config_sources` support per-org overrides from a DB (server) or only from settings/env? (Proposal: accept an `overrides` dict; the server decides the source.)
- **PA-4:** Pool of runtimes per tenant for reuse? (Future; depends on real load.)

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial technical design — composition root |
