# Prismal — Vector Store Port (interchangeable vector database)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Phase** | Z — Vector Store Port (successor to Phase Y — Tool Provider Injection) |

---

## 1. Executive Summary

Today all of prismal's vector search goes through `prismal/rag/vector_store.py::ChromaVectorStore`, a thin *facade* over `langchain_community.vectorstores.Chroma`. The coupling to ChromaDB is **nominal, not structural**: the wrapper already isolates Chroma (a single import of the lib) and **all consumers receive the store by constructor injection** (`engine`, `hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `CRAGPipeline`, and the `long_term`/`mongodb_store` memory). What ties it to Chroma is that the wrapper is a **concrete class** (not a port), that consumers **type against it**, and that the constructor **hardcodes** `Chroma(...)` + `settings.chroma_path`.

This phase introduces a **`VectorStorePort`** (Protocol) and a **`VectorStoreFactory`** selectable by `settings.vector_store_backend`, with adapters for **Chroma (existing), LanceDB, sqlite-vec, Qdrant, and pgvector**. It covers **RAG and the memory layer**. The change is **additive and backward-compatible**: **Chroma remains the default** (zero breakage for current users), the alternatives are opt-in via extras, and consumers only change their *type hint* (`ChromaVectorStore` → `VectorStorePort`), not their logic.

The benefit: prismal stops being tied to one vector database. It allows choosing **embedded backends with no HTTP server** (LanceDB, sqlite-vec) that structurally reduce the attack surface — relevant after the critical CVE of the ChromaDB *server* (CVE-2026-45829), which prismal does not expose today but whose risk family disappears with a serverless backend.

---

## 2. Context and Problem

### 2.1 Current Situation

- **`ChromaVectorStore` is already a narrow facade** with API: `add_documents`, `similarity_search` → `(Document, score)`, `delete_by_source`, `delete_collection`, `collection_name`. A single place imports `Chroma`.
- **Constructor injection already present** in all advanced RAG patterns and in memory: they receive `vector_store: ChromaVectorStore`. The inversion is 80% done.
- **Residual coupling:**
  1. The wrapper is a **concrete class**; consumers type against `ChromaVectorStore` (leaky: the name says "Chroma").
  2. The constructor **builds `Chroma(...)`** and reads **`settings.chroma_path`** — there is no backend selection point.
  3. The config only models `chroma_path` (local persistence); a server backend (Qdrant/pgvector) needs URL/credentials.
- **Memory also uses it:** `long_term.py` and `mongodb_store.py` build `ChromaVectorStore` by default → any abstraction must serve RAG **and** memory.

### 2.2 Problem

1. **Provider lock-in:** changing the vector database today requires touching the wrapper, the type hints of ~9 modules, and the config. It is not a config *swap*.
2. **No lower-surface option:** you cannot choose an embedded serverless backend (LanceDB/sqlite-vec) to reduce the risk of the server CVE family.
3. **Non-contractual score semantics:** `similarity_search_with_score` returns cosine `[0,1]` (higher=better) in Chroma but **distance** (lower=better) in other backends; `hybrid.py` fuses scores assuming a scale. Without a normalized contract, changing backend silently breaks ranking.

### 2.3 Opportunity

The pattern is already proven in the repo: `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`, `ToolProviderPort` (Phase X/Y) in `prismal/agents/extension/ports.py`. `EmbeddingsFactory.create(settings)` is already the exact mirror of the factory we need. The effort is low: **one port + one factory + 4 new adapters + retyping of consumers**, without changing the logic of the RAG patterns.

---

## 3. Target Users

### Persona 1: Operator / Platform Engineer
- **Need:** Choose the vector database by configuration (`vector_store_backend`) according to security, deployment, or scale, without touching code.
- **Frequency:** Per environment/deployment.

### Persona 2: Security-conscious Adopter
- **Need:** Use an embedded backend with no HTTP server (LanceDB/sqlite-vec) to minimize the surface.
- **Frequency:** Initial architecture decision.

### Persona 3: RAG Engineer
- **Need:** That the patterns (hybrid, hierarchical, multi_vector, self_rag, HyDE) work identically with any backend, with comparable scores.
- **Frequency:** Continuous.

### Persona 4: Core Maintainer / Test Author
- **Need:** Inject a deterministic `FakeVectorStore` in tests, without standing up Chroma or another backend.
- **Frequency:** Daily.

---

## 4. Objectives and Success Metrics

### 4.1 Objectives

| Objective | Metric | Target | Timeframe |
|---|---|---|---|
| Interchangeable backend | Change the vector database without touching code (only `settings`) | Yes | Phase Z |
| Decouple the type | Consumers typed against `VectorStorePort`, not `ChromaVectorStore` | 100% of consumers | Phase Z |
| RAG + memory coverage | rag/ and memory/ use the port | Both | Phase Z |
| Score contract | Normalized score `[0,1]` (higher=better) in all adapters | Verified by test | Phase Z |
| Backward compatibility | Default = chroma; existing suite without changes | 100% | Global |
| Slim base | New backends as optional extras | `[lancedb]`,`[sqlite-vec]`,`[qdrant]`,`[pgvector]` | Phase Z |
| Test coverage | Branch coverage of new modules | ≥ 85% | Global |

### 4.2 User Objectives

| Objective | Indicator |
|---|---|
| Change backend by config | `settings.vector_store_backend = "lancedb"` |
| Serverless backend | LanceDB / sqlite-vec available and embedded |
| Identical RAG patterns | hybrid/hierarchical/… without changes; comparable scores |
| Tests without a real backend | `FakeVectorStore` injectable |

---

## 5. Scope

### 5.1 In Scope (Phase Z)

**Z1 — `VectorStorePort` (`prismal/agents/extension/ports.py`):**
- [ ] `Protocol` with `add_documents`, `similarity_search`, `delete_by_source`, `delete_collection`, `collection_name`.
- [ ] Re-export from `extension/__init__.py`. Helper `conforms_to`.

**Z2 — Normalized score contract:**
- [ ] Define: `similarity_search` returns `(Document, score)` with `score ∈ [0,1]`, higher = more relevant.
- [ ] Each adapter documents its native metric and the normalization formula.

**Z3 — Adapters:**
- [ ] `ChromaVectorStore` (existing) conforms to the port (retype, no logic change; stays as default).
- [ ] `LanceDBVectorStore` (embedded).
- [ ] `SqliteVecVectorStore` (embedded).
- [ ] `QdrantVectorStore` (embedded or server).
- [ ] `PgVectorStore` (Postgres server).
- [ ] All wrap the corresponding LangChain integration; deferred imports; score normalization.

**Z4 — `VectorStoreFactory` + settings:**
- [ ] `VectorStoreFactory.create(settings, collection_name)` (mirror of `EmbeddingsFactory`).
- [ ] `settings.vector_store_backend: Literal["chroma","lancedb","sqlite_vec","qdrant","pgvector"] = "chroma"`.
- [ ] Generalized connection config: `vector_store_path` (embedded) + `vector_store_url`/credentials (server); `chroma_path` is kept as a backward-compatible alias.

**Z5 — Consumer retyping (RAG + memory):**
- [ ] `engine`, `hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `crag` → `vector_store: VectorStorePort`.
- [ ] `memory/long_term.py`, `memory/mongodb_store.py` → build via factory and type to the port.

**Z6 — Extras and dependencies:**
- [ ] Extras `[lancedb]`, `[sqlite-vec]`, `[qdrant]`, `[pgvector]`; base without new mandatory deps.

**Z7 — Tests:**
- [ ] `FakeVectorStore` for fixtures.
- [ ] Score parity test (Chroma vs adapters) and port behavior test.

**Z8 — Docs and examples:**
- [ ] `docs/vector-stores.md` (selection, extras, score contract, migration).
- [ ] `examples/vector_store_lancedb.py`.

### 5.2 Out of Scope

- Automatic data migration between backends (export/import remains a future utility).
- Changing the default away from Chroma (decision: keep Chroma as default).
- FAISS as an adapter in this delivery (not selected; can be added later conforming to the port).
- Per-host/per-session injection of the store (factory+setting is enough now; the port leaves the door open to the host variant like Phase Y).
- Sharding/replication or per-backend index tuning.

### 5.3 Future Considerations

- "Host-injected" variant of the store (like `ToolProviderPort`) for a per-tenant backend.
- Migration utility `export → import` between backends.
- Additional FAISS / Milvus / Weaviate adapters.
- Native hybrid search where the backend supports it (Qdrant, pgvector).

---

## 6. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-VS-001 | `VectorStorePort` declares the current narrow API of `ChromaVectorStore` | `MUST` |
| RF-VS-002 | `similarity_search` returns a normalized score `[0,1]` higher=better in all adapters | `MUST` |
| RF-VS-003 | `ChromaVectorStore` conforms to the port without changing behavior (default) | `MUST` |
| RF-VS-004 | `LanceDBVectorStore` adapter (embedded) | `MUST` |
| RF-VS-005 | `SqliteVecVectorStore` adapter (embedded) | `MUST` |
| RF-VS-006 | `QdrantVectorStore` adapter (embedded/server) | `SHOULD` |
| RF-VS-007 | `PgVectorStore` adapter (server) | `SHOULD` |
| RF-VS-008 | `VectorStoreFactory.create(settings, collection_name)` selects the backend | `MUST` |
| RF-VS-009 | `settings.vector_store_backend` (default `chroma`) + generalized connection config | `MUST` |
| RF-VS-010 | RAG consumers type against `VectorStorePort` | `MUST` |
| RF-VS-011 | Memory (`long_term`, `mongodb_store`) uses factory + port | `MUST` |
| RF-VS-012 | New backends as optional extras; deferred imports | `MUST` |
| RF-VS-013 | `FakeVectorStore` for tests | `SHOULD` |
| RF-VS-014 | `chroma_path` backward-compatible as an alias for the new config | `MUST` |
| RF-VS-015 | Docs + runnable example | `SHOULD` |

---

## 7. Non-Functional Requirements

### Performance
- The port adds no appreciable overhead (direct delegation to the backend).
- Each adapter respects the native cost of its `similarity_search`.

### Security
- Embedded backends (LanceDB, sqlite-vec) **do not open network ports** → lower surface.
- Server backends (Qdrant, pgvector) must support auth and a private network (operator's responsibility, documented).
- The normalized score contract must not leak paths/credentials in logs.

### Compatibility
- `prismal/` remains a PEP 420 namespace package.
- Default `chroma` → zero changes for current users.
- `filterwarnings=error`: imports of optional backends **deferred**; absent → clear error guiding to install the extra.

### Correctness
- Score contract verified by a parity test (Chroma as the `[0,1]` reference).
- `delete_by_source` semantic (each adapter translates its metadata filter).

### Maintainability
- Coverage ≥ 85% in new modules; `ruff` + `mypy --strict` + `bandit` clean.
- Public API (`VectorStorePort`, adapters, factory) versioned (SemVer; breaking → minor + 1-release deprecation).

---

## 8. Constraints and Dependencies

- Python 3.13+, `uv`. No new mandatory deps in base.
- Adapters wrap LangChain integrations (`langchain-chroma`/community, `langchain-qdrant`, `langchain-postgres`, LanceDB, sqlite-vec) — each in its extra.
- Score semantics and the metadata filter vary by backend → normalization and translation live in each adapter, not in the port.

| Dependency | Type | Use | Extra |
|---|---|---|---|
| `langchain-chroma` / community | Existing | Chroma adapter (default) | base |
| `lancedb` | New (optional) | LanceDB adapter | `[lancedb]` |
| `sqlite-vec` | New (optional) | sqlite-vec adapter | `[sqlite-vec]` |
| `langchain-qdrant` / `qdrant-client` | New (optional) | Qdrant adapter | `[qdrant]` |
| `langchain-postgres` / `psycopg` | New (optional) | pgvector adapter | `[pgvector]` |
| `EmbeddingsFactory` | Existing | Embeddings common to all backends | base |

---

## 9. User Stories

**US-VS-001:** As an Operator, I want to change the vector database by configuration.
```python
# settings.vector_store_backend = "lancedb"
store = VectorStoreFactory.create(settings, collection_name="docs")
```
- [ ] Without touching RAG or memory code.

**US-VS-002:** As a Security-conscious Adopter, I want a backend with no HTTP server.
- [ ] `lancedb` or `sqlite_vec` work embedded, without opening ports.

**US-VS-003:** As a RAG Engineer, I want the patterns to work the same with any backend.
- [ ] hybrid/hierarchical/multi_vector/self_rag/HyDE without changes; comparable scores `[0,1]`.

**US-VS-004:** As a Core Maintainer, I want tests without a real backend.
```python
engine = RAGEngine(vector_store=FakeVectorStore({...}))
```
- [ ] Deterministic, no I/O.

---

## 10. Risks and Mitigations

| Risk | Prob. | Impact | Mitigation |
|---|---|---|---|
| Score not comparable between backends breaks ranking (hybrid) | High | High | Contract `[0,1]` + per-adapter normalization + parity test |
| Metadata filter (`where`) differs by backend | Medium | Medium | Port exposes a semantic `delete_by_source`; adapter translates |
| Optional backend absent breaks the import | Medium | Medium | Deferred imports + clear error pointing to the extra |
| Connection config breaks `chroma_path` | Low | Medium | `chroma_path` as a backward-compatible alias |
| Memory and RAG diverge on the contract | Low | Medium | A single port serves both; tests on both |
| Qdrant/pgvector expose a server without auth | Medium | High | Document auth + private network; default stays embedded (chroma) |

---

## 11. Estimated Timeline

| Phase | Duration | Deliverable |
|---|---|---|
| Z1 — Port | 0.2 wk | `VectorStorePort` + re-export |
| Z2 — Score contract | 0.3 wk | Definition + reference test |
| Z3 — Adapters | 1.5 wk | Chroma (retype) + LanceDB + sqlite-vec + Qdrant + pgvector |
| Z4 — Factory + settings | 0.5 wk | `VectorStoreFactory` + generalized config |
| Z5 — Consumer retyping | 0.5 wk | RAG + memory against the port |
| Z6 — Extras | 0.2 wk | `[lancedb]`/`[sqlite-vec]`/`[qdrant]`/`[pgvector]` |
| Z7 — Tests + parity | 0.6 wk | `FakeVectorStore` + score parity |
| Z8 — Docs + example | 0.4 wk | `docs/vector-stores.md` + example |
| Hardening | 0.5 wk | Coverage ≥ 85%, mypy/bandit, cross-validation |
| **Total** | **~4.7 wk** | Interchangeable vector database, Chroma default |

---

## 12. Definition of Done (Global for Phase Z)

- [ ] `VectorStorePort` declared and re-exported; `ChromaVectorStore` conforms without behavior change.
- [ ] LanceDB, sqlite-vec, Qdrant, pgvector adapters implemented and conforming.
- [ ] Score contract `[0,1]` verified by parity test against Chroma.
- [ ] `VectorStoreFactory.create` + `settings.vector_store_backend` (default `chroma`).
- [ ] Generalized connection config; `chroma_path` backward-compatible.
- [ ] RAG (engine, hyde, self_rag, hybrid, hierarchical, multi_vector, multimodal, crag) and memory (long_term, mongodb_store) typed against the port.
- [ ] Optional extras; slim base; deferred imports.
- [ ] `FakeVectorStore` + tests; coverage ≥ 85%.
- [ ] `docs/vector-stores.md` + runnable example.
- [ ] `uv run pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` updated.
- [ ] PR merged with review approved.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial version — interchangeable vector database via `VectorStorePort` |

## Approvals

| Role | Name | Date | Status |
|---|---|---|---|
| Tech Lead | — | | ☐ Pending |
| AI Architect | — | | ☐ Pending |
| Security Lead | — | | ☐ Pending |
