# Prismal Vector Store Port — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/vector-store-port/PLAN.md` |
| **Architecture** | `specs/vector-store-port/ARCHITECTURE.md` |
| **SPEC** | `specs/vector-store-port/SPEC.md` |

---

## 1. Implementation Summary

Phase Z turns `ChromaVectorStore` (concrete class) into a `VectorStorePort` with interchangeable adapters, selectable by `settings.vector_store_backend`. **Chroma stays the default.** The work is mostly:

- **Additive:** port, factory, 4 new adapters, extras, settings, exceptions, docs, tests.
- **Relocation with shim:** `rag/vector_store.py` → `rag/stores/chroma.py` (backward-compatible re-export).
- **Retyping without logic:** RAG + memory consumers change `ChromaVectorStore` → `VectorStorePort`.

Guiding principle: **behavioral parity with default chroma** + **score contract `[0,1]`** verified against Chroma as the reference.

---

## 2. Prerequisites

- Phase X/Y port family in `extension/ports.py` (`EmbeddingsPort`, etc.). ✅ Present.
- `EmbeddingsFactory.create(settings)` as the mirror of the new factory. ✅ Present.
- Current `ChromaVectorStore` as the API and `[0,1]` score reference. ✅ Present.
- Constructor injection already present in RAG + memory consumers. ✅ Present.

---

## 3. Implementation Phases

### PHASE Z1 — `VectorStorePort`
#### Z1-01 — Declare the port
- [ ] Add `VectorStorePort` (`@runtime_checkable Protocol`) to `extension/ports.py` with `collection_name`, `add_documents`, `similarity_search`, `delete_by_source`, `delete_collection`.
- [ ] `__all__` + re-export from `extension/__init__.py`.
- **Done:** `conforms_to(ChromaVectorStore(), VectorStorePort)` is `True`.

### PHASE Z2 — Score contract
#### Z2-01 — Define contract + helpers
- [ ] Document `score ∈ [0,1]` higher=better in the port's docstring.
- [ ] Create `rag/stores/_normalize.py` with `cosine_identity`, `from_l2`, `from_distance` helpers.
#### Z2-02 — Reference test
- [ ] Test that fixes the corpus and captures Chroma's order/score as the *golden* for parity (Z7-02).
- **Done:** reproducible reference.

### PHASE Z3 — Adapters
#### Z3-01 — Relocate Chroma (default) + shim
- [ ] Move `ChromaVectorStore`/`ChromaStoreError` to `rag/stores/chroma.py`.
- [ ] `rag/vector_store.py` re-exports (shim) → existing imports do not break.
- [ ] `ChromaStoreError` subclasses `VectorStoreError`.
- **Done:** existing suite green with no behavior change.
#### Z3-02 — `LanceDBVectorStore` (`[lancedb]`)
- [ ] Implement the embedded adapter; deferred import; normalize score; translate `delete_by_source`.
#### Z3-03 — `SqliteVecVectorStore` (`[sqlite-vec]`)
- [ ] Implement the embedded adapter; resolve via SQL/extension or LangChain integration (PA-2).
#### Z3-04 — `QdrantVectorStore` (`[qdrant]`)
- [ ] Implement the embedded/server adapter; auth from settings; normalize score.
#### Z3-05 — `PgVectorStore` (`[pgvector]`)
- [ ] Implement the server adapter (DSN); normalize distance `<=>`/`<->`.
- **Done (Z3):** the 5 adapters conform to the port; each with its documented normalization.

### PHASE Z4 — Factory + Settings + Exceptions
#### Z4-01 — `VectorStoreFactory`
- [ ] Create `rag/vector_store_factory.py::VectorStoreFactory.create(settings, collection)`; deferred import per backend; mirror of `EmbeddingsFactory`.
- [ ] `FakeVectorStore` for tests.
#### Z4-02 — Settings
- [ ] `vector_store_backend` (default `chroma`), `vector_store_path`, `vector_store_url` (+ optional credentials).
- [ ] `chroma_path` as a backward-compatible alias when backend == chroma.
#### Z4-03 — Exceptions
- [ ] `VectorStoreError`, `VectorStoreBackendUnavailable` (message guiding to the extra).
- **Done:** `create()` selects the backend; absence of the extra → clear error.

### PHASE Z5 — Consumer retyping
#### Z5-01 — RAG
- [ ] `engine`, `hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `crag`: hint → `VectorStorePort`; default construction via factory.
#### Z5-02 — Memory
- [ ] `memory/long_term.py`, `memory/mongodb_store.py`: default via factory; hint → `VectorStorePort`.
- **Done:** `grep` does not find `ChromaVectorStore` type hints in consumers (only in the adapter). Suite green.

### PHASE Z6 — Extras
#### Z6-01 — `pyproject.toml`
- [ ] Extras `[lancedb]`, `[sqlite-vec]`, `[qdrant]`, `[pgvector]`; base without new mandatory deps; update `all` if applicable.
- [ ] `mypy` overrides for the new optional SDKs if needed.

### PHASE Z7 — Tests
#### Z7-01 — Unit per adapter
- [ ] add/search/delete per adapter (real embedded; server with mock).
#### Z7-02 — Score parity
- [ ] Top-k order of each adapter vs Chroma (reference) within declared tolerance.
#### Z7-03 — Port + retype
- [ ] `conforms_to` for the 5; RAG+memory suite green with default chroma; `FakeVectorStore` in patterns.

### PHASE Z8 — Docs + Example
#### Z8-01 — Documentation
- [ ] `docs/vector-stores.md`: selection, extras, score contract, server backend (auth/network), migration.
#### Z8-02 — Example
- [ ] `examples/vector_store_lancedb.py`.

### HARDENING
- [ ] Coverage ≥ 85% in `rag/stores/**` + factory.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] `CLAUDE.md` (rag/ section + extras) and `README.md` updated.

---

## 4. Inter-Task Dependencies

```
Z1 (port)
 └─▶ Z2 (score contract)
       └─▶ Z3-01 (Chroma relocated + shim)  ──▶ Z5 (retype) ──▶ Z7-03
             └─▶ Z3-02..05 (adapters)    ──▶ Z7-01, Z7-02 (parity)
Z4 (factory+settings+exc)  ──▶ Z5 (consumers use factory)
Z6 (extras)                ──▶ Z3-02..05 (dependency resolution)
Z8 (docs/example)          [after Z3..Z5]
```

Critical path: **Z1 → Z2 → Z3-01 → Z4 → Z5 → Z7** (default chroma green). The new adapters (Z3-02..05) and their parity are parallelizable after Z4.

---

## 5. Tasks ↔ Requirements Matrix

| Task | RF covered |
|---|---|
| Z1 | RF-VS-001 |
| Z2 | RF-VS-002 |
| Z3-01 | RF-VS-003 |
| Z3-02 | RF-VS-004 |
| Z3-03 | RF-VS-005 |
| Z3-04 | RF-VS-006 |
| Z3-05 | RF-VS-007 |
| Z4 | RF-VS-008, RF-VS-009, RF-VS-014 |
| Z5 | RF-VS-010, RF-VS-011 |
| Z6 | RF-VS-012 |
| Z7 | RF-VS-002, RF-VS-013 |
| Z8 | RF-VS-015 |

Coverage: RF-VS-001..015 mapped.

---

## 6. Risk Matrix

| Risk | Mitigation | Task |
|---|---|---|
| Score not comparable breaks hybrid | Contract `[0,1]` + per-adapter normalization + parity | Z2, Z7-02 |
| Relocation breaks imports | Shim in `rag/vector_store.py` | Z3-01 |
| Optional backend absent | Deferred import + `VectorStoreBackendUnavailable` | Z4-03 |
| `chroma_path` breaks | Backward-compatible alias | Z4-02 |
| sqlite-vec without a clean LangChain integration | Evaluate direct SQL (PA-2) | Z3-03 |
| Server without auth (qdrant/pg) | Document auth+network; default embedded | Z8-01 |

---

## 7. Definition of Done (Global for Phase Z)

- [ ] `VectorStorePort` declared/re-exported; Chroma conforms without behavior change.
- [ ] 4 new conforming adapters (LanceDB, sqlite-vec, Qdrant, pgvector).
- [ ] Score contract `[0,1]` verified by parity against Chroma.
- [ ] `VectorStoreFactory` + `settings.vector_store_backend` (default chroma) + generalized config; `chroma_path` alias.
- [ ] RAG + memory retyped to the port; no `ChromaVectorStore` type hints in consumers.
- [ ] Optional extras; slim base; deferred imports.
- [ ] `FakeVectorStore` + tests; coverage ≥ 85%.
- [ ] `docs/vector-stores.md` + example.
- [ ] `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` updated; PR merged with review.

---

## 8. Effort Estimate

| Sub-phase | Effort |
|---|---|
| Z1 Port | 0.2 wk |
| Z2 Score contract | 0.3 wk |
| Z3 Adapters (Chroma+4) | 1.5 wk |
| Z4 Factory+settings+exc | 0.5 wk |
| Z5 Consumer retype | 0.5 wk |
| Z6 Extras | 0.2 wk |
| Z7 Tests + parity | 0.6 wk |
| Z8 Docs + example | 0.4 wk |
| Hardening | 0.5 wk |
| **Total** | **~4.7 wk** |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial implementation plan — Vector Store Port |
