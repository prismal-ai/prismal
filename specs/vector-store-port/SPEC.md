# Prismal Vector Store Port — Interface Specification

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/vector-store-port/PLAN.md` |
| **Architecture** | `specs/vector-store-port/ARCHITECTURE.md` |
| **TASKS** | `specs/vector-store-port/TASKS.md` |

---

## Convenciones

- `from __future__ import annotations` en todos los módulos.
- Imports de SDKs de backend (`lancedb`, `sqlite_vec`, `qdrant_client`, `langchain_postgres`, …) **diferidos** dentro de cada adaptador, nunca a nivel de módulo del núcleo.
- API **sync** (paridad con `ChromaVectorStore` actual).
- Tipos de documento: `langchain_core.documents.Document`.
- Todos los símbolos públicos del puerto se re-exportan desde `prismal/agents/extension/__init__.py`.

---

## Resumen de módulos

| Módulo | Estado | Contenido |
|---|---|---|
| `prismal/agents/extension/ports.py` | MODIFICADO | `+ VectorStorePort` |
| `prismal/rag/vector_store.py` | MODIFICADO | Shim: re-export `ChromaVectorStore` desde `stores/chroma.py` |
| `prismal/rag/stores/chroma.py` | NUEVO (movido) | `ChromaVectorStore` (default) |
| `prismal/rag/stores/lancedb.py` | NUEVO | `LanceDBVectorStore` |
| `prismal/rag/stores/sqlite_vec.py` | NUEVO | `SqliteVecVectorStore` |
| `prismal/rag/stores/qdrant.py` | NUEVO | `QdrantVectorStore` |
| `prismal/rag/stores/pgvector.py` | NUEVO | `PgVectorStore` |
| `prismal/rag/stores/_normalize.py` | NUEVO | helpers de normalización de score |
| `prismal/rag/vector_store_factory.py` | NUEVO | `VectorStoreFactory`, `FakeVectorStore` |
| `prismal/core/config.py` | MODIFICADO | `vector_store_backend`, `vector_store_path`, `vector_store_url`, alias `chroma_path` |
| `prismal/core/exceptions.py` | MODIFICADO | `+ VectorStoreError`, `+ VectorStoreBackendUnavailable` |

---

## SPEC-VS-001: `VectorStorePort` (en `ports.py`)

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class VectorStorePort(Protocol):
    """Almacén vectorial intercambiable. Conforman: ChromaVectorStore (default),
    LanceDBVectorStore, SqliteVecVectorStore, QdrantVectorStore, PgVectorStore,
    FakeVectorStore. El núcleo solo invoca estos métodos; nunca construye un backend.
    """

    @property
    def collection_name(self) -> str: ...

    def add_documents(self, documents: list[Document]) -> list[str]: ...

    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]: ...
    # CONTRATO DE SCORE: cada tupla es (Document, score) con score ∈ [0,1],
    # mayor = más relevante. Ver SPEC-VS-002.

    def delete_by_source(self, source: str) -> None: ...
    # Borra por intención (metadata["source"] == source). Best-effort.

    def delete_collection(self) -> None: ...
```

Reglas:
- `add_documents` devuelve los IDs asignados por el backend.
- `delete_by_source` es **best-effort** (no lanza si no hay nada que borrar) — paridad con Chroma actual.
- `similarity_search` **debe** devolver score normalizado (SPEC-VS-002); si el backend nativo devuelve distancia, el adaptador la convierte.

---

## SPEC-VS-002: Contrato de score normalizado

Todos los adaptadores deben devolver `score ∈ [0,1]`, **mayor = más relevante**.

| Backend | Métrica nativa de `*_with_score` | Normalización a [0,1] |
|---|---|---|
| Chroma (default) | cosine similarity ∈ [0,1] (mayor=mejor) | identidad (referencia) |
| LanceDB | distancia (L2 o cosine, menor=mejor) | `1 / (1 + d)` o `1 - d` según métrica del índice |
| sqlite-vec | distancia L2/cosine (menor=mejor) | `1 / (1 + d)` |
| Qdrant | score según métrica del collection (cosine: mayor=mejor) | identidad si cosine; normalizar si dot/euclid |
| pgvector | distancia (`<->` L2, `<=>` cosine; menor=mejor) | `1 - d` (cosine) / `1/(1+d)` (L2) |

- La fórmula exacta por adaptador vive en `rag/stores/_normalize.py` y se documenta en el docstring del adaptador.
- **Test de paridad (SPEC-VS-010):** sobre un corpus fijo, el orden de los top-k de cada adaptador debe coincidir con Chroma (referencia) dentro de una tolerancia declarada.

---

## SPEC-VS-003: `ChromaVectorStore` (en `stores/chroma.py`, default)

API idéntica a la actual (movida desde `rag/vector_store.py`). Ya conforma `VectorStorePort` sin cambios de comportamiento. `rag/vector_store.py` queda como shim:

```python
# prismal/rag/vector_store.py
from prismal.rag.stores.chroma import ChromaStoreError, ChromaVectorStore
__all__ = ["ChromaStoreError", "ChromaVectorStore"]
```

---

## SPEC-VS-004: `LanceDBVectorStore` (en `stores/lancedb.py`)

```python
class LanceDBVectorStore:
    def __init__(self, collection_name: str = "default", settings: Settings | None = None) -> None: ...
    # import diferido de lancedb / langchain integración; usa EmbeddingsFactory.create(settings);
    # persiste en settings.vector_store_path (embebido, sin servidor).

    @property
    def collection_name(self) -> str: ...
    def add_documents(self, documents: list[Document]) -> list[str]: ...
    def similarity_search(self, query: str, k: int = 5) -> list[tuple[Document, float]]: ...
        # normaliza distancia → [0,1] (ver SPEC-VS-002)
    def delete_by_source(self, source: str) -> None: ...   # traduce a filtro LanceDB
    def delete_collection(self) -> None: ...
```
Extra: `[lancedb]`. Ausente → `VectorStoreBackendUnavailable`.

## SPEC-VS-005: `SqliteVecVectorStore` (en `stores/sqlite_vec.py`)
Misma forma. Embebido sobre la extensión `sqlite-vec` (en proceso, sin servidor). Persistencia en `vector_store_path`. Extra: `[sqlite-vec]`.

## SPEC-VS-006: `QdrantVectorStore` (en `stores/qdrant.py`)
Misma forma. Embebido (local) o servidor según `vector_store_url`; auth vía credenciales de settings. Extra: `[qdrant]`.

## SPEC-VS-007: `PgVectorStore` (en `stores/pgvector.py`)
Misma forma. Servidor Postgres vía `vector_store_url` (DSN). Extra: `[pgvector]`.

## SPEC-VS-008: `FakeVectorStore` (en `vector_store_factory.py`, tests)

```python
class FakeVectorStore:
    def __init__(self, docs: dict[str, list[tuple[Document, float]]] | None = None,
                 collection_name: str = "fake") -> None: ...
    # Determinista, sin I/O. similarity_search devuelve docs.get(query, []) ya en [0,1].
```

---

## SPEC-VS-009: `VectorStoreFactory` (en `vector_store_factory.py`)

```python
class VectorStoreFactory:
    @staticmethod
    def create(settings: Settings | None = None, collection_name: str = "default") -> VectorStorePort:
        """Selecciona por settings.vector_store_backend (default 'chroma').

        Mapea:
            "chroma"     -> ChromaVectorStore
            "lancedb"    -> LanceDBVectorStore
            "sqlite_vec" -> SqliteVecVectorStore
            "qdrant"     -> QdrantVectorStore
            "pgvector"   -> PgVectorStore
        Import diferido del adaptador; si falta el extra -> VectorStoreBackendUnavailable
        con mensaje guía ('pip install prismal[<extra>]'). Espejo de EmbeddingsFactory.create.
        """
```

Uso por consumidores (reemplaza `ChromaVectorStore(...)` directo):
```python
self._store: VectorStorePort = VectorStoreFactory.create(settings, collection_name="docs")
```

---

## SPEC-VS-010: Settings (en `core/config.py`)

```python
vector_store_backend: Literal["chroma", "lancedb", "sqlite_vec", "qdrant", "pgvector"] = "chroma"
vector_store_path: str = Field(default="data/db/vectors")   # backends embebidos
vector_store_url: str | None = Field(default=None)          # backends servidor (qdrant/pg)
# credenciales servidor (opt): vector_store_api_key / vector_store_user / vector_store_password

# Retrocompat: chroma_path sigue existiendo y, cuando backend == "chroma",
# alimenta vector_store_path si éste no se especificó.
```

Resolución de path:
- backend embebido → `vector_store_path` (o `chroma_path` para chroma si está seteado).
- backend servidor → `vector_store_url` (obligatorio; error claro si falta).

---

## SPEC-VS-011: Excepciones (en `core/exceptions.py`)

```python
class VectorStoreError(PrismalError):
    """Fallo genérico de un almacén vectorial (generaliza ChromaStoreError)."""

class VectorStoreBackendUnavailable(VectorStoreError):
    """El backend seleccionado no está instalado."""
    def __init__(self, backend: str, extra: str) -> None:
        super().__init__(
            f"Vector store backend '{backend}' is not available. "
            f"Install it with: pip install 'prismal[{extra}]'."
        )

# ChromaStoreError pasa a subclasear VectorStoreError (retrocompat).
```

---

## SPEC-VS-012: Consumidores retipados (RAG + memoria)

Cambio de **type hint** y de la construcción por defecto (sin cambio de lógica):

| Módulo | Antes | Después |
|---|---|---|
| `rag/engine.py` | `ChromaVectorStore(...)` | `VectorStoreFactory.create(settings, collection)` ; campo `VectorStorePort` |
| `rag/hyde.py` | `vector_store: ChromaVectorStore` | `vector_store: VectorStorePort` |
| `rag/self_rag.py` | idem | `VectorStorePort` |
| `rag/hybrid.py` | idem | `VectorStorePort` |
| `rag/hierarchical.py` | idem | `VectorStorePort` |
| `rag/multi_vector.py` | idem | `VectorStorePort` |
| `rag/multimodal.py` | idem | `VectorStorePort` |
| `rag/crag.py` (`CRAGPipeline`) | `vector_store=` | `VectorStorePort` |
| `memory/long_term.py` | default `ChromaVectorStore(...)` | `VectorStoreFactory.create(...)` ; tipo `VectorStorePort` |
| `memory/mongodb_store.py` | default `ChromaVectorStore(...)` | `VectorStoreFactory.create(...)` ; tipo `VectorStorePort` |

La inyección explícita (`vector_store=...`) ya existente se conserva; solo cambia el tipo aceptado (más amplio).

---

## SPEC-VS-013: Re-exports (en `extension/__init__.py`)

```python
from prismal.agents.extension.ports import VectorStorePort
```

Las clases de adaptador y la factory se importan desde `prismal.rag` (no desde `extension`, que es el namespace de contratos):
```python
from prismal.rag.vector_store_factory import VectorStoreFactory, FakeVectorStore
```

---

## SPEC-VS-014: Extras (en `pyproject.toml`)

```toml
[project.optional-dependencies]
lancedb    = ["lancedb>=..."]
sqlite-vec = ["sqlite-vec>=..."]
qdrant     = ["langchain-qdrant>=...", "qdrant-client>=..."]
pgvector   = ["langchain-postgres>=...", "psycopg[binary]>=..."]
```
La base **no** gana dependencias obligatorias. `all` puede agregarlas si ya existe ese extra agregador.

---

## Contrato del Operador

### Cambiar de backend (embebido)
```python
# .env / settings
vector_store_backend = "lancedb"
vector_store_path = "data/db/vectors"
# pip install 'prismal[lancedb]'
```

### Backend servidor (Qdrant/pg)
```python
vector_store_backend = "qdrant"
vector_store_url = "http://qdrant.internal:6333"
vector_store_api_key = "<secret>"   # auth + red privada (responsabilidad del operador)
```

### Tests
```python
from prismal.rag.vector_store_factory import FakeVectorStore
engine = RAGEngine(vector_store=FakeVectorStore({...}))
```

---

## Compatibilidad y Versionado

- `VectorStorePort`, adaptadores y `VectorStoreFactory` son **API pública** (SemVer; breaking → minor + deprecación 1 release).
- `ChromaVectorStore` y `ChromaStoreError` mantienen su import path actual (shim) → cero ruptura.
- Default `chroma` + `chroma_path` alias → despliegues actuales no cambian.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Especificación inicial — puerto, adaptadores, factory, contrato de score |
