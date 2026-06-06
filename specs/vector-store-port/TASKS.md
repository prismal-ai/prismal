# Prismal Vector Store Port — Implementation Plan (TASKS)

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **PLAN** | `specs/vector-store-port/PLAN.md` |
| **Architecture** | `specs/vector-store-port/ARCHITECTURE.md` |
| **SPEC** | `specs/vector-store-port/SPEC.md` |

---

## 1. Resumen de Implementación

Fase Z convierte `ChromaVectorStore` (clase concreta) en un `VectorStorePort` con adaptadores intercambiables, seleccionables por `settings.vector_store_backend`. **Chroma sigue default.** El trabajo es mayormente:

- **Aditivo:** puerto, factory, 4 adaptadores nuevos, extras, settings, exceptions, docs, tests.
- **Reubicación con shim:** `rag/vector_store.py` → `rag/stores/chroma.py` (re-export retrocompatible).
- **Retipado sin lógica:** consumidores RAG + memoria cambian `ChromaVectorStore` → `VectorStorePort`.

Principio rector: **paridad de comportamiento con default chroma** + **contrato de score `[0,1]`** verificado contra Chroma como referencia.

---

## 2. Pre-requisitos

- Familia de puertos de Fase X/Y en `extension/ports.py` (`EmbeddingsPort`, etc.). ✅ Presente.
- `EmbeddingsFactory.create(settings)` como espejo de la nueva factory. ✅ Presente.
- `ChromaVectorStore` actual como referencia de API y de score `[0,1]`. ✅ Presente.
- Inyección por constructor ya presente en consumidores RAG + memoria. ✅ Presente.

---

## 3. Fases de Implementación

### FASE Z1 — `VectorStorePort`
#### Z1-01 — Declarar el puerto
- [ ] Añadir `VectorStorePort` (`@runtime_checkable Protocol`) a `extension/ports.py` con `collection_name`, `add_documents`, `similarity_search`, `delete_by_source`, `delete_collection`.
- [ ] `__all__` + re-export desde `extension/__init__.py`.
- **Done:** `conforms_to(ChromaVectorStore(), VectorStorePort)` es `True`.

### FASE Z2 — Contrato de score
#### Z2-01 — Definir contrato + helpers
- [ ] Documentar `score ∈ [0,1]` mayor=mejor en el docstring del puerto.
- [ ] Crear `rag/stores/_normalize.py` con `cosine_identity`, `from_l2`, `from_distance` helpers.
#### Z2-02 — Test de referencia
- [ ] Test que fija el corpus y captura el orden/score de Chroma como *golden* para paridad (Z7-02).
- **Done:** referencia reproducible.

### FASE Z3 — Adaptadores
#### Z3-01 — Reubicar Chroma (default) + shim
- [ ] Mover `ChromaVectorStore`/`ChromaStoreError` a `rag/stores/chroma.py`.
- [ ] `rag/vector_store.py` re-exporta (shim) → imports existentes no rompen.
- [ ] `ChromaStoreError` subclasa `VectorStoreError`.
- **Done:** suite existente verde sin cambios de comportamiento.
#### Z3-02 — `LanceDBVectorStore` (`[lancedb]`)
- [ ] Implementar adaptador embebido; import diferido; normalizar score; traducir `delete_by_source`.
#### Z3-03 — `SqliteVecVectorStore` (`[sqlite-vec]`)
- [ ] Implementar adaptador embebido; resolver vía SQL/extensión o integración LangChain (PA-2).
#### Z3-04 — `QdrantVectorStore` (`[qdrant]`)
- [ ] Implementar adaptador embebido/servidor; auth desde settings; normalizar score.
#### Z3-05 — `PgVectorStore` (`[pgvector]`)
- [ ] Implementar adaptador servidor (DSN); normalizar distancia `<=>`/`<->`.
- **Done (Z3):** los 5 adaptadores conforman el puerto; cada uno con su normalización documentada.

### FASE Z4 — Factory + Settings + Exceptions
#### Z4-01 — `VectorStoreFactory`
- [ ] Crear `rag/vector_store_factory.py::VectorStoreFactory.create(settings, collection)`; import diferido por backend; espejo de `EmbeddingsFactory`.
- [ ] `FakeVectorStore` para tests.
#### Z4-02 — Settings
- [ ] `vector_store_backend` (default `chroma`), `vector_store_path`, `vector_store_url` (+ credenciales opt).
- [ ] `chroma_path` como alias retrocompatible cuando backend == chroma.
#### Z4-03 — Exceptions
- [ ] `VectorStoreError`, `VectorStoreBackendUnavailable` (mensaje guía al extra).
- **Done:** `create()` selecciona backend; ausencia de extra → error claro.

### FASE Z5 — Retipado de consumidores
#### Z5-01 — RAG
- [ ] `engine`, `hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `crag`: hint → `VectorStorePort`; construcción por defecto vía factory.
#### Z5-02 — Memoria
- [ ] `memory/long_term.py`, `memory/mongodb_store.py`: default vía factory; hint → `VectorStorePort`.
- **Done:** `grep` no encuentra type hints `ChromaVectorStore` en consumidores (sí en el adaptador). Suite verde.

### FASE Z6 — Extras
#### Z6-01 — `pyproject.toml`
- [ ] Extras `[lancedb]`, `[sqlite-vec]`, `[qdrant]`, `[pgvector]`; base sin nuevas deps obligatorias; actualizar `all` si aplica.
- [ ] `mypy` overrides para los nuevos SDKs opcionales si hace falta.

### FASE Z7 — Tests
#### Z7-01 — Unit por adaptador
- [ ] add/search/delete por adaptador (embebidos reales; servidor con mock).
#### Z7-02 — Paridad de score
- [ ] Orden top-k de cada adaptador vs Chroma (referencia) dentro de tolerancia declarada.
#### Z7-03 — Puerto + retipo
- [ ] `conforms_to` de los 5; suite RAG+memoria verde con default chroma; `FakeVectorStore` en patrones.

### FASE Z8 — Docs + Ejemplo
#### Z8-01 — Documentación
- [ ] `docs/vector-stores.md`: selección, extras, contrato de score, backend servidor (auth/red), migración.
#### Z8-02 — Ejemplo
- [ ] `examples/vector_store_lancedb.py`.

### HARDENING
- [ ] Coverage ≥ 85% en `rag/stores/**` + factory.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; `pytest -m "not live_api"` 100%.
- [ ] `CLAUDE.md` (sección rag/ + extras) y `README.md` actualizados.

---

## 4. Dependencias Inter-Tareas

```
Z1 (puerto)
 └─▶ Z2 (contrato score)
       └─▶ Z3-01 (Chroma reubicado + shim)  ──▶ Z5 (retipo) ──▶ Z7-03
             └─▶ Z3-02..05 (adaptadores)    ──▶ Z7-01, Z7-02 (paridad)
Z4 (factory+settings+exc)  ──▶ Z5 (consumidores usan factory)
Z6 (extras)                ──▶ Z3-02..05 (resolución de deps)
Z8 (docs/ejemplo)          [tras Z3..Z5]
```

Ruta crítica: **Z1 → Z2 → Z3-01 → Z4 → Z5 → Z7** (default chroma verde). Los adaptadores nuevos (Z3-02..05) y su paridad son paralelizables tras Z4.

---

## 5. Matriz Tareas ↔ Requisitos

| Tarea | RF cubiertos |
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

Cobertura: RF-VS-001..015 mapeados.

---

## 6. Matriz de Riesgos

| Riesgo | Mitigación | Tarea |
|---|---|---|
| Score no comparable rompe hybrid | Contrato `[0,1]` + normalización por adaptador + paridad | Z2, Z7-02 |
| Reubicación rompe imports | Shim en `rag/vector_store.py` | Z3-01 |
| Backend opcional ausente | Import diferido + `VectorStoreBackendUnavailable` | Z4-03 |
| `chroma_path` se rompe | Alias retrocompatible | Z4-02 |
| sqlite-vec sin integración LangChain limpia | Evaluar SQL directo (PA-2) | Z3-03 |
| Servidor sin auth (qdrant/pg) | Documentar auth+red; default embebido | Z8-01 |

---

## 7. Definición de Done (Global de Fase Z)

- [ ] `VectorStorePort` declarado/re-exportado; Chroma conforma sin cambio de comportamiento.
- [ ] 4 adaptadores nuevos conformes (LanceDB, sqlite-vec, Qdrant, pgvector).
- [ ] Contrato de score `[0,1]` verificado por paridad contra Chroma.
- [ ] `VectorStoreFactory` + `settings.vector_store_backend` (default chroma) + config generalizada; `chroma_path` alias.
- [ ] RAG + memoria retipados al puerto; sin type hints `ChromaVectorStore` en consumidores.
- [ ] Extras opcionales; base slim; imports diferidos.
- [ ] `FakeVectorStore` + tests; coverage ≥ 85%.
- [ ] `docs/vector-stores.md` + ejemplo.
- [ ] `pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` actualizados; PR mergeado con review.

---

## 8. Estimación de Esfuerzo

| Sub-fase | Esfuerzo |
|---|---|
| Z1 Puerto | 0.2 sem |
| Z2 Contrato score | 0.3 sem |
| Z3 Adaptadores (Chroma+4) | 1.5 sem |
| Z4 Factory+settings+exc | 0.5 sem |
| Z5 Retipo consumidores | 0.5 sem |
| Z6 Extras | 0.2 sem |
| Z7 Tests + paridad | 0.6 sem |
| Z8 Docs + ejemplo | 0.4 sem |
| Hardening | 0.5 sem |
| **Total** | **~4.7 sem** |

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Plan de implementación inicial — Vector Store Port |
