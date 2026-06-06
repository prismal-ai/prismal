# Prismal Runtime Composition Root — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/composition-root/PLAN.md` |
| **Architecture** | `specs/composition-root/ARCHITECTURE.md` |
| **SPEC** | `specs/composition-root/SPEC.md` |
| **Depends on** | Phase Y (`specs/tool-provider-injection/`), Phase Z (`specs/vector-store-port/`) |

---

## 1. Implementation Summary

Phase R adds a composition *facade* (`build_runtime`) that orchestrates the ports already provided by Y and Z plus embeddings/checkpoint/audit, with config loaders and tenant resolution. **Additive and opt-in**: it does not change the signatures of nodes or the defaults; anyone who does not use it keeps the individual injection.

Guiding principle: **orchestrate, do not reimplement**. The sources of truth are `build_default_tool_provider` (Y) and `VectorStoreFactory`/provider (Z).

---

## 2. Prerequisites

- Phase Y implemented: `ToolProviderPort`, `build_default_tool_provider`, `set_tool_provider`. (Status in `specs/tool-provider-injection/TASKS.md`.)
- Phase Z implemented: `VectorStorePort`, `VectorStoreFactory`, `set_vector_store_provider`, `get_async_compiled_graph(vector_store_provider=...)`.
- Existing: `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`, `get_settings()`.

> If Y/Z are not yet 100% done, R is specified now but its implementation comes after.

---

## 3. Implementation Phases

### PHASE R1 — `RuntimeConfig` / `RuntimeContext`
#### R1-01 — Types
- [ ] Create `prismal/composition.py` with `RuntimeConfig` (frozen) and `RuntimeContext` (dataclass + `aclose` + async context manager).
- **Done:** `RuntimeContext` groups the 5 ports + `org_id`; `aclose` idempotent.

### PHASE R2 — `build_runtime`
#### R2-01 — Global composition
- [ ] Implement `build_runtime(settings, *, org_id, overrides, mode)` reusing the Y/Z builders + EmbeddingsFactory + build_checkpointer + AuditLogger.
- [ ] Global mode: `set_tool_provider` + `set_vector_store_provider`.
- [ ] On failure: `aclose()` of what was created + `RuntimeCompositionError`.
#### R2-02 — Context mode
- [ ] Context mode: does not touch globals; the context is passed to `get_async_compiled_graph(...)`.
- [ ] `build_test_runtime(...)` with fakes.
- **Done:** `ctx = await build_runtime(settings)` returns a context with 5 non-null ports.

### PHASE R3 — Config loaders
#### R3-01 — `config_sources.py`
- [ ] `load_mcp_config`, `resolve_skills_source`, `resolve_vector_store`, `apply_org_overrides`, `collection_for`.
- **Done:** pure loaders (sync), no connection, tested.

### PHASE R4 — Tenant resolution
#### R4-01 — collection_for in RAG and memory
- [ ] `collection_for(base, org_id)` applied consistently when building RAG (`RAGEngine`) and memory (`LongTermMemory`) from the runtime.
- **Done:** same tenant → same collection in RAG and memory; different tenant → different.

### PHASE R5 — Settings (unified mode)
#### R5-01 — `runtime_mode`
- [ ] `settings.runtime_mode: Literal["global","context"] = "global"`; `build_runtime` propagates it to Y and Z.
- [ ] Backward-compat: derive from `tool_provider_mode` if set.

### PHASE R6 — Lifecycle
#### R6-01 — aclose + context manager
- [ ] `aclose()` closes MCP/vstore/checkpointer; `async with build_runtime(...)`.
- **Done:** teardown test verifies the close calls.

### PHASE R7 — Exception + graph integration
#### R7-01 — `RuntimeCompositionError`
- [ ] In `core/exceptions.py`.
#### R7-02 — graph accepts context providers
- [ ] Confirm/adjust `get_async_compiled_graph(tool_provider=, vector_store_provider=)` (consistency with Z).

### PHASE R8 — Tests + Docs + Example
#### R8-01 — Tests
- [ ] Composition (5 ports), non-duplication (uses Y/Z builders), tenant (collection_for), context isolation (`asyncio.gather`), lifecycle (aclose), backward-compat (without build_runtime).
#### R8-02 — Docs + example
- [ ] `docs/composition-root.md` (server lifespan + dashboard contract); `examples/composition_root.py`.

### HARDENING
- [ ] Coverage ≥ 85% in `composition*`; `ruff`/`mypy --strict`/`bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] `CLAUDE.md` + `README.md` + Obsidian notes updated with the composition root.

---

## 4. Inter-Task Dependencies

```
(Phase Y + Phase Z implemented)
   └─▶ R1 (types)
         └─▶ R2 (build_runtime)  ──┬─▶ R4 (tenant)   ──▶ R8 (tests)
                                   ├─▶ R5 (runtime_mode)
                                   └─▶ R6 (lifecycle)
   R3 (loaders) ──▶ R2 (build_runtime uses them)
   R7 (exception + graph) ──▶ R2/R8
   R8 (docs/example) [last]
```

Critical path: **Y+Z → R1 → R3 → R2 → R8**.

---

## 5. Tasks ↔ Requirements Matrix

| Task | RF covered |
|---|---|
| R1 | RF-CR-001 |
| R2 | RF-CR-002, RF-CR-003, RF-CR-004, RF-CR-009, RF-CR-010 |
| R3 | RF-CR-005 |
| R4 | RF-CR-006 |
| R5 | RF-CR-004, RF-CR-008 |
| R6 | RF-CR-007 |
| R7 | RF-CR-002 (errors), RF-CR-008 |
| R8 | RF-CR-009, RF-CR-011, RF-CR-012 |

Coverage: RF-CR-001..012 mapped.

---

## 6. Risk Matrix

| Risk | Mitigation | Task |
|---|---|---|
| Duplicate Y/Z logic | Orchestrate builders; non-duplication test | R2, R8 |
| Leakage between tenants | Context mode without globals; per-collection isolation; test | R2, R4, R8 |
| Hanging resources | aclose + context manager; teardown test | R6, R8 |
| Coupling with non-existent server | Feature in core; server only calls; documented contract | R8 |
| Incomplete Y/Z | Specify now, implement behind Y/Z | Prerequisites |

---

## 7. Definition of Done (Global for Phase R)

- [ ] `RuntimeContext`/`RuntimeConfig`/`build_runtime` implemented; composes 5 ports without duplicating Y/Z.
- [ ] Global and context modes; `runtime_mode` in settings; `collection_for` per `org_id` in RAG+memory.
- [ ] `aclose()` + async context manager; `RuntimeCompositionError`.
- [ ] `build_test_runtime` with fakes; backward-compat (individual injection still valid).
- [ ] Host contract (`prismal-server` lifespan) and dashboard documented.
- [ ] `docs/composition-root.md` + `examples/composition_root.py`.
- [ ] Coverage ≥ 85%; `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` + Obsidian notes updated.
- [ ] PR merged with review.

---

## 8. Effort Estimate

| Sub-phase | Effort |
|---|---|
| R1 Types | 0.4 wk |
| R2 build_runtime | 0.6 wk |
| R3 Loaders | 0.5 wk |
| R4 Tenant | 0.3 wk |
| R5 Settings | 0.2 wk |
| R6 Lifecycle | 0.3 wk |
| R7 Exception + graph | 0.3 wk |
| R8 Tests + docs | 0.5 wk |
| Hardening | 0.4 wk |
| **Total** | **~3.5 wk** |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial implementation plan — composition root |
