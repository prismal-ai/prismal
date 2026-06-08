# Prismal Vector Stores (Phase Z)

Vector search is a **hexagonal port**: the RAG patterns and the memory layer
depend on `VectorStorePort`, and `VectorStoreFactory` builds the concrete
adapter named by `settings.vector_store_backend`. **Chroma stays the default**,
so existing deployments change nothing; the alternatives are opt-in via extras.
This is the same playbook as the tool-provider inversion (Fase Y).

The port is importable from `prismal.agents.extension`; the factory and the test
double from `prismal.rag.vector_store_factory`. A runnable example lives at
[`examples/vector_store_lancedb.py`](../examples/vector_store_lancedb.py).

---

## 1. Backends

| Backend | `vector_store_backend` | Kind | Extra | Server? |
|---|---|---|---|---|
| Chroma (default) | `chroma` | embedded | base install | no |
| LanceDB | `lancedb` | embedded | `[lancedb]` | no |
| sqlite-vec | `sqlite_vec` | embedded | `[sqlite-vec]` | no |
| Qdrant | `qdrant` | embedded or server | `[qdrant]` | optional |
| pgvector | `pgvector` | server | `[pgvector]` | yes |

Embedded backends (`lancedb`, `sqlite_vec`) open **no network port** —
structurally removing the server-CVE risk family. Prefer them when minimizing
attack surface is the priority.

---

## 2. Quickstart — swap by configuration

No consumer code changes; only settings (env vars use the `PRISMAL_` prefix):

```bash
# Embedded LanceDB
PRISMAL_VECTOR_STORE_BACKEND=lancedb
PRISMAL_VECTOR_STORE_PATH=data/db/vectors
# pip install 'prismal[lancedb]'
```

```python
from prismal.agents.extension import VectorStorePort
from prismal.rag.vector_store_factory import VectorStoreFactory
from prismal.core.config import get_settings

store: VectorStorePort = VectorStoreFactory.create(get_settings(), collection_name="docs")
store.add_documents(documents)
hits = store.similarity_search("query", k=3)   # [(Document, score)], score in [0, 1]
```

The RAG engine, the advanced patterns (`hybrid`, `hierarchical`, `multi_vector`,
`self_rag`, HyDE, RAG-Fusion), the CRAG pipeline, and the memory layer
(`long_term`, `mongodb_store`) all accept any `VectorStorePort` — inject your own
or let them build the configured default.

---

## 3. Server backends (Qdrant / pgvector)

Server backends need a connection URL and, usually, credentials. **Auth and a
private network are the operator's responsibility** — the defaults stay embedded.

```bash
# Qdrant server
PRISMAL_VECTOR_STORE_BACKEND=qdrant
PRISMAL_VECTOR_STORE_URL=http://qdrant.internal:6333
PRISMAL_VECTOR_STORE_API_KEY=<secret>
# pip install 'prismal[qdrant]'
```

```bash
# PostgreSQL + pgvector
PRISMAL_VECTOR_STORE_BACKEND=pgvector
PRISMAL_VECTOR_STORE_URL=postgresql+psycopg://user:pass@db.internal:5432/prismal
# pip install 'prismal[pgvector]'
```

Connection URLs and keys are treated as secrets and never logged. Qdrant without
`vector_store_url` runs embedded against `vector_store_path`; pgvector always
requires the DSN (a clear error is raised if it is missing).

---

## 4. Score contract (SPEC-VS-002)

`similarity_search` returns `(Document, score)` with **`score ∈ [0, 1]`,
higher = more relevant**, for every backend. Each adapter normalizes its native
metric:

| Backend | Native metric | Normalization |
|---|---|---|
| Chroma | cosine similarity `[0, 1]` | identity (reference) |
| LanceDB | distance (lower=better) | `1 / (1 + d)` |
| sqlite-vec | L2 distance | `1 / (1 + d)` |
| Qdrant | cosine similarity | identity |
| pgvector | cosine distance `<=>` | `1 - d` |

This is why `hybrid` can fuse dense scores without knowing the backend. The
normalization helpers live in `prismal/rag/stores/_normalize.py`; a parity test
pins them against the cosine reference.

---

## 5. The `chroma_path` alias (backward compatibility)

`vector_store_path` is the generalized embedded path. The legacy `chroma_path`
still works: when `vector_store_backend == "chroma"` it feeds the persistence
directory exactly as before, so current deployments are byte-for-byte unchanged.
`Settings.resolve_vector_store_path()` encapsulates the rule.

---

## 6. Testing without a backend

Inject `FakeVectorStore` — deterministic, no I/O:

```python
from langchain_core.documents import Document
from prismal.rag.vector_store_factory import FakeVectorStore

doc = Document(page_content="hello", metadata={"source": "a"})
store = FakeVectorStore({"my query": [(doc, 0.92)]})
engine = RAGEngine(vector_store=store)   # or pass to any pattern / memory store
```

`similarity_search` returns the pre-seeded `(Document, score)` tuples verbatim;
`add_documents` / `delete_by_source` / `delete_collection` record their inputs
for assertions.

---

## 7. Writing a new adapter

Conform to `VectorStorePort` (five methods: `collection_name`, `add_documents`,
`similarity_search`, `delete_by_source`, `delete_collection`), keep the backend
SDK import **deferred** inside `__init__`, raise `VectorStoreBackendUnavailable`
when the extra is missing, and normalize scores into `[0, 1]`. Add the class to
`prismal/rag/stores/`, wire it into `VectorStoreFactory.create`, and declare its
extra in `pyproject.toml`. No base class or registration is required — the port
is structural.
```python
from prismal.agents.extension import VectorStorePort, conforms_to
assert conforms_to(MyStore(), VectorStorePort)
```
