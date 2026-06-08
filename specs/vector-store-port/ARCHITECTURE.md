# Prismal Vector Store Port — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Related PLAN** | `specs/vector-store-port/PLAN.md` |
| **Related SPEC** | `specs/vector-store-port/SPEC.md` |
| **TASKS** | `specs/vector-store-port/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Context

Prismal's vector search is centralized in `prismal/rag/vector_store.py::ChromaVectorStore`, a *facade* over `langchain_community.vectorstores.Chroma`. The advanced RAG patterns (`hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`), the `RAGEngine`, the `CRAGPipeline`, and the memory layer (`long_term`, `mongodb_store`) already receive the store by **constructor injection**, but type against the **concrete class** `ChromaVectorStore`. This document describes **Phase Z — Vector Store Port**, which turns that concrete class into a **hexagonal port** with interchangeable adapters (Chroma, LanceDB, sqlite-vec, Qdrant, pgvector), selectable by `settings.vector_store_backend`, keeping Chroma as the default.

It is the natural continuation of the Phase X/Y port family (`CheckpointPort`, `EmbeddingsPort`, `ToolProviderPort`).

---

## 2. Technical Objectives

- **OT-1:** Model `VectorStorePort` as a structural `Protocol` with the current narrow API.
- **OT-2:** Keep `ChromaVectorStore` as the default adapter, with no behavior change.
- **OT-3:** Add LanceDB, sqlite-vec, Qdrant, pgvector adapters that conform to the port.
- **OT-4:** Define and enforce a **normalized score contract** `[0,1]` (higher=better) in all adapters.
- **OT-5:** Select the backend via `VectorStoreFactory.create(settings)` (mirror of `EmbeddingsFactory`).
- **OT-6:** Retype RAG **and** memory against the port, without changing logic.
- **OT-7:** Keep the base slim: new backends as extras with deferred imports.

---

## 3. Proposed Architecture

### 3.1 High-Level Diagram

```
BEFORE:
  rag/engine, hyde, self_rag, hybrid, hierarchical, multi_vector, multimodal, crag
  memory/long_term, mongodb_store
        │  vector_store: ChromaVectorStore  (concrete type)
        ▼
  rag/vector_store.py::ChromaVectorStore ──▶ langchain_community...Chroma ──▶ chroma_path

AFTER:
  (same consumers)
        │  vector_store: VectorStorePort   (abstract type)
        ▼
  VectorStoreFactory.create(settings, collection)   ── settings.vector_store_backend
        ├─ "chroma"     → ChromaVectorStore     ──▶ langchain Chroma        (default)
        ├─ "lancedb"    → LanceDBVectorStore    ──▶ langchain LanceDB        [lancedb]
        ├─ "sqlite_vec" → SqliteVecVectorStore  ──▶ sqlite-vec               [sqlite-vec]
        ├─ "qdrant"     → QdrantVectorStore     ──▶ langchain-qdrant         [qdrant]
        └─ "pgvector"   → PgVectorStore         ──▶ langchain-postgres       [pgvector]
              ▲ all conform to VectorStorePort + normalize score to [0,1]
  prismal/agents/extension/ports.py :: VectorStorePort (Protocol)
```

### 3.2 Layer Diagram

```
┌────────────────────────────────────────────────────────────┐
│ CONSUMERS (no logic change, only type hint)                │
│  rag/*  +  memory/long_term, mongodb_store                 │
└───────────────┬────────────────────────────────────────────┘
                │ vector_store: VectorStorePort
┌───────────────▼────────────────────────────────────────────┐
│ CONTRACT:  extension/ports.py :: VectorStorePort            │
│  add_documents · similarity_search([0,1]) · delete_by_source│
│  · delete_collection · collection_name                     │
└───────────────┬────────────────────────────────────────────┘
                │ produces
┌───────────────▼────────────────────────────────────────────┐
│ SELECTION:  rag/vector_store_factory.py :: VectorStoreFactory│
│   create(settings, collection_name) → VectorStorePort       │
└───────────────┬────────────────────────────────────────────┘
                │ instantiates (deferred import per extra)
┌───────────────▼────────────────────────────────────────────┐
│ ADAPTERS:  rag/stores/{chroma,lancedb,sqlite_vec,          │
│               qdrant,pgvector}.py                           │
│   each one: wraps the integration + normalizes score        │
└────────────────────────────────────────────────────────────┘
```

### 3.3 Components

#### Z1 — `VectorStorePort` (`prismal/agents/extension/ports.py`)
`@runtime_checkable Protocol` with the exact API of the current `ChromaVectorStore` (sync, for parity):
```
add_documents(documents) -> list[str]
similarity_search(query, k=5) -> list[tuple[Document, float]]   # score in [0,1], higher=better
delete_by_source(source) -> None
delete_collection() -> None
collection_name: str   (property)
```

#### Z2 — Adapters (`prismal/rag/stores/`)
Relocation: the current `rag/vector_store.py::ChromaVectorStore` is moved to `rag/stores/chroma.py` (with a backward-compatible import shim in `rag/vector_store.py`). New: `lancedb.py`, `sqlite_vec.py`, `qdrant.py`, `pgvector.py`. Each adapter:
- Builds its LangChain `VectorStore` (deferred import).
- Implements `similarity_search` **normalizing** the native metric to `[0,1]`.
- Translates `delete_by_source` to the backend's metadata filter.
- Uses `EmbeddingsFactory.create(settings)` (common embeddings).

#### Z3 — `VectorStoreFactory` (`prismal/rag/vector_store_factory.py`)
```
VectorStoreFactory.create(settings, collection_name="default") -> VectorStorePort
```
Selects by `settings.vector_store_backend`; deferred import of the adapter; if the extra is missing → `VectorStoreBackendUnavailable` with a guiding message. Mirror of `EmbeddingsFactory.create`.

#### Z4 — Settings (`prismal/core/config.py`)
- `vector_store_backend: Literal["chroma","lancedb","sqlite_vec","qdrant","pgvector"] = "chroma"`.
- `vector_store_path: str` (embedded; default reuses `chroma_path` for compat).
- `vector_store_url: str | None` + credentials (server; Qdrant/pg).
- `chroma_path` remains as a **backward-compatible alias** that feeds `vector_store_path` when the backend is chroma.

#### Z5 — Consumer retyping
Change of *type hint* `ChromaVectorStore` → `VectorStorePort` in: `rag/engine.py`, `hyde.py`, `self_rag.py`, `hybrid.py`, `hierarchical.py`, `multi_vector.py`, `multimodal.py`, `crag.py`, `memory/long_term.py`, `memory/mongodb_store.py`. The defaults move to `VectorStoreFactory.create(settings, collection)` instead of `ChromaVectorStore(...)`.

### 3.4 Data Flow

#### Flow Z-A: Backend resolution
```
1. RAGEngine() (or memory) without an explicit store
2. VectorStoreFactory.create(settings, "docs")
3. switch settings.vector_store_backend → deferred import of the adapter
4. adapter builds its LangChain VectorStore + EmbeddingsFactory
5. returns VectorStorePort
```

#### Flow Z-B: Search with normalized score
```
1. pattern (e.g. hybrid) → store.similarity_search(q, k)
2. adapter calls native similarity_search → (doc, native_metric)
3. adapter normalizes: score = normalize(native_metric) ∈ [0,1] (higher=better)
4. returns [(Document, score)] comparable across backends
5. hybrid fuses scores without knowing which backend is underneath
```

---

## 4. Design Decisions

### DD-VS-001: Structural port (Protocol), reusing the current API
The `VectorStorePort` copies the signature of `ChromaVectorStore` so that **the Chroma adapter conforms unchanged** and consumers only change the *hint*. Minimal blast radius.

### DD-VS-002: The score contract lives in the port, normalization in the adapter
The port **defines** `score ∈ [0,1]` higher=better (a central correctness decision). Each adapter **implements** the normalization from its native metric (cosine, L2, dot). Chroma already complies with `[0,1]` cosine → it is the reference of the parity test.

### DD-VS-003: Semantic `delete_by_source`, not syntactic
The port exposes `delete_by_source(source)` (intent), not a `where={...}` (Chroma-specific syntax). Each backend translates to its metadata filter. It avoids leaking one backend's syntax to the rest of the system.

### DD-VS-004: Factory + setting, not host injection (for now)
`VectorStoreFactory.create(settings)` is chosen (mirror of `EmbeddingsFactory`) for simplicity and consistency. The port leaves the door open to a "host-injected" variant per session/tenant (like `ToolProviderPort` of Phase Y) without redoing anything.

### DD-VS-005: Chroma stays default; alternatives opt-in via extras
Zero breakage for current users. New backends as extras (`[lancedb]`,`[sqlite-vec]`,`[qdrant]`,`[pgvector]`) with deferred imports; absence → clear error pointing to the extra. Keeps the base slim (same as the multimodal layer extras).

### DD-VS-006: Relocate to `rag/stores/` with a backward-compatible shim
The adapters live in `rag/stores/`; `rag/vector_store.py` re-exports `ChromaVectorStore` (shim) so as not to break existing imports. Invisible migration.

### DD-VS-007: Generalized connection config with alias
`vector_store_path`/`vector_store_url` conceptually replace `chroma_path`, which is kept as a backward-compatible alias. It allows server backends (Qdrant/pg) without breaking Chroma's local config.

### DD-VS-008: Sync API, parity with today
The current `ChromaVectorStore` is sync and consumers call it sync; the port stays sync so as not to propagate `async` across the whole RAG layer. (An async variant can be added later if a backend requires it.)

---

## 5. Code Structure

```
prismal/
├── agents/extension/
│   ├── ports.py                       # + VectorStorePort
│   └── __init__.py                    # re-export VectorStorePort
├── rag/
│   ├── vector_store.py                # SHIM: re-export ChromaVectorStore (compat)
│   ├── vector_store_factory.py        # NEW: VectorStoreFactory
│   ├── stores/
│   │   ├── __init__.py
│   │   ├── chroma.py                  # MOVED from vector_store.py (default)
│   │   ├── lancedb.py                 # NEW
│   │   ├── sqlite_vec.py              # NEW
│   │   ├── qdrant.py                  # NEW
│   │   ├── pgvector.py                # NEW
│   │   └── _normalize.py             # score normalization helpers
│   ├── engine.py / hyde.py / self_rag.py / hybrid.py / hierarchical.py /
│   │   multi_vector.py / multimodal.py / crag.py   # retype to VectorStorePort
├── memory/
│   ├── long_term.py                   # factory + port
│   └── mongodb_store.py               # factory + port
├── core/
│   ├── config.py                      # vector_store_backend, *_path/*_url, alias chroma_path
│   └── exceptions.py                  # + VectorStoreBackendUnavailable
docs/vector-stores.md                  # NEW
examples/vector_store_lancedb.py       # NEW
tests/unit/rag/stores/                 # tests per adapter + score parity
```

### Applied Patterns
- **Hexagonal Ports & Adapters** (Phase X/Y).
- **Factory** (mirror of `EmbeddingsFactory`).
- **Adapter** (score normalization + per-backend filter translation).
- **Stable facade** (`VectorStorePort` with the current API).

### Error Handling
- Backend not installed → `VectorStoreBackendUnavailable` (guides to the extra).
- Backend operation failure → keep the current convention (`ChromaStoreError` generalizes to `VectorStoreError`; the adapters wrap native exceptions).
- `delete_by_source` best-effort (same as in Chroma today).

---

## 6. Security

### 6.1 Attack Surface
- **Embedded (LanceDB, sqlite-vec):** no network port → minimal surface; structurally eliminates the server CVE family (e.g. ChromaDB CVE-2026-45829).
- **Server (Qdrant, pgvector):** require auth + private network; documented as the operator's responsibility.

### 6.2 Cross-Cutting Rules
- Default embedded (chroma) → no server by default.
- Logs do not include credentials or DSN; sensitive connection config is treated as a secret.
- Each adapter keeps deferred imports (does not load backend SDKs unless selected).

---

## 7. Observability

### 7.1 Logs
- `vector_store.created{backend, collection}` in the factory (which backend ended up active).
- Parity with the current logs: `vector_store_add_documents`, `vector_store_similarity_search`, `vector_store_delete_by_source`.

### 7.2 Metrics
```
prismal_vector_store_backend_active{backend}
prismal_vector_store_queries_total{backend, op}
prismal_vector_store_errors_total{backend, op}
```

---

## 8. Testing Strategy

- **Unit per adapter:** add/search/delete with a real embedded backend (LanceDB/sqlite-vec run in-process) or mock for server (Qdrant/pg).
- **Score parity:** given a fixed corpus, the normalized scores of each adapter fall in `[0,1]` and order the same as Chroma (reference). Documented tolerance.
- **Port contract:** `conforms_to(adapter, VectorStorePort)` for all 5.
- **Retype without regression:** existing RAG + memory suite green with default chroma.
- **Mock:** `FakeVectorStore(mapping)` for pattern tests without a backend.

---

## 9. Rollout Plan

1. Z1–Z2 (port + score contract) — additive.
2. Z3 (Chroma adapter conforms + relocation with shim) — no behavior change.
3. Z4–Z5 (factory + settings + retype) — default chroma, green suite.
4. Z3 (LanceDB, sqlite-vec, Qdrant, pgvector) — opt-in per extra.
5. Z7–Z8 (parity tests + docs).

Backout: each adapter is isolated; the default chroma + shim guarantee a trivial rollback.

---

## 10. Open Questions

- **PA-1:** Exact tolerance of the score parity test (identical order vs correlation ≥ threshold)? (Define in Z2.)
- **PA-2:** Is sqlite-vec integrated via LangChain or via direct SQL over the extension? (Evaluate in Z3; affects the adapter.)
- **PA-3:** An async variant of the port in the future (Qdrant/pg async)? (Out of scope; DD-VS-008.)
- **PA-4:** Data migration Chroma→LanceDB as a utility in this phase or later? (Proposal: later.)

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial technical design — `VectorStorePort` + adapters + factory |
