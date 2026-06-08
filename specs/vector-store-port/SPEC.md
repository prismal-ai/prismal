# Prismal Vector Store Port — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-05 |
| **PLAN** | `specs/vector-store-port/PLAN.md` |
| **Architecture** | `specs/vector-store-port/ARCHITECTURE.md` |
| **TASKS** | `specs/vector-store-port/TASKS.md` |

---

## Conventions

- `from __future__ import annotations` in all modules.
- Imports of backend SDKs (`lancedb`, `sqlite_vec`, `qdrant_client`, `langchain_postgres`, …) **deferred** inside each adapter, never at the core module level.
- **Sync** API (parity with the current `ChromaVectorStore`).
- Document types: `langchain_core.documents.Document`.
- All public port symbols are re-exported from `prismal/agents/extension/__init__.py`.

---

## Module Summary

| Module | Status | Content |
|---|---|---|
| `prismal/agents/extension/ports.py` | MODIFIED | `+ VectorStorePort` |
| `prismal/rag/vector_store.py` | MODIFIED | Shim: re-export `ChromaVectorStore` from `stores/chroma.py` |
| `prismal/rag/stores/chroma.py` | NEW (moved) | `ChromaVectorStore` (default) |
| `prismal/rag/stores/lancedb.py` | NEW | `LanceDBVectorStore` |
| `prismal/rag/stores/sqlite_vec.py` | NEW | `SqliteVecVectorStore` |
| `prismal/rag/stores/qdrant.py` | NEW | `QdrantVectorStore` |
| `prismal/rag/stores/pgvector.py` | NEW | `PgVectorStore` |
| `prismal/rag/stores/_normalize.py` | NEW | score normalization helpers |
| `prismal/rag/vector_store_factory.py` | NEW | `VectorStoreFactory`, `FakeVectorStore` |
| `prismal/core/config.py` | MODIFIED | `vector_store_backend`, `vector_store_path`, `vector_store_url`, alias `chroma_path` |
| `prismal/core/exceptions.py` | MODIFIED | `+ VectorStoreError`, `+ VectorStoreBackendUnavailable` |

---

## SPEC-VS-001: `VectorStorePort` (in `ports.py`)

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class VectorStorePort(Protocol):
    """Interchangeable vector store. Conforming: ChromaVectorStore (default),
    LanceDBVectorStore, SqliteVecVectorStore, QdrantVectorStore, PgVectorStore,
    FakeVectorStore. The core only invokes these methods; it never constructs a backend.
    """

    @property
    def collection_name(self) -> str: ...

    def add_documents(self, documents: list[Document]) -> list[str]: ...

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]: ...
    # SCORE CONTRACT: each tuple is (Document, score) with score ∈ [0,1],
    # higher = more relevant. See SPEC-VS-002.

    def delete_by_source(self, source: str) -> None: ...
    # Deletes by intent (metadata["source"] == source). Best-effort.

    def delete_collection(self) -> None: ...
```

Rules:
- `add_documents` returns the IDs assigned by the backend.
- `delete_by_source` is **best-effort** (does not raise if there is nothing to delete) — parity with current Chroma.
- `similarity_search` **must** return a normalized score (SPEC-VS-002); if the native backend returns a distance, the adapter converts it.

---

## SPEC-VS-002: Normalized score contract

All adapters must return `score ∈ [0,1]`, **higher = more relevant**.

| Backend | Native metric of `*_with_score` | Normalization to [0,1] |
|---|---|---|
| Chroma (default) | cosine similarity ∈ [0,1] (higher=better) | identity (reference) |
| LanceDB | distance (L2 or cosine, lower=better) | `1 / (1 + d)` or `1 - d` depending on metric |
| sqlite-vec | L2/cosine distance (lower=better) | `1 / (1 + d)` |
| Qdrant | score per collection metric (cosine: higher=better) | identity if cosine; normalize if dot/euclid |
| pgvector | distance (`<->` L2, `<=>` cosine; lower=better) | `1 - d` (cosine) / `1/(1+d)` (L2) |

- The exact per-adapter formula lives in `rag/stores/_normalize.py` and is documented in the adapter's docstring.
- **Parity test (SPEC-VS-010):** over a fixed corpus, the top-k order of each adapter must match Chroma (reference) within a declared tolerance.

---

## SPEC-VS-003: `ChromaVectorStore` (in `stores/chroma.py`, default)

API identical to the current one (moved from `rag/vector_store.py`). It already conforms to `VectorStorePort` without behavior changes. `rag/vector_store.py` remains as a shim:

```python
# prismal/rag/vector_store.py
from prismal.rag.stores.chroma import ChromaStoreError, ChromaVectorStore
__all__ = ["ChromaStoreError", "ChromaVectorStore"]
```

---

## SPEC-VS-004: `LanceDBVectorStore` (in `stores/lancedb.py`)

```python
class LanceDBVectorStore:
    def __init__(self, collection_name: str = "default", settings: Settings | None = None) -> None: ...
    # deferred import of lancedb / langchain integration; uses EmbeddingsFactory.create(settings);
    # persists in settings.vector_store_path (embedded, no server).

    @property
    def collection_name(self) -> str: ...
    def add_documents(self, documents: list[Document]) -> list[str]: ...
    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]: ...
        # normalizes distance → [0,1] (see SPEC-VS-002)
    def delete_by_source(self, source: str) -> None: ...   # translates to a LanceDB filter
    def delete_collection(self) -> None: ...
```
Extra: `[lancedb]`. Absent → `VectorStoreBackendUnavailable`.

## SPEC-VS-005: `SqliteVecVectorStore` (in `stores/sqlite_vec.py`)
Same shape. Embedded over the `sqlite-vec` extension (in-process, no server). Persistence in `vector_store_path`. Extra: `[sqlite-vec]`.

## SPEC-VS-006: `QdrantVectorStore` (in `stores/qdrant.py`)
Same shape. Embedded (local) or server depending on `vector_store_url`; auth via settings credentials. Extra: `[qdrant]`.

## SPEC-VS-007: `PgVectorStore` (in `stores/pgvector.py`)
Same shape. Postgres server via `vector_store_url` (DSN). Extra: `[pgvector]`.

## SPEC-VS-008: `FakeVectorStore` (in `vector_store_factory.py`, tests)

```python
class FakeVectorStore:
    def __init__(self, docs: dict[str, list[tuple[Document, float]]] | None = None,
                 collection_name: str = "fake") -> None: ...
    # Deterministic, no I/O. similarity_search returns docs.get(query, []) already in [0,1].
```

---

## SPEC-VS-009: `VectorStoreFactory` (in `vector_store_factory.py`)

```python
class VectorStoreFactory:
    @staticmethod
    def create(settings: Settings | None = None, collection_name: str = "default") -> VectorStorePort:
        """Selects by settings.vector_store_backend (default 'chroma').

        Maps:
            "chroma"     -> ChromaVectorStore
            "lancedb"    -> LanceDBVectorStore
            "sqlite_vec" -> SqliteVecVectorStore
            "qdrant"     -> QdrantVectorStore
            "pgvector"   -> PgVectorStore
        Deferred import of the adapter; if the extra is missing -> VectorStoreBackendUnavailable
        with a guiding message ('pip install prismal[<extra>]'). Mirror of EmbeddingsFactory.create.
        """
```

Use by consumers (replaces direct `ChromaVectorStore(...)`):
```python
self._store: VectorStorePort = VectorStoreFactory.create(settings, collection_name="docs")
```

---

## SPEC-VS-010: Settings (in `core/config.py`)

```python
vector_store_backend: Literal["chroma", "lancedb", "sqlite_vec", "qdrant", "pgvector"] = "chroma"
vector_store_path: str = Field(default="data/db/vectors")   # embedded backends
vector_store_url: str | None = Field(default=None)          # server backends (qdrant/pg)
# server credentials (opt): vector_store_api_key / vector_store_user / vector_store_password

# Backward-compat: chroma_path still exists and, when backend == "chroma",
# feeds vector_store_path if the latter was not specified.
```

Path resolution:
- embedded backend → `vector_store_path` (or `chroma_path` for chroma if set).
- server backend → `vector_store_url` (mandatory; clear error if missing).

---

## SPEC-VS-011: Exceptions (in `core/exceptions.py`)

```python
class VectorStoreError(PrismalError):
    """Generic failure of a vector store (generalizes ChromaStoreError)."""

class VectorStoreBackendUnavailable(VectorStoreError):
    """The selected backend is not installed."""
    def __init__(self, backend: str, extra: str) -> None:
        super().__init__(
            f"Vector store backend '{backend}' is not available. "
            f"Install it with: pip install 'prismal[{extra}]'."
        )

# ChromaStoreError now subclasses VectorStoreError (backward-compat).
```

---

## SPEC-VS-012: Retyped consumers (RAG + memory)

Change of **type hint** and default construction (no logic change):

| Module | Before | After |
|---|---|---|
| `rag/engine.py` | `ChromaVectorStore(...)` | `VectorStoreFactory.create(settings, collection)` ; field `VectorStorePort` |
| `rag/hyde.py` | `vector_store: ChromaVectorStore` | `vector_store: VectorStorePort` |
| `rag/self_rag.py` | same | `VectorStorePort` |
| `rag/hybrid.py` | same | `VectorStorePort` |
| `rag/hierarchical.py` | same | `VectorStorePort` |
| `rag/multi_vector.py` | same | `VectorStorePort` |
| `rag/multimodal.py` | same | `VectorStorePort` |
| `rag/crag.py` (`CRAGPipeline`) | `vector_store=` | `VectorStorePort` |
| `memory/long_term.py` | default `ChromaVectorStore(...)` | `VectorStoreFactory.create(...)` ; type `VectorStorePort` |
| `memory/mongodb_store.py` | default `ChromaVectorStore(...)` | `VectorStoreFactory.create(...)` ; type `VectorStorePort` |

The already-existing explicit injection (`vector_store=...`) is preserved; only the accepted type changes (broader).

---

## SPEC-VS-013: Re-exports (in `extension/__init__.py`)

```python
from prismal.agents.extension.ports import VectorStorePort
```

The adapter classes and the factory are imported from `prismal.rag` (not from `extension`, which is the contracts namespace):
```python
from prismal.rag.vector_store_factory import VectorStoreFactory, FakeVectorStore
```

---

## SPEC-VS-014: Extras (in `pyproject.toml`)

```toml
[project.optional-dependencies]
lancedb    = ["lancedb>=..."]
sqlite-vec = ["sqlite-vec>=..."]
qdrant     = ["langchain-qdrant>=...", "qdrant-client>=..."]
pgvector   = ["langchain-postgres>=...", "psycopg[binary]>=..."]
```
The base does **not** gain mandatory dependencies. `all` may aggregate them if that aggregator extra already exists.

---

## Operator Contract

### Change backend (embedded)
```python
# .env / settings
vector_store_backend = "lancedb"
vector_store_path = "data/db/vectors"
# pip install 'prismal[lancedb]'
```

### Server backend (Qdrant/pg)
```python
vector_store_backend = "qdrant"
vector_store_url = "http://qdrant.internal:6333"
vector_store_api_key = "<secret>"   # auth + private network (operator's responsibility)
```

### Tests
```python
from prismal.rag.vector_store_factory import FakeVectorStore
engine = RAGEngine(vector_store=FakeVectorStore({...}))
```

---

## Compatibility and Versioning

- `VectorStorePort`, adapters, and `VectorStoreFactory` are **public API** (SemVer; breaking → minor + 1-release deprecation).
- `ChromaVectorStore` and `ChromaStoreError` keep their current import path (shim) → zero breakage.
- Default `chroma` + `chroma_path` alias → current deployments do not change.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Initial specification — port, adapters, factory, score contract |
