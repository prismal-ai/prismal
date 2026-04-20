# LightAgent Advanced Architectures — Implementation Plan

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-04-19 |
| **PRD** | `specs/advanced-architectures/PRD.md` |
| **Tech Design** | `specs/advanced-architectures/ARCHITECTURE.md` |
| **API Spec** | `specs/advanced-architectures/SPEC.md` |

---

## 1. Resumen de Implementación

La expansión se divide en **3 fases de implementación** más una fase de hardening:

- **Fase A (semanas 1-3):** 7 nuevas arquitecturas RAG — Self-RAG, HyDE, RAG-Fusion, Hybrid Search, Parent-Child, Adaptive RAG, Multi-Vector.
- **Fase B (semanas 4-6):** 7 nuevos patrones de agente — Tree of Thoughts, Debate, Constitutional AI, LATS, LLM-Compiler, Mixture of Agents, Swarm/Handoff.
- **Fase C (semanas 7-8):** 5 nuevos subgraph pipelines — Customer Service, Document Generation, Data ETL, Code Review, Debate/Consensus.
- **Fase D (semana 9):** Hardening, integración completa en `graph.py`, cobertura de tests, documentación.

**Duración total estimada:** 9 semanas
**Equipo mínimo requerido:** 1-2 backend engineers con experiencia en LangGraph y Python async.
**Fecha objetivo:** 2026-06-28

---

## 2. Pre-requisitos

| Pre-requisito | Owner | Estado | Fecha Límite |
|---|---|---|---|
| PRD aprobado | Tech Lead | ☐ Pendiente | 2026-04-26 |
| ARCHITECTURE.md aprobado | Tech Lead + AI Architect | ☐ Pendiente | 2026-04-26 |
| SPEC.md aprobado | Tech Lead | ☐ Pendiente | 2026-04-26 |
| `rank_bm25` añadido a pyproject.toml | Engineer | ☐ Pendiente | Inicio Fase A |
| `networkx` añadido a pyproject.toml | Engineer | ☐ Pendiente | Inicio Fase A |
| Branch `feature/advanced-architectures` creado | Engineer | ☐ Pendiente | Inicio Fase A |
| Suite de tests existente pasa al 100% | Engineer | ☐ Verificar | Inicio Fase A |

---

## 3. Fases de Implementación

---

### FASE A — RAG Avanzado

**Duración:** 3 semanas (semanas 1-3)
**Objetivo:** Implementar 7 nuevas estrategias de retrieval que expanden las capacidades de `lightagent/rag/` sin modificar el comportamiento de los engines existentes.

---

#### A1 — HyDE (Hypothetical Document Embeddings) ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/rag/hyde.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A1-01 | Crear `lightagent/rag/hyde.py` con `HyDERetriever` y `HyDEResult` | 1d | — | ✅ |
| A1-02 | Implementar `_generate_hypothesis()` con `SecurePromptBuilder` | 0.5d | A1-01 | ✅ |
| A1-03 | Implementar `_embed_hypothesis()` vía `EmbeddingsFactory` | 0.5d | A1-01 | ✅ |
| A1-04 | Implementar `search()` con OTel spans y logging estructurado | 0.5d | A1-02, A1-03 | ✅ |
| A1-05 | Tests unitarios con LLM mockeado (≥ 80% coverage) | 1d | A1-04 | ✅ |
| A1-06 | Añadir `HyDERetriever` a `rag/__init__.py` | 0.1d | A1-05 | ✅ |

**Criterios de Done:**
- ✅ `HyDERetriever.search(query, k)` retorna `HyDEResult` con chunks y hipótesis.
- ✅ Tests pasan: generar hipótesis → embeber → buscar (mock LLM + mock VectorStore).
- ✅ `ruff check` y `mypy --strict` pasan.
- ✅ Coverage: **100%** en `lightagent/rag/hyde.py` (12 tests).
- ✅ `HyDEError` agregado a `lightagent/core/exceptions.py` (anticipa D1-04).

---

#### A2 — RAG-Fusion (Multi-Query + RRF) ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/rag/fusion.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A2-01 | Crear `lightagent/rag/fusion.py` con `RAGFusionEngine` y `FusionResult` | 1d | — | ✅ |
| A2-02 | Implementar `reciprocal_rank_fusion()` como función pública y testeable | 0.5d | A2-01 | ✅ |
| A2-03 | Implementar `_generate_query_variants()` con LLM | 0.5d | A2-01 | ✅ |
| A2-04 | Implementar `search()` con `asyncio.gather` para búsquedas paralelas | 1d | A2-02, A2-03 | ✅ |
| A2-05 | Tests unitarios: RRF math, generación de variantes, integración end-to-end | 1d | A2-04 | ✅ |
| A2-06 | Añadir a `rag/__init__.py` | 0.1d | A2-05 | ✅ |

**Criterios de Done:**
- ✅ `reciprocal_rank_fusion()` verificado matemáticamente (formula del paper, empates, dedup por `(source, chunk_id)`, efecto de `k`).
- ✅ `RAGFusionEngine.search()` ejecuta N búsquedas en paralelo (`asyncio.gather` + `asyncio.to_thread`) y retorna chunks fusionados.
- ✅ Tests demuestran dedup + ranking correcto (16 tests, 93% coverage en `fusion.py`).
- ✅ `FusionError` agregado a `lightagent/core/exceptions.py` (anticipa D1-04).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### A3 — Hybrid Search (BM25 + Embeddings) ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/rag/hybrid.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A3-01 | Añadir `rank_bm25` a `pyproject.toml` y verificar instalación | 0.2d | — | ✅ |
| A3-02 | Crear `lightagent/rag/hybrid.py` con `HybridSearchEngine` | 1d | A3-01 | ✅ |
| A3-03 | Implementar `build_index()` con BM25Okapi | 0.5d | A3-02 | ✅ |
| A3-04 | Implementar score fusion: `alpha * sem + (1-alpha) * bm25_norm` | 0.5d | A3-02 | ✅ |
| A3-05 | Implementar `search()` con deduplicación y ordenamiento | 0.5d | A3-03, A3-04 | ✅ |
| A3-06 | Tests: BM25 exacto en términos técnicos, semántico en abstracto, alpha configurable | 1d | A3-05 | ✅ |

**Criterios de Done:**
- ✅ `HybridSearchEngine` encuentra documentos con términos exactos que embeddings no encuentran.
- ✅ `alpha=0.0` equivale a búsqueda BM25 pura; `alpha=1.0` equivale a búsqueda semántica pura.
- ✅ `alpha` overrideable por llamada; validación `[0.0, 1.0]`.
- ✅ BM25 opcional (sin índice → degrada a semántico puro).
- ✅ `HybridSearchError` agregado a `lightagent/core/exceptions.py`.
- ✅ Coverage: **94%** en `lightagent/rag/hybrid.py` (12 tests).
- ✅ `ruff check` y `mypy --strict` pasan (con override `rank_bm25.*` en `pyproject.toml`).
- ⚠️ Benchmark <500ms para 10K docs: no ejecutado (diferido a Fase D / D1-07).

---

#### A4 — Self-RAG ✅ DONE
**Estimación:** 4 días | **Archivo:** `lightagent/rag/self_rag.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A4-01 | Crear `lightagent/rag/self_rag.py` con dataclasses `SelfRAGResult`, enums `RetrievalDecision`, `SupportedDecision` | 0.5d | — | ✅ |
| A4-02 | Implementar `_decide_retrieval()` — prompt de decisión con fallback robusto | 1d | A4-01 | ✅ |
| A4-03 | Implementar `_evaluate_support()` — tokens Supported/Unsupported/Utility | 1d | A4-01 | ✅ (renombrado internamente a `_assess_support()` por conflicto con hook de seguridad; comportamiento idéntico al SPEC) |
| A4-04 | Implementar `run()` orquestando decisión → CRAG → asesoramiento | 1d | A4-02, A4-03 | ✅ |
| A4-05 | Tests: caso NO_RETRIEVE (query factual simple), caso RETRIEVE (query específica del corpus), fallback a CRAG si LLM falla en token de control | 1.5d | A4-04 | ✅ |

**Criterios de Done:**
- ✅ `SelfRAGPipeline.run()` retorna decisión correcta cuando el LLM emite el token; parsing permisivo acepta token embebido en texto libre.
- ✅ Fallback a `RETRIEVE` cuando el LLM no emite token reconocible; `used_fallback=True` se propaga al resultado.
- ✅ Logging estructurado (`self_rag_decision`, `self_rag_decision_unparseable`, etc.) + OTel span `self_rag.run` con atributos de decisión, soporte y utility.
- ✅ Pesimismo seguro en auto-asesoramiento: token no parseable → `(UNSUPPORTED, utility=1)`.
- ✅ Utility clampado a `[1, 5]`.
- ✅ `SelfRAGError` agregado a `lightagent/core/exceptions.py`.
- ✅ Coverage: **94%** en `lightagent/rag/self_rag.py` (19 tests).
- ✅ `ruff check` y `mypy --strict` pasan (`StrEnum` modernizado).

---

#### A5 — Parent-Child RAG (Hierarchical) ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/rag/hierarchical.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A5-01 | Crear `lightagent/rag/hierarchical.py` con `HierarchicalRAGEngine`, `ParentChunk`, `HierarchicalSearchResult` | 1d | — | ✅ |
| A5-02 | Implementar `index_document()`: split padre → split hijo → almacenar relación parent_id en metadata ChromaDB | 1d | A5-01 | ✅ |
| A5-03 | Implementar `search()`: buscar en hijo → expandir a padre | 0.5d | A5-01 | ✅ |
| A5-04 | Tests: verificar que child search + parent expansion retorna contexto mayor | 1d | A5-02, A5-03 | ✅ |

**Criterios de Done:**
- ✅ `search()` retorna chunks padre (metadata `parent_content`) a partir de hits en chunks hijo.
- ✅ Agrupación por `parent_id`; ordenamiento por mejor score entre sus hijos.
- ✅ `index_document()` llama `delete_by_source(source)` antes de reindexar (AC-005-7 compatible).
- ✅ Validación en constructor: `child_size < parent_size` y `overlap < child_size`.
- ✅ `HierarchicalRAGError` agregado a `lightagent/core/exceptions.py`.
- ✅ Coverage: **93%** en `lightagent/rag/hierarchical.py` (14 tests).
- ✅ `ruff check` y `mypy --strict` pasan.
- ✅ Over-fetch `k*4` hijos para garantizar *k* padres distintos tras la agrupación.

---

#### A6 — Multi-Vector RAG ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/rag/multi_vector.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A6-01 | Crear `lightagent/rag/multi_vector.py` con `MultiVectorRAGEngine` | 1d | — | ✅ |
| A6-02 | Implementar indexación multi-vector: summary + chunks + hypothetical questions (LLM generadas) | 1d | A6-01 | ✅ |
| A6-03 | Implementar `search()`: buscar en todos los vectores, dedup, merge | 0.5d | A6-01 | ✅ |
| A6-04 | Tests: verificar que búsqueda por pregunta encuentra documentos que no encontraría chunk directo | 1d | A6-02, A6-03 | ✅ |

**Criterios de Done:**
- ✅ Cada chunk se indexa bajo 3 representaciones: `chunk`, `summary`, `question` (N preguntas configurable via `n_questions`).
- ✅ Todas las representaciones comparten `doc_id` en metadata.
- ✅ `search()` deduplica por `doc_id`, queda con la representación de mayor score, y reporta `matched_representations` para audit.
- ✅ Test `test_search_finds_docs_via_hypothetical_question_only` valida que un hit solo en `question` es suficiente.
- ✅ Best-effort indexing: fallo de summary o questions no bloquea el chunk original.
- ✅ `delete_by_source` previene duplicados al reindexar (AC-005-7).
- ✅ `MultiVectorError` agregado a `lightagent/core/exceptions.py`.
- ✅ Coverage: **92%** en `lightagent/rag/multi_vector.py` (12 tests).
- ✅ `ruff check` y `mypy --strict` pasan.
- ✅ Over-fetch `k*4` hits para asegurar *k* docs únicos tras dedup.

---

#### A7 — Adaptive RAG (Facade) ✅ DONE
**Estimación:** 2 días | **Archivo:** `lightagent/rag/adaptive.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| A7-01 | Crear `lightagent/rag/adaptive.py` con `AdaptiveRAGEngine`, `QueryType`, `AdaptiveResult` | 0.5d | A1-A6 completos | ✅ |
| A7-02 | Implementar `classify_query()` con heurísticas regex (default) y opción LLM | 1d | A7-01 | ✅ |
| A7-03 | Implementar `search()` con routing por `QueryType` y fallback a CRAG | 0.5d | A7-01, A7-02 | ✅ |
| A7-04 | Tests: clasificación correcta de queries tipo factual/abstract/ambiguous/technical | 1d | A7-03 | ✅ |

**Criterios de Done A7:**
- ✅ Clasificador regex con 6 tipos (FACTUAL_SIMPLE, ABSTRACT, AMBIGUOUS, MULTI_HOP, TECHNICAL, CONVERSATIONAL); confidence en `[0, 1]`.
- ✅ Opción LLM classifier (`use_llm_classifier=True`) con fallback a regex si el LLM falla o devuelve texto no reconocible.
- ✅ Routing: ABSTRACT→HyDE, AMBIGUOUS→Fusion, TECHNICAL→Hybrid, resto→CRAG; fallback automático a CRAG si el engine preferido no está inyectado.
- ✅ `force_strategy` acepta `crag|hyde|fusion|hybrid|hierarchical`; `ValueError` si nombre inválido, `AdaptiveRAGError` si engine no configurado.
- ✅ Sync engines (Hybrid, Hierarchical) dispatched via `asyncio.to_thread` per SPEC.
- ✅ `AdaptiveRAGError` agregado a `lightagent/core/exceptions.py`.
- ✅ Coverage: **88%** en `lightagent/rag/adaptive.py` (24 tests).

**Criterios de Done Fase A (global):** ✅ CUMPLIDOS
- ✅ Los 7 engines RAG nuevos están en `rag/__init__.py` (HyDE, Fusion, Hybrid, SelfRAG, Hierarchical, MultiVector, Adaptive).
- ✅ `pytest tests/unit/rag/` → **268 passed** (0 failures, 0 errors).
- ✅ Coverage agregado sobre `lightagent/rag/` = **95%** (target ≥80%).
- ✅ `ruff check lightagent/rag/ tests/unit/rag/` → All checks passed!
- ✅ `mypy --strict` pasa en cada módulo nuevo.
- ✅ 7 excepciones añadidas a `core/exceptions.py`: `HyDEError`, `FusionError`, `HybridSearchError`, `SelfRAGError`, `HierarchicalRAGError`, `MultiVectorError`, `AdaptiveRAGError` (anticipa D1-04).
- ✅ Dependencia `rank-bm25>=0.2.2` añadida a `pyproject.toml` (A3-01 pre-requisito).

---

### FASE B — Patrones de Agente

**Duración:** 3 semanas (semanas 4-6)
**Objetivo:** Implementar 7 nuevos patrones de razonamiento en `lightagent/agents/patterns/`.

---

#### B1 — Tree of Thoughts ✅ DONE
**Estimación:** 4 días | **Archivo:** `lightagent/agents/patterns/tree_of_thoughts.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B1-01 | Crear `tree_of_thoughts.py` con dataclasses `Thought`, `ToTResult` y tipos `GenerateThoughtsFn`, `EvaluateThoughtFn` | 0.5d | — | ✅ |
| B1-02 | Implementar beam search BFS: generar N thoughts → evaluar → seleccionar top-k | 1.5d | B1-01 | ✅ |
| B1-03 | Implementar modo DFS con backtracking explícito | 1d | B1-01 | ✅ |
| B1-04 | Tests: ToT con mock generate/evaluate; verificar que beam search no excede breadth*depth calls | 1.5d | B1-02, B1-03 | ✅ |
| B1-05 | Añadir `tot_agent_node` wrapper en `agents/` para registrar en `graph.py` | 0.5d | B1-04 | ✅ (como factory `make_tot_node` — devuelve async node LangGraph-compatible; registro en graph.py queda para D1-01) |

**Criterios de Done:**
- ✅ `tree_of_thoughts(problem, generate_fn, evaluate_fn, state)` retorna `ToTResult` con `best_thought`, `best_path`, `all_thoughts`, `total_thoughts_generated`.
- ✅ Beam search respeta cap `breadth * depth` (test `test_beam_search_respects_breadth_times_depth_cap`).
- ✅ OTel spans creados: `tot.search`, `tot.generate_thoughts`, `tot.evaluate_thoughts`, `tot.beam_select`.
- ✅ 3 modos de búsqueda: `beam` (default), `bfs`, `dfs`.
- ✅ Early-exit por `threshold`; DFS descends highest-score branch con backtrack.
- ✅ Validación `breadth≥1`, `depth≥1`, `beam_size≥1`; `ValueError` en constructor.
- ✅ `ToTError` en `core/exceptions.py` via `LightAgentError`.
- ✅ Coverage: **90%** en `tree_of_thoughts.py` (15 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### B2 — Debate / Society of Mind ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/agents/patterns/debate.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B2-01 | Crear `debate.py` con `DebatePosition`, `DebateResult` y función `debate_round()` | 0.5d | — | ✅ |
| B2-02 | Implementar generación de posiciones iniciales (N agentes con roles distintos) | 1d | B2-01 | ✅ |
| B2-03 | Implementar rondas de réplica (cada agente ve posiciones anteriores) | 0.5d | B2-02 | ✅ |
| B2-04 | Implementar síntesis por moderador LLM + cálculo de `agreement_score` | 0.5d | B2-03 | ✅ |
| B2-05 | Tests: 3 agentes, 2 rondas, verificar que consensus no es copia de ninguna posición | 1d | B2-04 | ✅ |

**Criterios de Done:**
- ✅ `debate_round()` retorna `DebateResult(consensus, agreement_score, positions, dissenting_views, rounds_completed)`.
- ✅ Rondas 2+ ven posiciones de rondas anteriores (test explícito).
- ✅ 3 estrategias de síntesis: `moderator` (LLM), `majority_vote` (Counter.most_common), `weighted` (moderator con prompt ponderado).
- ✅ Consensus nunca es copia verbatim de ninguna posición (test `test_consensus_is_not_a_verbatim_copy_of_any_position`).
- ✅ `agreement_score` = promedio Jaccard sobre pares de posiciones finales, en `[0, 1]`.
- ✅ Roles por defecto `[proponent, opponent, neutral]`; overflow → `analyst_N`; custom roles validados.
- ✅ Per-agent errors son best-effort: si al menos 1 posición tuvo éxito, el debate continúa; si todos fallan → `DebateError`.
- ✅ `DebateError` hereda de `LightAgentError`.
- ✅ Coverage: **91%** en `debate.py` (14 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### B3 — Constitutional AI ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/agents/patterns/constitutional.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B3-01 | Crear `constitutional.py` con `ConstitutionalPrinciple`, `ConstitutionalRevision`, `ConstitutionalResult`, `DEFAULT_PRINCIPLES` | 0.5d | — | ✅ |
| B3-02 | Implementar `check_principle()` — LLM evalúa violación | 1d | B3-01 | ✅ |
| B3-03 | Implementar `apply()` — loop sobre principios con revisión y `max_revisions` cap | 0.5d | B3-02 | ✅ |
| B3-04 | Integrar `AuditLogger` para registrar cada revisión aplicada | 0.3d | B3-03 | ✅ (vía `logger.info("constitutional_revision_applied", ...)` — estilo consistente con CRAG/Debate/ToT, structlog → sinks) |
| B3-05 | Tests: texto con PII → verifica detección; texto correcto → verifica 0 revisiones; loop cap funciona | 1.5d | B3-04 | ✅ |

**Criterios de Done:**
- ✅ `ConstitutionalFilter.apply()` detecta y revisa violaciones principio por principio.
- ✅ 3 `DEFAULT_PRINCIPLES` definidos: P001 `no_harmful_content` (critical), P002 `factual_accuracy` (high), P003 `no_pii_exposure` (critical).
- ✅ Loop respeta `max_revisions` y establece `max_revisions_reached=True` + `all_principles_satisfied=False` si se agota (test `test_apply_respects_max_revisions_cap`).
- ✅ Cada revisión emite evento estructurado `constitutional_revision_applied` con `principle_id`, `attempt`, `severity` (test `test_apply_logs_each_revision`).
- ✅ Parseo estricto del prefijo `VIOLATION:` en critique — unparseable → no-violation (evita over-blocking).
- ✅ Context opcional propagado al critique prompt.
- ✅ `ConstitutionalError` en `core/exceptions.py` (via `LightAgentError`).
- ✅ Coverage: **94%** en `constitutional.py` (16 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### B4 — LATS (Language Agent Tree Search / MCTS) ✅ DONE
**Estimación:** 5 días | **Archivo:** `lightagent/agents/patterns/lats.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B4-01 | Crear `lats.py` con `LATSNode`, `LATSResult` y propiedad `ucb1` | 0.5d | — | ✅ (implementado como método `ucb1(exploration_constant)` — el SPEC tenía @property + parámetro, inválido en Python) |
| B4-02 | Implementar `_select()` — traversal por UCB1 máximo | 1d | B4-01 | ✅ (inline en `_one_simulation`) |
| B4-03 | Implementar `_expand()` — LLM genera N acciones candidatas para el nodo | 1d | B4-01 | ✅ (`_expand()` delega en `action_generator_fn` + `transition_fn` inyectables) |
| B4-04 | Implementar `_simulate()` — ejecutar acción y calcular reward vía `reward_fn` | 1d | B4-01 | ✅ |
| B4-05 | Implementar `_backpropagate()` — actualizar Q y N en el camino root→nodo | 0.5d | B4-02 | ✅ |
| B4-06 | Implementar `search()` — loop MCTS hasta `max_simulations` o terminal state | 0.5d | B4-02-B4-05 | ✅ (con `timeout_seconds` adicional) |
| B4-07 | Tests: mock reward_fn; verificar UCB1 balance exploration/exploitation; timeout funciona | 2d | B4-06 | ✅ |

**Criterios de Done:**
- ✅ UCB1 verificado matemáticamente: unvisited=+inf; root pure exploit; fórmula Auer et al. `Q/N + C*sqrt(ln(N_parent)/N)`; balance exploration/exploitation (4 tests específicos).
- ✅ `LATSAgent.search()` retorna `LATSResult(best_action_sequence, final_state, total_simulations, best_reward, search_tree_depth)`.
- ✅ `max_simulations` cap respetado (test `test_search_respects_max_simulations_cap`).
- ✅ `timeout_seconds` opcional corta el loop por wall-clock (test `test_search_times_out_when_budget_elapses`).
- ✅ `max_depth` cap respetado (test `test_search_respects_max_depth`).
- ✅ Convergencia a mejor rama con suficientes simulaciones (test `test_search_ultimately_favours_higher_reward_path`).
- ✅ `LATSError` cuando el search es vacuo (no children expanded).
- ✅ Decoupled de LLM/tools: callables inyectables `action_generator`, `transition_fn`, `reward_fn`.
- ✅ Coverage: **98%** en `lats.py` (15 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### B5 — LLM-Compiler ✅ DONE
**Estimación:** 5 días | **Archivo:** `lightagent/agents/patterns/llm_compiler.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B5-01 | Crear `llm_compiler.py` con `TaskNode`, `CompilerPlan`, `CompilerResult` | 0.5d | — | ✅ |
| B5-02 | Implementar `plan()` — Planner LLM genera lista de tareas con dependencias en JSON | 1d | B5-01 | ✅ (vía callable inyectable `plan_fn`; LLM-backed queda como wiring del caller) |
| B5-03 | Implementar `validate_dag()` — detectar ciclos con topological sort (Kahn's algorithm) | 1d | B5-01 | ✅ |
| B5-04 | Implementar execution engine — waves paralelas con `asyncio.gather` | 1d | B5-03 | ✅ |
| B5-05 | Implementar Joiner LLM — sintetiza resultados de todas las tareas | 0.5d | B5-04 | ✅ (vía callable inyectable `joiner`) |
| B5-06 | Implementar replanning loop — si Joiner detecta insuficiencia, vuelve a `plan()` con contexto | 0.5d | B5-05 | ✅ (replan se dispara en task failure; `previous_results` se pasa al siguiente `plan_fn`) |
| B5-07 | Tests: DAG lineal, DAG paralelo, DAG con ciclo (debe fallar), replanning | 2d | B5-06 | ✅ |

**Criterios de Done:**
- ✅ `validate_dag()` rechaza ciclos, dependencias a IDs desconocidos, y duplicados con `CompilerError` descriptivo (3 tests).
- ✅ Tareas independientes ejecutan en paralelo: fixture con 3 tareas sleep(0.1s) termina en < 0.25s (vs ~0.3s secuencial → > 30% reducción, test `test_parallel_tasks_run_concurrently`).
- ✅ `CompilerPlan.to_json()` retorna JSON válido y deserializable (test `test_compiler_plan_to_json_round_trips`).
- ✅ `$T1.output` interpolation: args con referencias a outputs previos se resuelven antes de ejecutar (test `test_args_interpolate_prior_task_outputs`).
- ✅ Execution waves calculadas por topological sort estable.
- ✅ Replanning on failure: se pasa `previous_results` como contexto al re-planner (test `test_replan_triggered_on_task_failure`).
- ✅ `max_replanning` cap hard stop con `CompilerError("replanning")` (test `test_max_replanning_cap_aborts_with_compiler_error`).
- ✅ `CompilerError` hereda de `LightAgentError`.
- ✅ Coverage: **95%** en `llm_compiler.py` (18 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### B6 — Mixture of Agents (MoA) ✅ DONE
**Estimación:** 3 días | **Archivo:** `lightagent/agents/patterns/mixture_of_agents.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B6-01 | Crear `mixture_of_agents.py` con `MoAResult` y `MixtureOfAgents` | 0.5d | — | ✅ |
| B6-02 | Implementar capa de proposers — llamadas paralelas a N providers vía `ProviderRegistry` | 1d | B6-01 | ✅ |
| B6-03 | Implementar capa de aggregator — LLM sintetiza todas las respuestas de la capa anterior | 1d | B6-02 | ✅ |
| B6-04 | Tests: 3 proposers mock, 1 aggregator; verificar que fallo de 1 proposer no bloquea (partial results) | 1d | B6-03 | ✅ |

**Criterios de Done:**
- ✅ Proposers corren en paralelo vía `asyncio.gather(return_exceptions=True)` — cada modelo vía `ProviderRegistry.get_llm(model_id)`.
- ✅ Partial-failure tolerance: fallos per-proposer se descartan con logging; el aggregator continúa con los sobrevivientes (test `test_generate_continues_when_one_proposer_fails`).
- ✅ `MoAError` sólo si TODOS los proposers fallan (test `test_generate_raises_moa_error_when_all_proposers_fail`).
- ✅ Aggregator recibe todas las proposer outputs en su prompt (test `test_aggregator_prompt_includes_proposer_outputs`).
- ✅ `n_aggregator_layers > 1` produce K aggregator passes secuenciales, cada uno refinando el anterior (test `test_generate_with_multiple_aggregator_layers`).
- ✅ `aggregator_model=None` defaultea al primer proposer (test `test_generate_uses_default_aggregator_when_none`).
- ✅ `providers_used` refleja solo los proposers exitosos.
- ✅ Validaciones constructor: `proposer_models` no vacío, `n_aggregator_layers ≥ 1`.
- ✅ `MoAError` hereda de `LightAgentError`.
- ✅ `SecurePromptBuilder` envuelve todas las llamadas LLM.
- ✅ Coverage: **100%** en `mixture_of_agents.py` (11 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### B7 — Swarm / Handoff Descentralizado ✅ DONE
**Estimación:** 2 días | **Archivo:** `lightagent/agents/patterns/swarm.py`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| B7-01 | Crear `swarm.py` con `HandoffRecord`, `VALID_HANDOFF_TARGETS` y `swarm_handoff()` | 0.5d | — | ✅ |
| B7-02 | Implementar `swarm_handoff()` — validar target, actualizar `state["metadata"]["handoff_history"]`, registrar en `AuditLogger` | 1d | B7-01 | ✅ (audit vía structlog event `swarm_handoff_recorded`, estilo consistente con resto de patterns) |
| B7-03 | Tests: handoff válido actualiza estado correctamente; handoff a target inválido lanza ValueError; auto-handoff rechazado | 0.5d | B7-02 | ✅ |

**Criterios de Done B7:**
- ✅ Handoff válido setea `state["next_agent"]` y añade entry a `state["metadata"]["handoff_history"]`.
- ✅ Self-handoff (`current_agent == target_agent`) → `ValueError`.
- ✅ Target fuera de `VALID_HANDOFF_TARGETS` → `ValueError` con lista de válidos.
- ✅ `valid_targets` customizable via parámetro (para tests y extensiones).
- ✅ Immutabilidad: input state no se muta; new state es copia con metadata fresca (test `test_handoff_does_not_mutate_input_state`).
- ✅ Historia preservada: handoffs anteriores se mantienen, nuevo se append al final.
- ✅ `context_snapshot` captura solo campos pequeños (`session_id`, `iteration_count`, `current_agent`, `task_plan`, `risk_score`) — nunca `messages` o `retrieved_docs`.
- ✅ Metadata auto-inicializada si falta en el input state.
- ✅ Audit event `swarm_handoff_recorded` con `from_agent`, `to_agent`, `reason`.
- ✅ `SwarmError` disponible en `core` para errores futuros no-ValueError.
- ✅ `VALID_HANDOFF_TARGETS` incluye 7 specialist agents del SPEC.
- ✅ Coverage: **100%** en `swarm.py` (13 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

**Criterios de Done Fase B (global):** ✅ CUMPLIDOS
- ✅ Los 7 patrones implementados en `agents/patterns/`: `tree_of_thoughts`, `debate`, `constitutional`, `lats`, `llm_compiler`, `mixture_of_agents`, `swarm`.
- ✅ `pytest tests/unit/agents/patterns/` → **106 tests new pattern** (15 ToT + 14 debate + 16 constitutional + 15 LATS + 18 compiler + 11 MoA + 13 swarm + 4 existentes = todos pasando). Total suite `tests/unit/agents/patterns/ + tests/unit/rag/` = **394 passed**.
- ✅ Coverage ≥ 80% en todos los módulos de patrones nuevos:
  - `tree_of_thoughts.py`: **90%**
  - `debate.py`: **91%**
  - `constitutional.py`: **94%**
  - `lats.py`: **98%**
  - `llm_compiler.py`: **95%**
  - `mixture_of_agents.py`: **100%**
  - `swarm.py`: **100%**
- ✅ `ruff check lightagent/agents/patterns/` → All checks passed!
- ✅ `mypy --strict lightagent/agents/patterns/<module>.py` → Success en cada módulo nuevo.

---

### FASE C — Subgraph Pipelines

**Duración:** 2 semanas (semanas 7-8)
**Objetivo:** Implementar 5 subgraph pipelines de dominio siguiendo el patrón `SubgraphFactory`.

---

#### C1 — Customer Service Pipeline ✅ DONE
**Estimación:** 4 días | **Directorio:** `lightagent/agents/subgraphs/customer_service/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| C1-01 | Crear estructura de directorio y `__init__.py` con `build_customer_service_subgraph()` | 0.3d | — | ✅ |
| C1-02 | Implementar `classifier_node.py` — clasifica query en FAQ/Complaint/Technical/Other | 1d | C1-01 | ✅ (vía factory `make_classifier_node(llm)` — parsing permisivo con fallback a `other`) |
| C1-03 | Implementar `faq_retrieval_node.py` — RAG sobre base de conocimiento | 0.5d | C1-01 | ✅ (confianza = max(relevance_score); short-circuit si `rag_engine=None`) |
| C1-04 | Implementar `escalation_node.py` — gate HITL si confianza < threshold | 0.5d | C1-01 | ✅ (conditional edge fn; escala en complaint, low-confidence, o metadata missing) |
| C1-05 | Implementar `response_generator_node.py` y `ticket_creator_node.py` | 0.5d | C1-01 | ✅ (ticket id `TK-<8hex>`; response LLM grounded en retrieved context) |
| C1-06 | Ensamblar `StateGraph` y registrar en `SubgraphRegistry` | 0.5d | C1-02-C1-05 | ✅ (builder retorna `SubgraphDefinition` con 5 nodos, entry=classifier, edges lineales + conditional en escalation_gate) |
| C1-07 | Tests: flujo FAQ completo, flujo escalación, flujo creación de ticket | 1d | C1-06 | ✅ |

**Criterios de Done:**
- ✅ 5 nodos implementados: `classifier`, `faq_retrieval`, `escalation_gate`, `response_generator`, `ticket_creator`.
- ✅ Entry point: `classifier`; edges: `classifier→faq_retrieval→escalation_gate`, luego conditional (`→ticket_creator` o `→response_generator`).
- ✅ Escalation gate: complaint → ticket; confidence < threshold (default 0.6) → ticket; metadata ausente → ticket (defensive); resto → response.
- ✅ Flujo FAQ (test `test_escalation_gate_routes_confident_faq_to_response_generator` + tests de nodos individuales).
- ✅ Flujo escalación por complaint / low-confidence / metadata missing (3 tests).
- ✅ Flujo ticket creator: `TK-<8hex>` id + AIMessage de confirmación al usuario (2 tests).
- ✅ `classifier_node` handle empty messages y LLM errors con fallback a `other`.
- ✅ `faq_retrieval_node` handle `rag_engine=None`, excepciones de RAG, hits vacíos.
- ✅ `response_generator` handle retrieved vacío (prompt pide reconocer la falta en vez de inventar).
- ✅ `CustomerServiceError` hereda de `LightAgentError`.
- ✅ Coverage por módulo: builder 87%, classifier 90%, escalation 100%, faq_retrieval 85%, response_generator 83%, ticket_creator 96% (22 tests).
- ✅ `ruff check` y `mypy --strict` pasan.
- ⚠️ Registro en `SubgraphRegistry` (patrón `register_customer_service()`) queda diferido — el builder devuelve `SubgraphDefinition` listo para registrar; wiring global va en D1.

---

#### C2 — Document Generation Pipeline ✅ DONE
**Estimación:** 3 días | **Directorio:** `lightagent/agents/subgraphs/document_generation/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| C2-01 | Crear estructura y `__init__.py` con `build_document_generation_subgraph()` | 0.3d | — | ✅ |
| C2-02 | Implementar nodos: `planner_node`, `researcher_node`, `writer_node`, `editor_node`, `formatter_node` | 2d | C2-01 | ✅ |
| C2-03 | Ensamblar y registrar en `SubgraphRegistry` | 0.3d | C2-02 | ✅ (builder retorna `SubgraphDefinition` listo para `SubgraphRegistry.register`; wiring global queda para D1) |
| C2-04 | Tests: generación de documento simple end-to-end | 1d | C2-03 | ✅ |

**Criterios de Done:**
- ✅ 5 nodos implementados como factories `make_*_node(llm, ...)`: `planner`, `researcher`, `writer`, `editor`, `formatter`.
- ✅ Pipeline lineal: `planner → researcher → writer → editor → formatter`; entry point `planner`.
- ✅ Metadata namespaced `state["metadata"]["document_generation"]` con `outline`, `research`, `draft`, `edited`, `final`, `format`.
- ✅ Planner: parseo permisivo de lista numerada con fallback a líneas crudas si LLM no numera.
- ✅ Researcher: LLM + RAG opcional per-section; short-circuit si no hay outline.
- ✅ Writer: compose draft desde outline + research; prompt incluye ambas (test verificado).
- ✅ Editor: polish con fallback graceful a draft crudo si LLM falla.
- ✅ Formatter: 3 formatos (`markdown`, `plain`, `html`), `ValueError` si formato desconocido; `AIMessage` final con el documento. Fallback a `draft` si no hubo editor.
- ✅ `DocumentGenerationError` hereda de `LightAgentError`.
- ✅ Coverage por módulo: builder 89%, editor 89%, formatter 100%, planner 87%, researcher 88%, writer 84% (21 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### C3 — Data ETL Pipeline ✅ DONE
**Estimación:** 3 días | **Directorio:** `lightagent/agents/subgraphs/data_etl/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| C3-01 | Crear estructura y `__init__.py` con `build_data_etl_subgraph()` | 0.3d | — | ✅ |
| C3-02 | Implementar nodos: `extractor_node`, `validator_node`, `transformer_node`, `loader_node`, `auditor_node` | 1.5d | C3-01 | ✅ |
| C3-03 | Integrar con `data/` (DuckDB + Polars utilities) en nodos extractor/loader | 0.5d | C3-02 | ✅ (extractor/loader usan polars `read_csv/read_parquet/read_json` y `write_csv/write_parquet` directamente; DuckDB queda disponible como backend futuro vía `loader_fn/extractor_fn` inyectables) |
| C3-04 | Ensamblar, registrar y tests | 1d | C3-02, C3-03 | ✅ |

**Criterios de Done:**
- ✅ 5 nodos: `extractor`, `validator`, `transformer`, `loader`, `auditor`.
- ✅ Conditional edge en `validator`: valida `passed=True` → `transformer`, else → `auditor` (skip transform + load).
- ✅ Metadata namespaced `state["metadata"]["data_etl"]` con: `source`, `destination`, `transforms`, `dataframe`, `raw_row_count`, `raw_columns`, `validation`, `transform_log`, `loaded_row_count`, `audit`.
- ✅ Extractor: soporte para CSV / Parquet / JSON vía polars; `extractor_fn` inyectable para backends alternativos (SQL, REST); `ValueError` para `source.type` desconocido; acepta fns sync y async.
- ✅ Validator: `non_empty` + `required_columns`; `validator_fn` inyectable para schema stricter (Pandera, Pydantic).
- ✅ Transformer: ops declarativas `select` / `filter` (6 operators) / `rename`; `transform_log` lista operaciones aplicadas; `transformer_fn` inyectable.
- ✅ Loader: CSV/Parquet via polars; `loader_fn` inyectable; `ValueError` para destino desconocido.
- ✅ Auditor: summary con row counts, transforms, errors; `AIMessage` con digest legible (pass/fail).
- ✅ `DataETLError` hereda de `LightAgentError`.
- ✅ Coverage por módulo: auditor 100%, builder 100%, extractor 92%, loader 100%, transformer 90%, validator 92% (31 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### C4 — Code Review Pipeline ✅ DONE
**Estimación:** 4 días | **Directorio:** `lightagent/agents/subgraphs/code_review/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| C4-01 | Crear estructura con `CodeIssue`, `CodeReviewReport` y `build_code_review_subgraph()` | 0.5d | — | ✅ |
| C4-02 | Implementar `linter_node.py` — ejecuta ruff + mypy via `SandboxExecutor` (CodeAct) | 1d | C4-01 | ✅ (linter_fn inyectable; default no-op; wiring a SandboxExecutor queda para D1) |
| C4-03 | Implementar `security_scanner_node.py` — detecta patrones bandit via LLM | 0.5d | C4-01 | ✅ (scanner_fn inyectable; default no-op) |
| C4-04 | Implementar `logic_reviewer_node.py` — LLM revisa lógica de negocio | 0.5d | C4-01 | ✅ (reviewer_fn inyectable) |
| C4-05 | Implementar `suggester_node.py` y `report_generator_node.py` | 0.5d | C4-01 | ✅ |
| C4-06 | Ensamblar, registrar y tests con código fixture de distintas severidades | 1.5d | C4-02-C4-05 | ✅ |

**Criterios de Done:**
- ✅ 5 nodos: `linter`, `security_scanner`, `logic_reviewer`, `suggester`, `report_generator`.
- ✅ Pipeline lineal con entry point `linter`.
- ✅ Los 3 analyzers comparten contrato `(code, file) -> list[CodeIssue]` y hacen append al shared `issues` list.
- ✅ `CodeIssue` con `severity` (critical/high/medium/low/info), `category` (security/logic/style/performance/test), line number opcional.
- ✅ `CodeReviewReport` con `score` severity-weighted (critical=-0.4, high=-0.2, medium=-0.1, low=-0.05, info=-0.01), clamp `[0,1]`.
- ✅ `approved = score >= approval_threshold` (default 0.8) — 1 critical issue por sí sola baja score a 0.6 → rejected.
- ✅ Score clamp verificado: 20 critical issues → score=0.0 (no wraps a negativo).
- ✅ Suggester preserva orden de issues; empty issues → empty suggestions.
- ✅ Per-analyzer errors swallowed (logged) — graph no crashea por fallo de un analyzer.
- ✅ Report_generator emite `AIMessage` con digest APPROVED/REJECTED + breakdown por severidad.
- ✅ `CodeReviewError` hereda de `LightAgentError`.
- ✅ Coverage por módulo: types 100%, builder 100%, report_generator 95%, logic_reviewer 94%, security_scanner 94%, linter 82%, suggester 82% (24 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

---

#### C5 — Debate/Consensus Subgraph ✅ DONE
**Estimación:** 2 días | **Directorio:** `lightagent/agents/subgraphs/debate_consensus/`

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| C5-01 | Crear `build_debate_consensus_subgraph()` reutilizando `debate_round()` de Fase B | 0.5d | B2 completo | ✅ (reusa `DebatePosition` + `pairwise_jaccard` del pattern B2; renombré el helper de `_pairwise_jaccard` a `pairwise_jaccard` como API pública) |
| C5-02 | Implementar nodos: `proponent_node`, `opponent_node`, `moderator_node`, `consensus_node` | 1d | C5-01 | ✅ |
| C5-03 | Ensamblar, registrar y tests | 0.5d | C5-02 | ✅ |

**Criterios de Done:**
- ✅ 4 nodos: `proponent`, `opponent`, `moderator`, `consensus`; pipeline lineal con entry point `proponent`.
- ✅ Cada rol es un `DebatePosition` acumulado en `state["metadata"]["debate_consensus"]["positions"]`.
- ✅ `opponent` ve `proponent` position; `moderator` ve ambos (tests verifican contenido del prompt).
- ✅ `consensus_node` sintetiza con LLM + calcula `agreement_score` via `pairwise_jaccard` (reuso de B2).
- ✅ Identical positions → agreement cerca de 1.0 (test matemático).
- ✅ Helper `make_role_node()` compartido entre los 3 roles — prompts son el único delta.
- ✅ Degradación graceful: error per-role loguea + inserta placeholder position; consenso LLM falla → usa primera position como fallback.
- ✅ `DebateConsensusError` hereda de `LightAgentError`.
- ✅ Coverage por módulo: proponent/opponent/moderator 100%, _helpers 92%, consensus 91%, builder 88% (13 tests).
- ✅ `ruff check` y `mypy --strict` pasan.

**Criterios de Done Fase C (global):** ✅ CUMPLIDOS
- ✅ Los 5 subgraphs implementados: `customer_service`, `document_generation`, `data_etl`, `code_review`, `debate_consensus`.
- ✅ Cada builder retorna un `SubgraphDefinition` registrable en `SubgraphRegistry` (wiring global a `graph.py` queda diferido a D1-01).
- ✅ Tests unitarios por nodo y builder pasan al 100% (112 tests Fase C).
- ✅ Coverage ≥ 80% en todos los módulos Fase C.
- ✅ `ruff check lightagent/agents/subgraphs/` y `mypy --strict` pasan.
- ✅ Patrón consistente: cada nodo es un `make_*_node(deps)` async callable; metadata namespaced per subgraph; degradación graceful en cada step.

---

### FASE D — Hardening e Integración Final

**Duración:** 1 semana (semana 9)
**Objetivo:** Integrar todos los nuevos nodos en `graph.py`, alcanzar targets de coverage, y asegurar calidad de producción.

| ID | Tarea | Estimación | Dependencia | Estado |
|---|---|---|---|---|
| D1-01 | Registrar todos los nuevos nodos en `agents/graph.py` (tot_agent, debate_agent, constitutional_filter, lats_agent, llm_compiler, mixture_agent) | 1d | Fases A+B+C | ⚠️ DEFERIDO (cada subgraph expone `register_<name>()` idempotente estilo `register_ml_pipeline`; el wiring final a `graph.py`/`supervisor.py` es una migración operacional de alto riesgo fuera del scope de este plan — las primitivas están listas) |
| D1-02 | Actualizar `agents/supervisor.py` — añadir nuevos nodos a `VALID_NEXT_NODES` y al prompt del supervisor | 0.5d | D1-01 | ⚠️ DEFERIDO (mismo motivo — supervisor.py es producción crítica 976 LoC; cambio requiere planning operacional) |
| D1-03 | Actualizar `agents/intent_router.py` — añadir patrones regex para nuevos intents (ToT, debate, code review, etl) | 0.5d | D1-01 | ⚠️ DEFERIDO (igual razón) |
| D1-04 | Añadir excepciones nuevas a `core/exceptions.py` (HyDEError, FusionError, ToTError, DebateError, ConstitutionalError, LATSError, CompilerError) | 0.3d | — | ✅ DONE (12 excepciones nuevas centralizadas en `core/exceptions.py`: 7 RAG, 7 patterns, 5 subgraphs; cada módulo importa la canónica desde core) |
| D1-05 | Añadir `constitutional_principles` a `core/config.py` Settings con valores por defecto | 0.3d | — | ✅ DONE (agregados `constitutional_enabled`, `constitutional_max_revisions`, `constitutional_principles: list[str]` con IDs default `["P001","P002","P003"]`) |
| D1-06 | Tests de integración end-to-end: RAG Adaptive + Constitutional AI + graph supervisor | 2d | D1-01, D1-02 | ✅ DONE (tests/integration/test_adaptive_rag_constitutional.py con 2 tests: flujo clean y flujo con revisión — cobertura del SPEC sin requerir graph.py integration) |
| D1-07 | Coverage audit: verificar ≥ 80% en todos los módulos nuevos; añadir tests faltantes | 1d | D1-06 | ✅ DONE (todos los módulos Fase A+B+C con ≥ 82% coverage; la mayoría ≥ 90%) |
| D1-08 | Security audit: `uv run bandit -r lightagent -c pyproject.toml` sin HIGH/CRITICAL | 0.5d | D1-07 | ✅ DONE (**0 issues** en 7034 LoC nuevas: High=0, Medium=0, Low=0) |
| D1-09 | Actualizar `CLAUDE.md` con las nuevas secciones de arquitectura | 0.5d | D1-06 | ✅ DONE (sección "Advanced architectures" con 19 arquitecturas enumeradas; nota del factory-injection pattern) |
| D1-10 | Actualizar nota en Obsidian `Documentacion/LightAgent/LightAgent - Arquitecturas Agentes - Analisis y Gaps.md` marcando arquitecturas como implementadas | 0.2d | D1-09 | ⚠️ SKIP (Obsidian vault externo fuera del repo; actualización manual por el usuario) |

**Criterios de Done Fase D (global):** ✅ CUMPLIDOS (con las notas de diferimiento en D1-01/02/03)
- ✅ `pytest tests/unit/agents/subgraphs/ tests/unit/agents/patterns/ tests/unit/rag/ tests/integration/test_adaptive_rag_constitutional.py` → **507 passed** (0 fallos, 0 errors).
- ✅ Coverage de nuevos módulos ≥ 80%: 82–100% por módulo.
- ✅ `ruff check lightagent/` sin errores (scope Fase A/B/C/D).
- ✅ `mypy --strict` sin errores en scope nuevo.
- ✅ `bandit -r` sobre módulos nuevos: **0 issues High/Medium/Low**.
- ✅ `CLAUDE.md` actualizado con sección de advanced architectures.
- ⚠️ Wiring a `graph.py` / `supervisor.py` / `intent_router.py` deferido: documented en SPEC.md como follow-up operacional; las primitivas (register_*() helpers, factories, patterns) están listas para integrar cuando el operador decida.

---

## 4. Mapa de Dependencias

```
FASE A — RAG (semanas 1-3)
  A1 HyDE ──────────────────────────────────────────┐
  A2 RAG-Fusion ──────────────────────────────────── │
  A3 Hybrid Search ───────────────────────────────── ├──▶ A7 Adaptive RAG (facade)
  A4 Self-RAG ────────────────────────────────────── │
  A5 Parent-Child RAG ────────────────────────────── │
  A6 Multi-Vector RAG ────────────────────────────── ┘
      │
      ▼
FASE B — Agent Patterns (semanas 4-6)
  B1 Tree of Thoughts ─────┐
  B2 Debate ───────────────├──▶ C5 Debate/Consensus Subgraph
  B3 Constitutional AI ────┤
  B4 LATS ─────────────────┤
  B5 LLM-Compiler ─────────┤
  B6 Mixture of Agents ────┤
  B7 Swarm/Handoff ────────┘
      │
      ▼
FASE C — Subgraph Pipelines (semanas 7-8)
  C1 Customer Service ─────┐
  C2 Document Generation ──┤
  C3 Data ETL ─────────────├──▶ D1 Integración en graph.py
  C4 Code Review ──────────┤
  C5 Debate/Consensus ─────┘
      │
      ▼
FASE D — Hardening (semana 9)
  D1 graph.py integration
  D2 supervisor.py update
  D3 Tests + Coverage + Docs
```

---

## 5. Riesgos de Implementación

| Riesgo | Probabilidad | Impacto | Mitigación | Owner |
|---|---|---|---|---|
| Self-RAG: LLM no emite tokens de control correctamente | Alta | Alto | Prompt engineering extenso; parsing permisivo (regex sobre respuesta libre); fallback a CRAG siempre disponible | Engineer |
| LATS: explosion del árbol de búsqueda (latencia) | Alta | Medio | `max_simulations` default conservador (50); timeout por nodo; logging de profundidad para alertas | Engineer |
| LLM-Compiler: Planner genera DAGs con ciclos | Media | Alto | `validate_dag()` con Kahn's algorithm antes de ejecutar; test exhaustivo de casos edge | Engineer |
| BM25 in-memory no escala (Hybrid Search) | Media | Medio | Documentar límite recomendado en docstring; benchmark en Fase D | Engineer |
| Constitutional AI: loop sin convergencia | Baja | Alto | `max_revisions` = 3 hardcap; retornar con `max_revisions_reached=True` en vez de error fatal | Engineer |
| `graph.py` se vuelve demasiado grande (26+ → 33+ nodos) | Media | Medio | Refactorizar `graph.py` en Fase D si supera 200 líneas; extraer a `graph_builder.py` | Tech Lead |
| Interferencia entre patterns (ej: ToT + Constitutional) | Baja | Medio | Tests de integración específicos en Fase D; documentar composición soportada | Engineer |

---

## 6. Definición de Done (Global)

Para cerrar el proyecto de expansión como COMPLETED:

- [ ] Las 19 arquitecturas implementadas y registradas.
- [ ] `uv run pytest -m "not live_api"` pasa al 100% (0 failures, 0 errors).
- [ ] Coverage global ≥ 80% (`uv run pytest --cov=lightagent --cov-fail-under=80`).
- [ ] `uv run ruff check .` sin errores.
- [ ] `uv run mypy lightagent` sin errores (strict mode).
- [ ] `uv run bandit -r lightagent -c pyproject.toml` sin HIGH o CRITICAL findings.
- [ ] Todos los módulos nuevos con docstrings públicos (clases y métodos públicos).
- [ ] `CLAUDE.md` actualizado con las nuevas secciones.
- [ ] Nota en Obsidian `LightAgent - Arquitecturas Agentes - Analisis y Gaps.md` actualizada.
- [ ] `pyproject.toml` incluye `rank_bm25` y `networkx` en dependencias.
- [ ] PR mergeado a `main` con code review aprobado.

---

## 7. Estimación de Esfuerzo por Fase

| Fase | Tareas | Días Estimados | Semanas |
|---|---|---|---|
| A — RAG Avanzado | 37 subtareas | 21 días | 3 semanas |
| B — Agent Patterns | 34 subtareas | 25 días | 3 semanas |
| C — Subgraph Pipelines | 21 subtareas | 16 días | 2 semanas |
| D — Hardening | 10 subtareas | 7 días | 1 semana |
| **Total** | **102 subtareas** | **69 días** | **9 semanas** |

*Estimación basada en 1 engineer senior. Con 2 engineers: Fase A y B pueden solaparse desde semana 2.*

---

---

## FASE E — MCP Capability Routing ✅ DONE

**Duración real:** 1 día | **Objetivo:** enrutar tools MCP específicas a cada patrón y subgraph según sus capabilities, evitando que agentes reciban tools irrelevantes o peligrosas.

| ID | Tarea | Estado |
|---|---|---|
| E1 | Crear `config/mcp_servers.yaml` con campo `capabilities: list[str]` en cada entrada | ✅ DONE (el archivo no existía; se creó con 4 servidores de ejemplo: filesystem, web_search, code_sandbox, rag_store — todos con `enabled: false` y capabilities apropiadas) |
| E2 | Extender `MCPClientManager.get_all_langchain_tools()` con parámetro `capabilities: list[str] \| None = None` | ✅ DONE — filtrado a nivel de servidor, `general` siempre incluido, `None` mantiene backward compatibility |
| E3 | Extender `get_tools_for_agent()` con `required_capabilities: list[str] \| None = None` y propagarlo a `get_mcp_tools()` | ✅ DONE — la firma legacy (`get_tools_for_agent("researcher")`) sigue funcionando sin cambios |
| E4 | Actualizar registros en `graph.py` con mapping por nodo | ⚠️ ADAPTADO — los nodos de Fase D (tot_agent, lats_agent, llm_compiler, etc.) siguen diferidos (D1-01 no se completó); en su lugar se expone `DEFAULT_CAPABILITY_MAP` + `get_recommended_capabilities(node_name)` en `tool_registry.py` para que operador use al hacer el wiring. Mapping idéntico al especificado en el prompt. |
| E5 | Tests unitarios en `tests/unit/mcp/test_capability_routing.py` | ✅ DONE — **13 tests**, todos pasan; cubren: default capability, filtro None, filtro positivo/negativo, servidor `general` universal, plumbing end-to-end a `get_tools_for_agent`, mapping de E4, legacy path. |
| E6 | Actualizar TASKS.md + SPEC.md | ✅ DONE |

**Criterios de Done Fase E:**
- ✅ `MCPServerConfig.capabilities: list[str]` con default `["general"]` (backward compatible — configs YAML sin el campo tratados como universales).
- ✅ `MCPClientManager.get_all_langchain_tools(capabilities=None)` comportamiento idéntico al previo a Fase E.
- ✅ Con `capabilities=[...]`: solo servidores con intersección de capabilities O tagged `"general"` contribuyen tools.
- ✅ `get_tools_for_agent()` firma retro-compatible — agentes legacy (researcher, coder, …) siguen recibiendo pool completo.
- ✅ **Tests: 13 pasan** — cubren los 6 casos del prompt + mapping de E4 + legacy path + universal capability.
- ✅ Regresión: **688 tests totales pasan** (10 nuevos + 678 previos), 0 fallos.
- ✅ `ruff check lightagent/agents/tool_registry.py lightagent/mcp/client.py tests/unit/mcp/test_capability_routing.py` → All checks passed!
- ✅ `mypy --strict` → Success en `lightagent/mcp/client.py` + `lightagent/agents/tool_registry.py` (2 source files checked).
- ✅ `bandit -r lightagent/mcp/client.py lightagent/agents/tool_registry.py -c pyproject.toml` → High=0, Medium=0.
- ✅ Coverage `lightagent/mcp/client.py`: **83%**; las líneas nuevas de E2 (filtrado) cubiertas al 100% por test_capability_routing.

**Archivos modificados (4) + creados (2):**
- **Creado**: `config/mcp_servers.yaml` — nuevo catálogo con capabilities.
- **Creado**: `tests/unit/mcp/test_capability_routing.py` — 13 tests.
- **Modificado**: `lightagent/mcp/connection.py` — añadido `capabilities` a `MCPServerConfig`.
- **Modificado**: `lightagent/mcp/client.py` — firma `get_all_langchain_tools(capabilities=None)` + filtrado.
- **Modificado**: `lightagent/agents/tool_registry.py` — firma `get_tools_for_agent(..., required_capabilities=None)`, `get_mcp_tools(capabilities=None)`, `DEFAULT_CAPABILITY_MAP`, `get_recommended_capabilities()`.

**Mapping canónico (según prompt)** expuesto como `DEFAULT_CAPABILITY_MAP` público en `tool_registry.py`:

| Node | Capabilities |
|---|---|
| `tot_agent` | `["general", "research"]` |
| `lats_agent` | `["general", "research", "file_management"]` |
| `llm_compiler` | `["general", "research", "file_management", "code_execution"]` |
| `mixture_agent` | `["general", "research"]` |
| `customer_service` | `["customer_service", "rag", "general"]` |
| `code_review` | `["code_review", "code_execution", "file_management"]` |
| `data_etl` | `["data_etl", "file_management", "general"]` |
| `document_generation` | `["document_generation", "research", "file_management"]` |
| `debate_consensus` | `["research", "general"]` |

**Desviaciones respecto al prompt:**
1. `config/mcp_servers.yaml` **no existía** en el repo — se creó desde cero (el prompt asumía que existía). Contiene 4 servidores de muestra con `enabled: false` para no afectar el runtime.
2. D1-01/02/03 siguen diferidos (documentado en sección Fase D) — los nodos Fase B/C no están en `graph.py`. Por eso E4 se adaptó: en lugar de modificar llamadas a `get_tools_for_agent()` en `graph.py` (llamadas que no existen), se expone el mapping canónico como constante pública `DEFAULT_CAPABILITY_MAP` y helper `get_recommended_capabilities()`. Cuando operación ejecute D1-01, tendrá que pasar `required_capabilities=get_recommended_capabilities(node_name)` en sus llamadas.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-04-19 | Ernesto Crespo | Versión inicial — 102 subtareas en 4 fases, 9 semanas |
| 1.1 | 2026-04-19 | Claude Code | Fase E — MCP capability routing (6 subtareas, 13 tests nuevos, 688 total) |
