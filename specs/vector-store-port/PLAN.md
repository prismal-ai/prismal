# Prismal — Vector Store Port (base vectorial intercambiable)

## Strategic Plan / Product Requirements Document (PLAN)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-06-05 |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Documentos relacionados** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |
| **Fase** | Z — Vector Store Port (sucesora de Fase Y — Tool Provider Injection) |

---

## 1. Resumen Ejecutivo

Hoy toda la búsqueda vectorial de prismal pasa por `prismal/rag/vector_store.py::ChromaVectorStore`, un *facade* delgado sobre `langchain_community.vectorstores.Chroma`. El acoplamiento a ChromaDB es **nominal, no estructural**: el wrapper ya aísla Chroma (un único import de la lib) y **todos los consumidores reciben el store por inyección de constructor** (`engine`, `hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `CRAGPipeline`, y la memoria `long_term`/`mongodb_store`). Lo que ata a Chroma es que el wrapper es una **clase concreta** (no un puerto), que los consumidores **tipan contra ella**, y que el constructor **hardcodea** `Chroma(...)` + `settings.chroma_path`.

Esta fase introduce un **`VectorStorePort`** (Protocol) y una **`VectorStoreFactory`** seleccionable por `settings.vector_store_backend`, con adaptadores para **Chroma (existente), LanceDB, sqlite-vec, Qdrant y pgvector**. Cubre **RAG y la capa de memoria**. El cambio es **aditivo y retrocompatible**: **Chroma sigue siendo el default** (cero ruptura para usuarios actuales), las alternativas son opt-in vía extras, y los consumidores solo cambian su *type hint* (`ChromaVectorStore` → `VectorStorePort`), no su lógica.

El beneficio: prismal deja de estar amarrado a una base vectorial. Permite elegir backends **embebidos sin servidor HTTP** (LanceDB, sqlite-vec) que reducen estructuralmente la superficie de ataque — relevante tras la CVE crítica del *server* de ChromaDB (CVE-2026-45829), que prismal no expone hoy pero cuya familia de riesgo desaparece con un backend sin servidor.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

- **`ChromaVectorStore` ya es un facade estrecho** con API: `add_documents`, `similarity_search` → `(Document, score)`, `delete_by_source`, `delete_collection`, `collection_name`. Un solo sitio importa `Chroma`.
- **Inyección por constructor ya presente** en todos los patrones RAG avanzados y en memoria: reciben `vector_store: ChromaVectorStore`. La inversión está 80% hecha.
- **Acoplamiento residual:**
  1. El wrapper es **clase concreta**; los consumidores tipan contra `ChromaVectorStore` (leaky: el nombre dice "Chroma").
  2. El constructor **construye `Chroma(...)`** y lee **`settings.chroma_path`** — no hay punto de selección de backend.
  3. La config solo modela `chroma_path` (persistencia local); un backend servidor (Qdrant/pgvector) necesita URL/credenciales.
- **La memoria también lo usa:** `long_term.py` y `mongodb_store.py` construyen `ChromaVectorStore` por defecto → cualquier abstracción debe servir RAG **y** memoria.

### 2.2 Problema

1. **Lock-in de proveedor:** cambiar de base vectorial hoy obliga a tocar el wrapper, los type hints de ~9 módulos, y la config. No es un *swap* de configuración.
2. **Sin opción de menor superficie:** no se puede elegir un backend embebido sin servidor (LanceDB/sqlite-vec) para reducir el riesgo de la familia de CVEs de servidor.
3. **Semántica de score no contractual:** `similarity_search_with_score` devuelve cosine `[0,1]` (mayor=mejor) en Chroma pero **distancia** (menor=mejor) en otros backends; `hybrid.py` fusiona scores asumiendo escala. Sin un contrato normalizado, cambiar backend rompe el ranking silenciosamente.

### 2.3 Oportunidad

El patrón ya está probado en el repo: `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`, `ToolProviderPort` (Fase X/Y) en `prismal/agents/extension/ports.py`. `EmbeddingsFactory.create(settings)` ya es el espejo exacto de la factory que necesitamos. El esfuerzo es bajo: **un puerto + una factory + 4 adaptadores nuevos + retipado de consumidores**, sin cambiar lógica de los patrones RAG.

---

## 3. Usuarios Objetivo

### Persona 1: Operator / Platform Engineer
- **Necesidad:** Elegir la base vectorial por configuración (`vector_store_backend`) según seguridad, despliegue o escala, sin tocar código.
- **Frecuencia:** Por entorno/despliegue.

### Persona 2: Security-conscious Adopter
- **Necesidad:** Usar un backend embebido sin servidor HTTP (LanceDB/sqlite-vec) para minimizar superficie.
- **Frecuencia:** Decisión de arquitectura inicial.

### Persona 3: RAG Engineer
- **Necesidad:** Que los patrones (hybrid, hierarchical, multi_vector, self_rag, HyDE) funcionen idénticos con cualquier backend, con scores comparables.
- **Frecuencia:** Continua.

### Persona 4: Core Maintainer / Test Author
- **Necesidad:** Inyectar un `FakeVectorStore` determinista en tests, sin levantar Chroma ni otro backend.
- **Frecuencia:** Diaria.

---

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Backend intercambiable | Cambiar de base vectorial sin tocar código (solo `settings`) | Sí | Fase Z |
| Desacoplar el tipo | Consumidores tipados contra `VectorStorePort`, no `ChromaVectorStore` | 100% de consumidores | Fase Z |
| Cobertura RAG + memoria | rag/ y memory/ usan el puerto | Ambos | Fase Z |
| Contrato de score | Score normalizado `[0,1]` (mayor=mejor) en todos los adaptadores | Verificado por test | Fase Z |
| Backward compatibility | Default = chroma; suite existente sin cambios | 100% | Global |
| Base slim | Backends nuevos como extras opcionales | `[lancedb]`,`[sqlite-vec]`,`[qdrant]`,`[pgvector]` | Fase Z |
| Cobertura de tests | Branch coverage módulos nuevos | ≥ 85% | Global |

### 4.2 Objetivos de Usuario

| Objetivo | Indicador |
|---|---|
| Cambiar de backend por config | `settings.vector_store_backend = "lancedb"` |
| Backend sin servidor | LanceDB / sqlite-vec disponibles y embebidos |
| Patrones RAG idénticos | hybrid/hierarchical/… sin cambios; scores comparables |
| Tests sin backend real | `FakeVectorStore` inyectable |

---

## 5. Alcance

### 5.1 In Scope (Fase Z)

**Z1 — `VectorStorePort` (`prismal/agents/extension/ports.py`):**
- [ ] `Protocol` con `add_documents`, `similarity_search`, `delete_by_source`, `delete_collection`, `collection_name`.
- [ ] Re-export desde `extension/__init__.py`. Helper `conforms_to`.

**Z2 — Contrato de score normalizado:**
- [ ] Definir: `similarity_search` devuelve `(Document, score)` con `score ∈ [0,1]`, mayor = más relevante.
- [ ] Cada adaptador documenta su métrica nativa y la fórmula de normalización.

**Z3 — Adaptadores:**
- [ ] `ChromaVectorStore` (existente) conforma el puerto (retipo, sin cambio de lógica; queda como default).
- [ ] `LanceDBVectorStore` (embebido).
- [ ] `SqliteVecVectorStore` (embebido).
- [ ] `QdrantVectorStore` (embebido o servidor).
- [ ] `PgVectorStore` (servidor Postgres).
- [ ] Todos envuelven la integración LangChain correspondiente; imports diferidos; normalización de score.

**Z4 — `VectorStoreFactory` + settings:**
- [ ] `VectorStoreFactory.create(settings, collection_name)` (espejo de `EmbeddingsFactory`).
- [ ] `settings.vector_store_backend: Literal["chroma","lancedb","sqlite_vec","qdrant","pgvector"] = "chroma"`.
- [ ] Config de conexión generalizada: `vector_store_path` (embebido) + `vector_store_url`/credenciales (servidor); `chroma_path` se mantiene como alias retrocompatible.

**Z5 — Retipado de consumidores (RAG + memoria):**
- [ ] `engine`, `hyde`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `crag` → `vector_store: VectorStorePort`.
- [ ] `memory/long_term.py`, `memory/mongodb_store.py` → construir vía factory y tipar al puerto.

**Z6 — Extras y dependencias:**
- [ ] Extras `[lancedb]`, `[sqlite-vec]`, `[qdrant]`, `[pgvector]`; base sin nuevas deps obligatorias.

**Z7 — Tests:**
- [ ] `FakeVectorStore` para fixtures.
- [ ] Test de paridad de score (Chroma vs adaptadores) y de comportamiento del puerto.

**Z8 — Docs y ejemplos:**
- [ ] `docs/vector-stores.md` (selección, extras, contrato de score, migración).
- [ ] `examples/vector_store_lancedb.py`.

### 5.2 Out of Scope

- Migración automática de datos entre backends (export/import queda como utilidad futura).
- Cambiar el default fuera de Chroma (decisión: mantener Chroma default).
- FAISS como adaptador en esta entrega (no seleccionado; puede añadirse luego conformando el puerto).
- Inyección por host/sesión del store (factory+setting es suficiente ahora; el puerto deja la puerta abierta a la variante host como Fase Y).
- Sharding/replicación o tuning de índices por backend.

### 5.3 Futuras Consideraciones

- Variante "host-injected" del store (como `ToolProviderPort`) para backend por tenant.
- Utilidad de migración `export → import` entre backends.
- Adaptador FAISS / Milvus / Weaviate adicionales.
- Búsqueda híbrida nativa donde el backend la soporte (Qdrant, pgvector).

---

## 6. Requisitos Funcionales (Resumen — detalle en `SPEC.md`)

| ID | Requisito | Prioridad |
|---|---|---|
| RF-VS-001 | `VectorStorePort` declara la API estrecha actual de `ChromaVectorStore` | `MUST` |
| RF-VS-002 | `similarity_search` devuelve score normalizado `[0,1]` mayor=mejor en todos los adaptadores | `MUST` |
| RF-VS-003 | `ChromaVectorStore` conforma el puerto sin cambiar comportamiento (default) | `MUST` |
| RF-VS-004 | Adaptador `LanceDBVectorStore` (embebido) | `MUST` |
| RF-VS-005 | Adaptador `SqliteVecVectorStore` (embebido) | `MUST` |
| RF-VS-006 | Adaptador `QdrantVectorStore` (embebido/servidor) | `SHOULD` |
| RF-VS-007 | Adaptador `PgVectorStore` (servidor) | `SHOULD` |
| RF-VS-008 | `VectorStoreFactory.create(settings, collection_name)` selecciona backend | `MUST` |
| RF-VS-009 | `settings.vector_store_backend` (default `chroma`) + config de conexión generalizada | `MUST` |
| RF-VS-010 | Consumidores RAG tipan contra `VectorStorePort` | `MUST` |
| RF-VS-011 | Memoria (`long_term`, `mongodb_store`) usa factory + puerto | `MUST` |
| RF-VS-012 | Backends nuevos como extras opcionales; imports diferidos | `MUST` |
| RF-VS-013 | `FakeVectorStore` para tests | `SHOULD` |
| RF-VS-014 | `chroma_path` retrocompatible como alias de la nueva config | `MUST` |
| RF-VS-015 | Docs + ejemplo ejecutable | `SHOULD` |

---

## 7. Requisitos No Funcionales

### Rendimiento
- El puerto no añade overhead apreciable (delegación directa al backend).
- Cada adaptador respeta el coste nativo de su `similarity_search`.

### Seguridad
- Backends embebidos (LanceDB, sqlite-vec) **no abren puertos de red** → menor superficie.
- Backends servidor (Qdrant, pgvector) deben soportar auth y red privada (responsabilidad del operador, documentada).
- El contrato de score normalizado no debe filtrar rutas/credenciales en logs.

### Compatibilidad
- `prismal/` sigue siendo namespace package PEP 420.
- Default `chroma` → cero cambios para usuarios actuales.
- `filterwarnings=error`: imports de backends opcionales **diferidos**; ausentes → error claro guiando a instalar el extra.

### Correctitud
- Contrato de score verificado por test de paridad (Chroma como referencia `[0,1]`).
- `delete_by_source` semántico (cada adaptador traduce su filtro de metadata).

### Mantenibilidad
- Coverage ≥ 85% en módulos nuevos; `ruff` + `mypy --strict` + `bandit` clean.
- API pública (`VectorStorePort`, adaptadores, factory) versionada (SemVer; breaking → minor + deprecación 1 release).

---

## 8. Restricciones y Dependencias

- Python 3.13+, `uv`. Sin nuevas deps obligatorias en base.
- Adaptadores envuelven integraciones LangChain (`langchain-chroma`/community, `langchain-qdrant`, `langchain-postgres`, LanceDB, sqlite-vec) — cada una en su extra.
- La semántica de score y el filtro de metadata varían por backend → la normalización y la traducción viven en cada adaptador, no en el puerto.

| Dependencia | Tipo | Uso | Extra |
|---|---|---|---|
| `langchain-chroma` / community | Existente | Adaptador Chroma (default) | base |
| `lancedb` | Nueva (opcional) | Adaptador LanceDB | `[lancedb]` |
| `sqlite-vec` | Nueva (opcional) | Adaptador sqlite-vec | `[sqlite-vec]` |
| `langchain-qdrant` / `qdrant-client` | Nueva (opcional) | Adaptador Qdrant | `[qdrant]` |
| `langchain-postgres` / `psycopg` | Nueva (opcional) | Adaptador pgvector | `[pgvector]` |
| `EmbeddingsFactory` | Existente | Embeddings comunes a todos los backends | base |

---

## 9. User Stories

**US-VS-001:** Como Operator, quiero cambiar de base vectorial por configuración.
```python
# settings.vector_store_backend = "lancedb"
store = VectorStoreFactory.create(settings, collection_name="docs")
```
- [ ] Sin tocar código de RAG ni memoria.

**US-VS-002:** Como Security-conscious Adopter, quiero un backend sin servidor HTTP.
- [ ] `lancedb` o `sqlite_vec` funcionan embebidos, sin abrir puertos.

**US-VS-003:** Como RAG Engineer, quiero que los patrones funcionen igual con cualquier backend.
- [ ] hybrid/hierarchical/multi_vector/self_rag/HyDE sin cambios; scores comparables `[0,1]`.

**US-VS-004:** Como Core Maintainer, quiero tests sin backend real.
```python
engine = RAGEngine(vector_store=FakeVectorStore({...}))
```
- [ ] Determinista, sin I/O.

---

## 10. Riesgos y Mitigaciones

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Score no comparable entre backends rompe ranking (hybrid) | Alta | Alto | Contrato `[0,1]` + normalización por adaptador + test de paridad |
| Filtro de metadata (`where`) difiere por backend | Media | Medio | Puerto expone `delete_by_source` semántico; adaptador traduce |
| Backend opcional ausente rompe import | Media | Medio | Imports diferidos + error claro hacia el extra |
| Config de conexión rompe `chroma_path` | Baja | Medio | `chroma_path` como alias retrocompatible |
| Memoria y RAG divergen en el contrato | Baja | Medio | Un único puerto sirve ambos; tests en los dos |
| Qdrant/pgvector exponen servidor sin auth | Media | Alto | Documentar auth + red privada; default sigue embebido (chroma) |

---

## 11. Timeline Estimado

| Fase | Duración | Entregable |
|---|---|---|
| Z1 — Puerto | 0.2 sem | `VectorStorePort` + re-export |
| Z2 — Contrato de score | 0.3 sem | Definición + test de referencia |
| Z3 — Adaptadores | 1.5 sem | Chroma (retipo) + LanceDB + sqlite-vec + Qdrant + pgvector |
| Z4 — Factory + settings | 0.5 sem | `VectorStoreFactory` + config generalizada |
| Z5 — Retipado consumidores | 0.5 sem | RAG + memoria contra el puerto |
| Z6 — Extras | 0.2 sem | `[lancedb]`/`[sqlite-vec]`/`[qdrant]`/`[pgvector]` |
| Z7 — Tests + paridad | 0.6 sem | `FakeVectorStore` + paridad de score |
| Z8 — Docs + ejemplo | 0.4 sem | `docs/vector-stores.md` + ejemplo |
| Hardening | 0.5 sem | Coverage ≥ 85%, mypy/bandit, validación cruzada |
| **Total** | **~4.7 sem** | Base vectorial intercambiable, Chroma default |

---

## 12. Definición de Done (Global de Fase Z)

- [ ] `VectorStorePort` declarado y re-exportado; `ChromaVectorStore` conforma sin cambio de comportamiento.
- [ ] Adaptadores LanceDB, sqlite-vec, Qdrant, pgvector implementados y conformes.
- [ ] Contrato de score `[0,1]` verificado por test de paridad contra Chroma.
- [ ] `VectorStoreFactory.create` + `settings.vector_store_backend` (default `chroma`).
- [ ] Config de conexión generalizada; `chroma_path` retrocompatible.
- [ ] RAG (engine, hyde, self_rag, hybrid, hierarchical, multi_vector, multimodal, crag) y memoria (long_term, mongodb_store) tipados contra el puerto.
- [ ] Extras opcionales; base slim; imports diferidos.
- [ ] `FakeVectorStore` + tests; coverage ≥ 85%.
- [ ] `docs/vector-stores.md` + ejemplo ejecutable.
- [ ] `uv run pytest -m "not live_api"` 100%; `ruff`/`mypy --strict`/`bandit` clean.
- [ ] `CLAUDE.md` + `README.md` actualizados.
- [ ] PR mergeado con review aprobado.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-06-05 | Ernesto Crespo | Versión inicial — base vectorial intercambiable vía `VectorStorePort` |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |
| Security Lead | — | | ☐ Pendiente |
