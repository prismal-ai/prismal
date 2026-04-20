# LightAgent — Advanced Architectures Expansion

## Product Requirements Document (PRD)

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-04-19 |
| **Reviewers** | Tech Lead, AI Architect |
| **Última actualización** | 2026-04-19 |

---

## 1. Resumen Ejecutivo

LightAgent v2.0.0 implementa un framework de agentes IA robusto con Supervisor/Hub-and-Spoke, CRAG, Reflection Loop, MapReduce y un conjunto de pipelines de producción. Sin embargo, el ecosistema de arquitecturas de agentes y RAG ha evolucionado significativamente: existen patrones probados en producción — Self-RAG, HyDE, RAG-Fusion, GraphRAG, Tree of Thoughts, LLM-Compiler, entre otros — que hoy no están disponibles en el sistema.

Este PRD define los requisitos para implementar **16 nuevas arquitecturas** agrupadas en tres dominios: (A) RAG avanzado, (B) patrones de agente, y (C) subgraph pipelines de dominio. La implementación se realizará por fases iterativas sobre el código existente en `lightagent/`, reutilizando la infraestructura de seguridad, observabilidad y providers ya establecida.

El resultado esperado es que LightAgent ofrezca paridad arquitectónica con el estado del arte (2024-2025), permitiendo a los equipos que lo usen acceder a estrategias de retrieval más precisas y patrones de razonamiento más sofisticados sin cambiar la interfaz pública del framework.

---

## 2. Contexto y Problema

### 2.1 Situación Actual

LightAgent-agents implementa:
- **RAG**: Standard RAG (ChromaDB), CRAG (5-step pipeline), Federated RAG (multi-nodo), RAG+Reflection.
- **Agentes**: Supervisor, ReAct, Reflection, Parallel Fan-out, HITL, Plan-Execute, CodeAct, CUA, Meta-Learning.
- **Pipelines**: dev_pipeline, ml_pipeline, financial, analysis/engineering/research orchestrators.

### 2.2 Problema

Las siguientes capacidades críticas no están disponibles:

**RAG:**
- No existe forma de decidir *si* recuperar (Self-RAG) — CRAG siempre recupera.
- No existe mejora de query antes del retrieval (HyDE, RAG-Fusion) — el embedding de la query directa tiene baja precisión en preguntas abstractas.
- No existe búsqueda léxica (BM25) combinada con semántica — falla en nombres propios y términos técnicos sin contexto previo.
- No existe razonamiento multi-hop sobre grafos de conocimiento (GraphRAG).
- No existe indexación jerárquica (Parent-Child) — el contexto recuperado puede ser demasiado pequeño.

**Patrones de agente:**
- No existe razonamiento ramificado con backtracking (Tree of Thoughts, LATS) — el sistema solo tiene razonamiento lineal.
- No existe mecanismo de consenso entre perspectivas múltiples (Debate) — las respuestas de alta incertidumbre no tienen segunda opinión.
- No existe aplicación sistemática de principios éticos/operacionales a los outputs (Constitutional AI).
- No existe compilación de DAGs de tareas con paralelismo óptimo (LLM-Compiler) — el planner actual es secuencial.
- No existe coordinación descentralizada sin supervisor (Swarm/Handoff).

**Subgraph Pipelines:**
- Faltan pipelines para casos de uso de alto valor: atención al cliente, generación documental, ETL de datos, revisión de código, consenso de debate.

### 2.3 Oportunidad

La implementación de estas arquitecturas posiciona a LightAgent como framework de referencia para producción, con capacidades que hoy solo están disponibles en implementaciones de investigación o en frameworks propietarios. El costo de implementación es acotado dado que la infraestructura base (providers, security, monitoring, LangGraph) ya existe.

---

## 3. Usuarios Objetivo

### Persona 1: AI/ML Engineer
- **Descripción:** Ingeniero que integra LightAgent en productos de IA, construye pipelines de procesamiento de documentos o sistemas de Q&A.
- **Necesidad principal:** Acceder a estrategias RAG de alta precisión sin implementarlas desde cero; combinar estrategias según el tipo de query.
- **Frecuencia de uso:** Diario.
- **Nivel técnico:** Alto.

### Persona 2: Arquitecto de Soluciones IA
- **Descripción:** Diseña la arquitectura de sistemas multi-agente para empresas; selecciona patrones según el dominio (servicio al cliente, análisis financiero, generación de código).
- **Necesidad principal:** Disponer de patrones de razonamiento probados que pueda componer en subgraphs sin escribir código de orquestación.
- **Frecuencia de uso:** Semanal (diseño) / Diario (validación).
- **Nivel técnico:** Alto.

### Persona 3: Investigador / Experimentador
- **Descripción:** Evalúa distintas estrategias de RAG y razonamiento sobre benchmarks propios. Necesita intercambiar estrategias fácilmente.
- **Necesidad principal:** API uniforme entre estrategias; capacidad de configurar y comparar.
- **Frecuencia de uso:** Diario.
- **Nivel técnico:** Muy alto.

---

## 4. Objetivos y Métricas de Éxito

### 4.1 Objetivos del Negocio

| Objetivo | Métrica | Target | Plazo |
|---|---|---|---|
| Cobertura arquitectónica | Arquitecturas implementadas / Total identificadas | 16/16 | Fase C |
| Calidad RAG | Recall@5 en benchmark interno | ≥ +15% vs CRAG base | Fase A |
| Reducción de alucinaciones | Groundedness score (Constitutional AI) | ≥ 0.90 p50 | Fase B |
| Adopción interna | Agentes usando nuevas arquitecturas | ≥ 3 casos de uso en producción | Fase C |
| Cobertura de tests | Branch coverage nuevos módulos | ≥ 80% | Global |

### 4.2 Objetivos de Usuario

| Objetivo del Usuario | Indicador |
|---|---|
| Seleccionar estrategia RAG según tipo de query | `AdaptiveRAGEngine` enruta correctamente ≥ 90% de queries en test set |
| Obtener respuestas más precisas en dominios técnicos | HyDE y RAG-Fusion mejoran MRR vs Standard RAG |
| Razonar sobre problemas complejos con backtracking | ToT y LATS resuelven problemas de planificación que ReAct falla |
| Garantizar outputs seguros y éticos | Constitutional AI bloquea respuestas que violan principios |
| Ejecutar tareas complejas en paralelo optimizado | LLM-Compiler reduce latencia en ≥ 30% vs Plan-Execute secuencial |

---

## 5. Alcance

### 5.1 In Scope (Incluido)

**Fase A — RAG Avanzado:**
- [x] Self-RAG (`rag/self_rag.py`) — decisión dinámica de recuperación
- [x] HyDE (`rag/hyde.py`) — embeddings de documentos hipotéticos
- [x] RAG-Fusion (`rag/fusion.py`) — multi-query + Reciprocal Rank Fusion
- [x] Hybrid Search (`rag/hybrid.py`) — BM25 + embeddings con score fusion
- [x] Parent-Child RAG (`rag/hierarchical.py`) — indexación jerárquica
- [x] Adaptive RAG (`rag/adaptive.py`) — selección dinámica de estrategia
- [x] Multi-Vector RAG (`rag/multi_vector.py`) — múltiples representaciones por doc

**Fase B — Patrones de Agente:**
- [x] Tree of Thoughts (`agents/patterns/tree_of_thoughts.py`)
- [x] Debate / Society of Mind (`agents/patterns/debate.py`)
- [x] Constitutional AI (`agents/patterns/constitutional.py`)
- [x] LATS / Monte Carlo Tree Search (`agents/patterns/lats.py`)
- [x] LLM-Compiler (`agents/patterns/llm_compiler.py`)
- [x] Mixture of Agents (`agents/patterns/mixture_of_agents.py`)
- [x] Swarm / Handoff Descentralizado (`agents/patterns/swarm.py`)

**Fase C — Subgraph Pipelines:**
- [x] Customer Service Pipeline (`agents/subgraphs/customer_service/`)
- [x] Document Generation Pipeline (`agents/subgraphs/document_generation/`)
- [x] Data ETL Pipeline (`agents/subgraphs/data_etl/`)
- [x] Code Review Pipeline (`agents/subgraphs/code_review/`)
- [x] Debate/Consensus Subgraph (`agents/subgraphs/debate_consensus/`)

**Transversal:**
- [x] Integración con `agents/graph.py` (registro de nuevos nodos)
- [x] Integración con `security/` (todos los patrones pasan por guardrails)
- [x] Integración con `monitoring/` (OTel spans + métricas por arquitectura)
- [x] Tests unitarios e integración (≥ 80% coverage)

### 5.2 Out of Scope (Excluido)

- **Fine-tuning de modelos (TALM)** — requiere infraestructura GPU dedicada, fuera del scope del framework.
- **ColBERT / PLAID** — requiere servidor de inferencia dedicado (ColBERT-live); se evalúa en Fase D.
- **LongRAG** — depende de LLMs con contexto >100K tokens; se integra cuando el provider lo soporte nativamente.
- **Neo4j en producción para GraphRAG** — la Fase A usa NetworkX (in-process); la integración Neo4j es Fase D.
- **UI/Dashboard** — este PRD es exclusivo del framework layer (`lightagent-agents`).
- **APIs REST públicas** — los módulos son bibliotecas Python, no servicios HTTP.

### 5.3 Futuras Consideraciones

- GraphRAG con Neo4j en producción (Fase D).
- ColBERT/PLAID como retriever alternativo.
- Self-Discover pattern.
- Episodic Memory store estructurado (extensión de `memory/`).
- Evaluación automática en benchmarks públicos (BEIR, RAGAS).

---

## 6. Requisitos Funcionales

### RF-001: Self-RAG — Recuperación Condicional
- **Descripción:** El sistema debe decidir dinámicamente si recuperar contexto externo antes de generar una respuesta, usando un LLM para emitir tokens de control (`RETRIEVE` / `NO_RETRIEVE`).
- **Actor:** `SelfRAGPipeline` invocado desde `rag_agent_node`.
- **Precondiciones:** Vector store inicializado; LLM provider configurado.
- **Flujo principal:**
  1. LLM recibe la query y decide si necesita recuperación.
  2. Si `RETRIEVE`: ejecuta similarity search → Grade → Filter → Generate con contexto.
  3. Si `NO_RETRIEVE`: genera directamente desde conocimiento paramétrico.
  4. LLM emite token `[Supported]` / `[Unsupported]` / `[Utility:N]` para auto-evaluación.
- **Flujo alternativo:** Si el LLM falla en emitir token de control → fallback a CRAG estándar.
- **Postcondiciones:** Respuesta generada con metadatos de decisión (retrieved: bool, tokens emitidos).
- **Prioridad:** `MUST`

### RF-002: HyDE — Hypothetical Document Embeddings
- **Descripción:** El sistema debe generar un documento hipotético para una query y usar su embedding como vector de búsqueda, mejorando el recall en preguntas abstractas.
- **Actor:** `HyDERetriever` llamado desde `RAGEngine.search_hyde()`.
- **Flujo principal:**
  1. LLM genera documento hipotético que respondería la query (sin contexto real).
  2. Se embebe el documento hipotético (no la query original).
  3. Se ejecuta similarity search con ese embedding.
  4. Se retornan los chunks encontrados para uso en pipeline downstream.
- **Prioridad:** `MUST`

### RF-003: RAG-Fusion — Multi-Query con RRF
- **Descripción:** El sistema debe generar N reformulaciones de la query (default 4), ejecutar N búsquedas en paralelo, y fusionar los resultados con Reciprocal Rank Fusion.
- **Actor:** `RAGFusionEngine` desde `rag_agent_node`.
- **Flujo principal:**
  1. LLM genera N variantes de la query original.
  2. Búsquedas paralelas (`asyncio.gather`) para cada variante.
  3. RRF: `score(d,q) = Σ 1/(k + rank(d,qi))` con k=60.
  4. Rerank final y retorno de top-k chunks fusionados.
- **Prioridad:** `MUST`

### RF-004: Hybrid Search — BM25 + Embeddings
- **Descripción:** El sistema debe combinar búsqueda léxica (BM25) con búsqueda semántica (embeddings) mediante fusion de scores, con peso configurable (alpha).
- **Actor:** `HybridSearchEngine` como extensión de `RAGEngine`.
- **Flujo principal:**
  1. Búsqueda BM25 sobre corpus indexado (rank_bm25).
  2. Búsqueda semántica en ChromaDB.
  3. Score fusion: `final = alpha * semantic_score + (1-alpha) * bm25_score`.
  4. Deduplicación y rerank.
- **Prioridad:** `MUST`

### RF-005: Parent-Child RAG — Indexación Jerárquica
- **Descripción:** El sistema debe indexar chunks pequeños (child, ~100 tokens) para retrieval preciso, pero devolver el contexto del chunk padre (~500 tokens) al LLM para mayor contexto.
- **Actor:** `HierarchicalRAGEngine`.
- **Flujo principal:**
  1. Indexación: divide documentos en chunks padre e hijo; almacena relación parent_id.
  2. Retrieval: busca por similitud en chunks hijo.
  3. Expansión: recupera el chunk padre correspondiente a cada hijo encontrado.
  4. Genera respuesta con contexto padre (más rico).
- **Prioridad:** `MUST`

### RF-006: Adaptive RAG — Selección Dinámica de Estrategia
- **Descripción:** El sistema debe clasificar la query entrante y seleccionar automáticamente la estrategia RAG más adecuada (Simple, CRAG, Self-RAG, GraphRAG, Fusion).
- **Actor:** `AdaptiveRAGEngine` como facade sobre todos los engines.
- **Flujo principal:**
  1. Clasificar query: factual simple / abstracta / multi-hop / ambigua.
  2. Seleccionar engine según clasificación y configuración.
  3. Ejecutar pipeline seleccionado.
  4. Retornar resultado con metadatos de estrategia usada.
- **Prioridad:** `SHOULD`

### RF-007: Multi-Vector RAG — Múltiples Representaciones
- **Descripción:** El sistema debe indexar cada documento con múltiples vectores: resumen, chunks, y preguntas hipotéticas generadas por LLM, mejorando el recall para distintos tipos de query.
- **Actor:** `MultiVectorRAGEngine`.
- **Prioridad:** `SHOULD`

### RF-008: Tree of Thoughts — Razonamiento Ramificado
- **Descripción:** El sistema debe explorar múltiples "pensamientos" (branches) en paralelo, evaluar cada rama, y podar las menos prometedoras, permitiendo backtracking en problemas complejos.
- **Actor:** `tree_of_thoughts()` en `agents/patterns/tree_of_thoughts.py`.
- **Flujo principal:**
  1. Generar N pensamientos candidatos para el paso actual (breadth-first o depth-first).
  2. Evaluar cada pensamiento con LLM (score 0-1).
  3. Seleccionar top-k pensamientos (beam search) o podar por threshold.
  4. Expandir pensamientos seleccionados hasta alcanzar solución o profundidad máxima.
  5. Retornar mejor camino encontrado.
- **Prioridad:** `MUST`

### RF-009: Debate / Society of Mind
- **Descripción:** El sistema debe instanciar múltiples agentes con perspectivas distintas, hacer que debatan sobre una respuesta, y sintetizar consenso o majority vote.
- **Actor:** `debate_round()` en `agents/patterns/debate.py`.
- **Flujo principal:**
  1. Generar N posiciones iniciales (default 3: proponent, opponent, neutral).
  2. Ejecutar M rondas de debate: cada agente responde a las posiciones anteriores.
  3. Moderador sintetiza el consenso o aplica majority vote.
  4. Retornar respuesta consensuada con nivel de acuerdo (agreement_score).
- **Prioridad:** `MUST`

### RF-010: Constitutional AI — Principios Constitucionales
- **Descripción:** El sistema debe evaluar cualquier output del agente contra un conjunto configurable de principios constitucionales y revisar automáticamente respuestas que los violen.
- **Actor:** `ConstitutionalFilter` en `agents/patterns/constitutional.py`.
- **Flujo principal:**
  1. Recibir draft de respuesta del agente.
  2. Para cada principio constitucional: LLM evalúa si el draft lo viola.
  3. Si hay violaciones: LLM genera respuesta revisada que cumple el principio.
  4. Iterar hasta que todos los principios se cumplan o se alcance max_revisions.
  5. Retornar respuesta final con log de revisiones aplicadas.
- **Prioridad:** `MUST`

### RF-011: LATS — Language Agent Tree Search
- **Descripción:** El sistema debe aplicar Monte Carlo Tree Search sobre el espacio de acciones del agente, permitiendo exploración profunda con backtracking real cuando un camino de herramientas falla.
- **Actor:** `LATSAgent` en `agents/patterns/lats.py`.
- **Flujo principal:**
  1. Selection: seleccionar nodo del árbol con mejor UCB1 score.
  2. Expansion: expandir con N acciones candidatas (tool calls).
  3. Simulation: ejecutar acción y evaluar resultado (reward).
  4. Backpropagation: actualizar scores en el árbol.
  5. Retornar mejor camino encontrado al alcanzar terminal state.
- **Prioridad:** `SHOULD`

### RF-012: LLM-Compiler — DAG de Tareas Paralelas
- **Descripción:** El sistema debe compilar un plan de alto nivel en un DAG de tareas con dependencias explícitas, ejecutar tareas independientes en paralelo, y recompilar el plan si alguna tarea falla o retorna datos inesperados.
- **Actor:** `LLMCompiler` en `agents/patterns/llm_compiler.py`.
- **Flujo principal:**
  1. Planner LLM genera lista de tareas con dependencias (`{"task": ..., "depends_on": [...], "tool": ...}`).
  2. Compiler construye DAG y valida ausencia de ciclos.
  3. Executor ejecuta tareas en paralelo según topological sort.
  4. Joiner sintetiza resultados y decide si replanning es necesario.
  5. Si replanning: volver a paso 1 con contexto actualizado.
- **Prioridad:** `MUST`

### RF-013: Mixture of Agents (MoA)
- **Descripción:** El sistema debe orquestar múltiples LLMs (de proveedores distintos) en capas, donde los modelos de la capa N generan respuestas independientes y la capa N+1 las sintetiza.
- **Actor:** `MixtureOfAgents` en `agents/patterns/mixture_of_agents.py`.
- **Prioridad:** `SHOULD`

### RF-014: Swarm / Handoff Descentralizado
- **Descripción:** El sistema debe permitir que agentes se transfieran el control directamente entre sí (sin supervisor central) mediante un protocolo de handoff con contexto compartido.
- **Actor:** `swarm_handoff()` en `agents/patterns/swarm.py`.
- **Prioridad:** `SHOULD`

### RF-015 — RF-019: Subgraph Pipelines de Dominio
- **RF-015:** Customer Service Pipeline (Classifier → FAQ RAG → Escalation → Response → Ticket). `MUST`
- **RF-016:** Document Generation Pipeline (Planner → Researcher → Writer → Editor → Formatter). `MUST`
- **RF-017:** Data ETL Pipeline (Extractor → Validator → Transformer → Loader → Auditor). `SHOULD`
- **RF-018:** Code Review Pipeline (Linter → Security Scanner → Logic Reviewer → Suggester). `MUST`
- **RF-019:** Debate/Consensus Subgraph (Proponent → Opponent → Moderator → Consensus). `SHOULD`

---

## 7. Requisitos No Funcionales

### Rendimiento
- `HyDERetriever.search()` ≤ 3s p95 (1 LLM call + 1 vector search).
- `RAGFusionEngine.search()` ≤ 5s p95 con N=4 queries paralelas.
- `HybridSearchEngine.search()` ≤ 1s p95 (BM25 es local in-process).
- `LLMCompiler` reduce latencia ≥ 30% vs Plan-Execute secuencial para tareas independientes paralelas.
- `tree_of_thoughts()` ≤ 30s p95 con breadth=3, depth=3.

### Seguridad
- Todos los prompts de nuevas arquitecturas deben pasar por `SecurePromptBuilder`.
- `ConstitutionalFilter` debe registrar revisiones en `AuditLogger`.
- Ninguna nueva arquitectura puede importar providers directamente (solo vía `lightagent/providers/`).
- `LLMCompiler` debe validar tool names contra `tool_registry` antes de ejecutar.

### Disponibilidad
- Todas las nuevas arquitecturas deben tener fallback graceful: si falla el LLM en pasos intermedios, retornar resultado parcial con flag `partial_result=True`.

### Escalabilidad
- RAG engines nuevos deben soportar colecciones ChromaDB de ≥ 1M vectores.
- `MixtureOfAgents` debe soportar ≥ 5 proveedores en paralelo.

### Observabilidad
- Cada nueva arquitectura debe crear OTel spans con `OTelManager().start_span("arquitectura.operacion")`.
- Métricas mínimas por arquitectura: `{name}_requests_total`, `{name}_latency_seconds`, `{name}_errors_total`.
- Logs estructurados con `get_logger()` en cada paso significativo.

### Mantenibilidad
- Coverage de tests ≥ 80% por módulo nuevo.
- Todas las clases públicas con docstrings siguiendo el estilo existente en el repo.
- `ruff check` y `mypy --strict` deben pasar sin errores.
- `bandit` sin findings HIGH/CRITICAL.

---

## 8. Restricciones y Dependencias

### Restricciones Técnicas
- Python 3.13+, uv como gestor de paquetes.
- LangGraph `StateGraph` como motor de orquestación — no introducir frameworks de orquestación alternativos.
- `lightagent/` es namespace package (sin `__init__.py`); no romper esta convención.
- `_MAX_TOTAL_TOOLS = 120` — los nuevos nodos no deben registrar herramientas excesivas.

### Dependencias Externas

| Dependencia | Tipo | Uso | Estado |
|---|---|---|---|
| `rank_bm25` | Nueva PyPI | Hybrid Search BM25 | ☐ Añadir a pyproject.toml |
| `networkx` | Nueva PyPI | GraphRAG (grafo in-process) | ☐ Añadir a pyproject.toml |
| `chromadb` | Existente | Vector store (todos RAG) | ✅ Ya incluida |
| `langchain-core` | Existente | Mensajes, documentos | ✅ Ya incluida |
| `langgraph` | Existente | StateGraph, Send, interrupt | ✅ Ya incluida |
| `litellm` | Existente | Provider abstraction | ✅ Ya incluida |

---

## 9. User Stories

### Épica A: RAG de Alta Precisión

**US-001:** Como AI Engineer, quiero usar HyDE para mejorar el recall en preguntas abstractas, para obtener mejores respuestas en dominios donde el corpus es técnico y denso.
- [ ] `RAGEngine.search_hyde(query)` genera documento hipotético y busca por su embedding.
- [ ] El resultado incluye metadatos con `retrieval_method: "hyde"`.
- [ ] Tests demuestran ≥ 10% mejor recall vs búsqueda directa en corpus de prueba.

**US-002:** Como AI Engineer, quiero usar RAG-Fusion para queries ambiguas, para que distintas formulaciones de la misma pregunta produzcan resultados complementarios.
- [ ] `RAGFusionEngine.search(query, n_queries=4)` genera variantes y fusiona con RRF.
- [ ] Los resultados incluyen el rank position de cada chunk en cada sub-búsqueda.

**US-003:** Como AI Engineer, quiero Hybrid Search para términos técnicos y nombres propios, para que el sistema no falle cuando el embedding no captura bien el término exacto.
- [ ] `HybridSearchEngine.search(query, alpha=0.5)` combina BM25 y semántico.
- [ ] `alpha` es configurable en runtime para ajustar el balance léxico/semántico.

**US-004:** Como AI Engineer, quiero Self-RAG para evitar recuperaciones innecesarias, para reducir latencia y costos en preguntas que el LLM puede responder directamente.
- [ ] `SelfRAGPipeline.run(query)` decide automáticamente si recuperar.
- [ ] El log muestra la decisión tomada y los tokens de control emitidos.

### Épica B: Razonamiento Avanzado

**US-005:** Como Arquitecto IA, quiero Tree of Thoughts para problemas de planificación complejos, para que el agente explore múltiples estrategias y elija la óptima con backtracking.
- [ ] `tree_of_thoughts(generate_fn, eval_fn, state, breadth=3, depth=3)` funciona en async.
- [ ] Soporta modos `bfs` (breadth-first) y `dfs` (depth-first).

**US-006:** Como Arquitecto IA, quiero Constitutional AI para garantizar outputs seguros, para que el sistema revise automáticamente respuestas que violen principios de seguridad o precisión.
- [ ] `ConstitutionalFilter` acepta lista de principios como strings.
- [ ] Cada revisión queda registrada en `AuditLogger`.

**US-007:** Como Arquitecto IA, quiero LLM-Compiler para reducir latencia en tareas complejas, para que el sistema ejecute tareas independientes en paralelo sin coordinación manual.
- [ ] `LLMCompiler.compile_and_run(goal, tools)` retorna resultados dentro del tiempo objetivo.
- [ ] El DAG generado es serializable para debugging.

### Épica C: Pipelines de Dominio

**US-008:** Como AI Engineer, quiero el Customer Service Pipeline listo para conectar al supervisor, para atender consultas de usuarios con escalación automática.
- [ ] Pipeline completo: Classifier → FAQ RAG → Escalation Gate → Response → Ticket.
- [ ] Registrado en `SubgraphRegistry`.

**US-009:** Como AI Engineer, quiero el Code Review Pipeline como subgraph reutilizable, para integrar revisión de código automatizada en flujos CI/CD via agente.
- [ ] Pipeline: Linter → Security Scanner → Logic Reviewer → Suggester.
- [ ] Retorna reporte estructurado con issues agrupados por severidad.

---

## 10. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| LLM no emite tokens de control correctamente (Self-RAG) | Media | Alto | Fallback a CRAG; regex parsing permisivo; prompt engineering extenso |
| BM25 en memoria no escala para corpus >10M docs (Hybrid) | Baja | Alto | Implementar con soporte de índice en disco (pickle); documentar límite |
| GraphRAG con NetworkX lento en grafos >100K nodos | Media | Medio | Limitar a grafos locales; Neo4j en Fase D como extensión |
| ToT genera demasiadas llamadas LLM (costo) | Alta | Medio | Cap `breadth * depth` ≤ 9 por defecto; configurable con advertencia |
| LLM-Compiler genera DAGs con ciclos | Baja | Alto | Validación topológica estricta; rechazar plan si hay ciclo |
| Constitutional AI en loop infinito | Baja | Alto | `max_revisions` = 3 por defecto; retornar con flag de warning |
| LATS requiere muchas simulaciones (latencia) | Alta | Medio | Limitar simulaciones por nodo; timeout configurable |

---

## 11. Timeline Estimado

| Fase | Duración Estimada | Entregable |
|---|---|---|
| Fase A — RAG Avanzado | 3 semanas | 7 engines RAG funcionales con tests |
| Fase B — Patrones de Agente | 3 semanas | 7 patrones de agente con tests |
| Fase C — Subgraph Pipelines | 2 semanas | 5 pipelines registrados y testeados |
| Hardening & Docs | 1 semana | Coverage ≥ 80%, docs actualizadas |
| **Total** | **9 semanas** | 19 nuevas arquitecturas en producción |

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-04-19 | Ernesto Crespo | Versión inicial — 19 arquitecturas, 3 fases |

## Aprobaciones

| Rol | Nombre | Fecha | Estado |
|---|---|---|---|
| Tech Lead | — | | ☐ Pendiente |
| AI Architect | — | | ☐ Pendiente |
