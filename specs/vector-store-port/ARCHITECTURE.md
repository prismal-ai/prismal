# Prismal Vector Store Port — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN Relacionado** | `specs/vector-store-port/PLAN.md` |
| **SPEC Relacionado** | `specs/vector-store-port/SPEC.md` |
| **TASKS** | `specs/vector-store-port/TASKS.md` |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |

---

## 1. Contexto

La búsqueda vectorial de prismal está centralizada en `prismal/rag/vector_store.py::ChromaVectorStore`, un *facade* sobre `langchain_community.vectorstores.Chroma`. Los patrones RAG avanzados (`hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`), el `RAGEngine`, el `CRAGPipeline` y la capa de memoria (`long_term`, `mongodb_store`) ya reciben el store por **inyección de constructor**, pero tipan contra la **clase concreta** `ChromaVectorStore`. Este documento describe la **Fase Z — Vector Store Port**, que convierte esa clase concreta en un **puerto hexagonal** con adaptadores intercambiables (Chroma, LanceDB, sqlite-vec, Qdrant, pgvector), seleccionables por `settings.vector_store_backend`, manteniendo Chroma como default.

Es la continuación natural de la familia de puertos de Fase X/Y (`CheckpointPort`, `EmbeddingsPort`, `ToolProviderPort`).

---

## 2. Objetivos Técnicos

- **OT-1:** Modelar `VectorStorePort` como `Protocol` estructural con la API estrecha actual.
- **OT-2:** Conservar `ChromaVectorStore` como adaptador default, sin cambio de comportamiento.
- **OT-3:** Añadir adaptadores LanceDB, sqlite-vec, Qdrant, pgvector que conformen el puerto.
- **OT-4:** Definir y hacer cumplir un **contrato de score normalizado** `[0,1]` (mayor=mejor) en todos los adaptadores.
- **OT-5:** Seleccionar backend por `VectorStoreFactory.create(settings)` (espejo de `EmbeddingsFactory`).
- **OT-6:** Retipar RAG **y** memoria contra el puerto, sin cambiar lógica.
- **OT-7:** Mantener la base slim: backends nuevos como extras con imports diferidos.

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel

```
ANTES:
  rag/engine, hyde, self_rag, hybrid, hierarchical, multi_vector, multimodal, crag
  memory/long_term, mongodb_store
        │  vector_store: ChromaVectorStore  (tipo concreto)
        ▼
  rag/vector_store.py::ChromaVectorStore ──▶ langchain_community...Chroma ──▶ chroma_path

DESPUÉS:
  (mismos consumidores)
        │  vector_store: VectorStorePort   (tipo abstracto)
        ▼
  VectorStoreFactory.create(settings, collection)   ── settings.vector_store_backend
        ├─ "chroma"     → ChromaVectorStore     ──▶ langchain Chroma        (default)
        ├─ "lancedb"    → LanceDBVectorStore    ──▶ langchain LanceDB        [lancedb]
        ├─ "sqlite_vec" → SqliteVecVectorStore  ──▶ sqlite-vec               [sqlite-vec]
        ├─ "qdrant"     → QdrantVectorStore     ──▶ langchain-qdrant         [qdrant]
        └─ "pgvector"   → PgVectorStore         ──▶ langchain-postgres       [pgvector]
              ▲ todos conforman VectorStorePort + normalizan score a [0,1]
  prismal/agents/extension/ports.py :: VectorStorePort (Protocol)
```

### 3.2 Diagrama de Capas

```
┌────────────────────────────────────────────────────────────┐
│ CONSUMIDORES (sin cambio de lógica, solo type hint)        │
│  rag/*  +  memory/long_term, mongodb_store                 │
└───────────────┬────────────────────────────────────────────┘
                │ vector_store: VectorStorePort
┌───────────────▼────────────────────────────────────────────┐
│ CONTRATO:  extension/ports.py :: VectorStorePort            │
│  add_documents · similarity_search([0,1]) · delete_by_source│
│  · delete_collection · collection_name                     │
└───────────────┬────────────────────────────────────────────┘
                │ produce
┌───────────────▼────────────────────────────────────────────┐
│ SELECCIÓN:  rag/vector_store_factory.py :: VectorStoreFactory│
│   create(settings, collection_name) → VectorStorePort       │
└───────────────┬────────────────────────────────────────────┘
                │ instancia (import diferido por extra)
┌───────────────▼────────────────────────────────────────────┐
│ ADAPTADORES:  rag/stores/{chroma,lancedb,sqlite_vec,        │
│               qdrant,pgvector}.py                           │
│   cada uno: envuelve la integración + normaliza score       │
└────────────────────────────────────────────────────────────┘
```

### 3.3 Componentes

#### Z1 — `VectorStorePort` (`prismal/agents/extension/ports.py`)
`@runtime_checkable Protocol` con la API exacta del `ChromaVectorStore` actual (sync, para paridad):
```
add_documents(documents) -> list[str]
similarity_search(query, k=5) -> list[tuple[Document, float]]   # score en [0,1], mayor=mejor
delete_by_source(source) -> None
delete_collection() -> None
collection_name: str   (property)
```

#### Z2 — Adaptadores (`prismal/rag/stores/`)
Reubicación: el actual `rag/vector_store.py::ChromaVectorStore` se mueve a `rag/stores/chroma.py` (con shim de import retrocompatible en `rag/vector_store.py`). Nuevos: `lancedb.py`, `sqlite_vec.py`, `qdrant.py`, `pgvector.py`. Cada adaptador:
- Construye su `VectorStore` LangChain (import diferido).
- Implementa `similarity_search` **normalizando** la métrica nativa a `[0,1]`.
- Traduce `delete_by_source` al filtro de metadata del backend.
- Usa `EmbeddingsFactory.create(settings)` (embeddings comunes).

#### Z3 — `VectorStoreFactory` (`prismal/rag/vector_store_factory.py`)
```
VectorStoreFactory.create(settings, collection_name="default") -> VectorStorePort
```
Selecciona por `settings.vector_store_backend`; import diferido del adaptador; si falta el extra → `VectorStoreBackendUnavailable` con mensaje guía. Espejo de `EmbeddingsFactory.create`.

#### Z4 — Settings (`prismal/core/config.py`)
- `vector_store_backend: Literal["chroma","lancedb","sqlite_vec","qdrant","pgvector"] = "chroma"`.
- `vector_store_path: str` (embebido; default reusa `chroma_path` para compat).
- `vector_store_url: str | None` + credenciales (servidor; Qdrant/pg).
- `chroma_path` permanece como **alias retrocompatible** que alimenta `vector_store_path` cuando el backend es chroma.

#### Z5 — Retipado de consumidores
Cambio de *type hint* `ChromaVectorStore` → `VectorStorePort` en: `rag/engine.py`, `hyde.py`, `self_rag.py`, `hybrid.py`, `hierarchical.py`, `multi_vector.py`, `multimodal.py`, `crag.py`, `memory/long_term.py`, `memory/mongodb_store.py`. Los defaults pasan a `VectorStoreFactory.create(settings, collection)` en lugar de `ChromaVectorStore(...)`.

### 3.4 Flujo de Datos

#### Flujo Z-A: Resolución de backend
```
1. RAGEngine() (o memoria) sin store explícito
2. VectorStoreFactory.create(settings, "docs")
3. switch settings.vector_store_backend → import diferido del adaptador
4. adaptador construye su VectorStore LangChain + EmbeddingsFactory
5. devuelve VectorStorePort
```

#### Flujo Z-B: Búsqueda con score normalizado
```
1. patrón (p.ej. hybrid) → store.similarity_search(q, k)
2. adaptador llama similarity_search nativo → (doc, métrica_nativa)
3. adaptador normaliza: score = normalize(métrica_nativa) ∈ [0,1] (mayor=mejor)
4. devuelve [(Document, score)] comparable entre backends
5. hybrid fusiona scores sin saber qué backend hay debajo
```

---

## 4. Decisiones de Diseño

### DD-VS-001: Puerto estructural (Protocol), reusando la API actual
El `VectorStorePort` copia la firma de `ChromaVectorStore` para que **el adaptador Chroma conforme sin cambios** y los consumidores solo cambien el *hint*. Mínimo blast radius.

### DD-VS-002: El contrato de score vive en el puerto, la normalización en el adaptador
El puerto **define** `score ∈ [0,1]` mayor=mejor (decisión central de correctitud). Cada adaptador **implementa** la normalización desde su métrica nativa (cosine, L2, dot). Chroma ya cumple `[0,1]` cosine → es la referencia del test de paridad.

### DD-VS-003: `delete_by_source` semántico, no sintáctico
El puerto expone `delete_by_source(source)` (intención), no un `where={...}` (sintaxis específica de Chroma). Cada backend traduce a su filtro de metadata. Evita filtrar la sintaxis de un backend al resto del sistema.

### DD-VS-004: Factory + setting, no inyección por host (por ahora)
Se elige `VectorStoreFactory.create(settings)` (espejo de `EmbeddingsFactory`) por simplicidad y consistencia. El puerto deja la puerta abierta a una variante "host-injected" por sesión/tenant (como `ToolProviderPort` de Fase Y) sin rehacer nada.

### DD-VS-005: Chroma sigue default; alternativas opt-in vía extras
Cero ruptura para usuarios actuales. Backends nuevos como extras (`[lancedb]`,`[sqlite-vec]`,`[qdrant]`,`[pgvector]`) con imports diferidos; ausencia → error claro hacia el extra. Mantiene la base slim (igual que los extras de la capa multimodal).

### DD-VS-006: Reubicar a `rag/stores/` con shim retrocompatible
Los adaptadores viven en `rag/stores/`; `rag/vector_store.py` re-exporta `ChromaVectorStore` (shim) para no romper imports existentes. Migración invisible.

### DD-VS-007: Config de conexión generalizada con alias
`vector_store_path`/`vector_store_url` reemplazan conceptualmente a `chroma_path`, que se conserva como alias retrocompatible. Permite backends servidor (Qdrant/pg) sin romper la config local de Chroma.

### DD-VS-008: API sync, paridad con hoy
El `ChromaVectorStore` actual es sync y los consumidores lo llaman sync; el puerto se mantiene sync para no propagar `async` por toda la capa RAG. (Una variante async puede añadirse después si un backend lo exige.)

---

## 5. Estructura del Código

```
prismal/
├── agents/extension/
│   ├── ports.py                       # + VectorStorePort
│   └── __init__.py                    # re-export VectorStorePort
├── rag/
│   ├── vector_store.py                # SHIM: re-export ChromaVectorStore (compat)
│   ├── vector_store_factory.py        # NUEVO: VectorStoreFactory
│   ├── stores/
│   │   ├── __init__.py
│   │   ├── chroma.py                  # MOVIDO desde vector_store.py (default)
│   │   ├── lancedb.py                 # NUEVO
│   │   ├── sqlite_vec.py              # NUEVO
│   │   ├── qdrant.py                  # NUEVO
│   │   ├── pgvector.py                # NUEVO
│   │   └── _normalize.py             # helpers de normalización de score
│   ├── engine.py / hyde.py / self_rag.py / hybrid.py / hierarchical.py /
│   │   multi_vector.py / multimodal.py / crag.py   # retipo a VectorStorePort
├── memory/
│   ├── long_term.py                   # factory + puerto
│   └── mongodb_store.py               # factory + puerto
├── core/
│   ├── config.py                      # vector_store_backend, *_path/*_url, alias chroma_path
│   └── exceptions.py                  # + VectorStoreBackendUnavailable
docs/vector-stores.md                  # NUEVO
examples/vector_store_lancedb.py       # NUEVO
tests/unit/rag/stores/                 # tests por adaptador + paridad de score
```

### Patrones Aplicados
- **Hexagonal Ports & Adapters** (Fase X/Y).
- **Factory** (espejo de `EmbeddingsFactory`).
- **Adapter** (normalización de score + traducción de filtros por backend).
- **Facade estable** (`VectorStorePort` con la API actual).

### Manejo de Errores
- Backend no instalado → `VectorStoreBackendUnavailable` (guía al extra).
- Fallo de operación del backend → mantener la convención actual (`ChromaStoreError` generaliza a `VectorStoreError`; los adaptadores envuelven excepciones nativas).
- `delete_by_source` best-effort (igual que hoy en Chroma).

---

## 6. Seguridad

### 6.1 Superficie de Ataque
- **Embebidos (LanceDB, sqlite-vec):** sin puerto de red → superficie mínima; elimina estructuralmente la familia de CVEs de servidor (p.ej. ChromaDB CVE-2026-45829).
- **Servidor (Qdrant, pgvector):** requieren auth + red privada; documentado como responsabilidad del operador.

### 6.2 Reglas Transversales
- Default embebido (chroma) → sin servidor por defecto.
- Logs no incluyen credenciales ni DSN; la config de conexión sensible se trata como secreto.
- Cada adaptador mantiene imports diferidos (no carga SDKs de backend salvo que se seleccione).

---

## 7. Observabilidad

### 7.1 Logs
- `vector_store.created{backend, collection}` en la factory (qué backend quedó activo).
- Paridad con los logs actuales: `vector_store_add_documents`, `vector_store_similarity_search`, `vector_store_delete_by_source`.

### 7.2 Métricas
```
prismal_vector_store_backend_active{backend}
prismal_vector_store_queries_total{backend, op}
prismal_vector_store_errors_total{backend, op}
```

---

## 8. Testing Strategy

- **Unit por adaptador:** add/search/delete con backend embebido real (LanceDB/sqlite-vec corren en proceso) o mock para servidor (Qdrant/pg).
- **Paridad de score:** dado un corpus fijo, los scores normalizados de cada adaptador caen en `[0,1]` y ordenan igual que Chroma (referencia). Tolerancia documentada.
- **Contrato del puerto:** `conforms_to(adaptador, VectorStorePort)` para los 5.
- **Retipo sin regresión:** suite RAG + memoria existente verde con default chroma.
- **Mock:** `FakeVectorStore(mapping)` para tests de patrones sin backend.

---

## 9. Plan de Rollout

1. Z1–Z2 (puerto + contrato de score) — aditivo.
2. Z3 (adaptador Chroma conforma + reubicación con shim) — sin cambio de comportamiento.
3. Z4–Z5 (factory + settings + retipo) — default chroma, suite verde.
4. Z3 (LanceDB, sqlite-vec, Qdrant, pgvector) — opt-in por extra.
5. Z7–Z8 (tests de paridad + docs).

Backout: cada adaptador es aislado; el default chroma + shim garantizan rollback trivial.

---

## 10. Preguntas Abiertas

- **PA-1:** ¿Tolerancia exacta del test de paridad de score (orden idéntico vs correlación ≥ umbral)? (Definir en Z2.)
- **PA-2:** ¿sqlite-vec se integra vía LangChain o vía SQL directo sobre la extensión? (Evaluar en Z3; afecta el adaptador.)
- **PA-3:** ¿Una variante async del puerto a futuro (Qdrant/pg async)? (Fuera de alcance; DD-VS-008.)
- **PA-4:** ¿Migración de datos Chroma→LanceDB como utilidad en esta fase o posterior? (Propuesta: posterior.)

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Diseño técnico inicial — `VectorStorePort` + adaptadores + factory |
