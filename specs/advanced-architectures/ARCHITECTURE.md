# LightAgent Advanced Architectures — Technical Design Document

## Metadata

| Campo | Valor |
|---|---|
| **Autor** | Ernesto Crespo |
| **Estado** | `DRAFT` |
| **Versión** | 1.0 |
| **Fecha** | 2026-04-19 |
| **PRD Relacionado** | `specs/advanced-architectures/PRD.md` |
| **Reviewers** | Tech Lead, AI Architect |

---

## 1. Contexto

LightAgent-agents es un namespace package Python (PEP 420) que implementa un framework de agentes IA sobre LangGraph. La arquitectura existente provee: Supervisor Hub-and-Spoke, CRAG, Federated RAG, Reflection Loop, Parallel Fan-out, HITL Gate, y 6 subgraph pipelines de dominio.

Este documento describe el diseño técnico para integrar 19 nuevas arquitecturas manteniendo coherencia con las convenciones existentes: namespace package, `SecurePromptBuilder` para prompts, providers vía `ProviderRegistry`, OTel spans via `OTelManager`, y `get_logger()` para logging estructurado.

El principio de diseño central es **composición sobre herencia**: las nuevas arquitecturas se construyen sobre las primitivas ya existentes (`reflection_loop`, `make_parallel_dispatcher`, `CRAGPipeline`, `ChromaVectorStore`) extendiendo sus capacidades sin duplicar código.

---

## 2. Objetivos Técnicos

- **Correctitud:** Cada arquitectura implementa fielmente el algoritmo de referencia (paper o spec).
- **Rendimiento:** Sin regresiones en arquitecturas existentes; nuevas dentro de los SLOs del PRD.
- **Mantenibilidad:** `ruff`, `mypy --strict`, `bandit` pasan sin errores. Coverage ≥ 80%.
- **Composabilidad:** Nuevas arquitecturas son usables como building blocks entre sí (ej: HyDE + RAG-Fusion).
- **Operabilidad:** OTel spans + métricas + logs en todos los módulos nuevos.

---

## 3. Arquitectura Propuesta

### 3.1 Diagrama de Alto Nivel — Módulos Nuevos

```
lightagent/
├── rag/
│   ├── [EXISTENTE] engine.py          ← RAGEngine (Standard RAG)
│   ├── [EXISTENTE] crag.py            ← CRAGPipeline
│   ├── [EXISTENTE] federated.py       ← FederatedRAGEngine
│   ├── [NUEVO] hyde.py                ← HyDERetriever
│   ├── [NUEVO] fusion.py              ← RAGFusionEngine (Multi-Query + RRF)
│   ├── [NUEVO] hybrid.py              ← HybridSearchEngine (BM25 + Embeddings)
│   ├── [NUEVO] self_rag.py            ← SelfRAGPipeline
│   ├── [NUEVO] hierarchical.py        ← HierarchicalRAGEngine (Parent-Child)
│   ├── [NUEVO] adaptive.py            ← AdaptiveRAGEngine (facade)
│   └── [NUEVO] multi_vector.py        ← MultiVectorRAGEngine
│
├── agents/
│   ├── patterns/
│   │   ├── [EXISTENTE] reflection.py
│   │   ├── [EXISTENTE] parallel.py
│   │   ├── [NUEVO] tree_of_thoughts.py ← tree_of_thoughts()
│   │   ├── [NUEVO] debate.py           ← debate_round()
│   │   ├── [NUEVO] constitutional.py   ← ConstitutionalFilter
│   │   ├── [NUEVO] lats.py             ← LATSAgent
│   │   ├── [NUEVO] llm_compiler.py     ← LLMCompiler
│   │   ├── [NUEVO] mixture_of_agents.py← MixtureOfAgents
│   │   └── [NUEVO] swarm.py            ← swarm_handoff()
│   │
│   └── subgraphs/
│       ├── [EXISTENTE] dev_pipeline/
│       ├── [EXISTENTE] ml_pipeline/
│       ├── [NUEVO] customer_service/   ← CustomerServicePipeline
│       ├── [NUEVO] document_generation/← DocumentGenerationPipeline
│       ├── [NUEVO] data_etl/           ← DataETLPipeline
│       ├── [NUEVO] code_review/        ← CodeReviewPipeline
│       └── [NUEVO] debate_consensus/   ← DebateConsensusPipeline
```

### 3.2 Estructura de Integración con el Grafo Principal

```
                     ┌──────────────────────────────────┐
                     │         SUPERVISOR NODE           │
                     │  (agents/supervisor.py)           │
                     └──────┬───────────────────────────┘
                            │ routes to
       ┌────────────────────┼────────────────────────────┐
       ▼                    ▼                            ▼
 ┌──────────┐       ┌──────────────┐            ┌──────────────┐
 │ rag_agent│       │ [NUEVOS]     │            │ [NUEVOS]     │
 │  _node   │       │ tot_node     │            │ customer_    │
 │ (CRAG+   │       │ debate_node  │            │ service_node │
 │  Reflect)│       │ compiler_node│            │ code_review_ │
 └────┬─────┘       │ lats_node    │            │ node         │
      │             └──────┬───────┘            └──────────────┘
      ▼                    ▼
 ┌──────────────────────────────────────┐
 │         RAG ENGINE LAYER             │
 │  AdaptiveRAGEngine (facade)          │
 │  ┌──────┐ ┌───────┐ ┌──────────┐   │
 │  │ HyDE │ │Fusion │ │ Hybrid   │   │
 │  │      │ │  RRF  │ │ BM25+    │   │
 │  └──────┘ └───────┘ │ Semantic │   │
 │  ┌──────┐ ┌───────┐ └──────────┘   │
 │  │ Self │ │Parent │ ┌──────────┐   │
 │  │ RAG  │ │ Child │ │  Graph   │   │
 │  └──────┘ └───────┘ │  RAG     │   │
 │                     └──────────┘   │
 └──────────────────────────────────────┘
                     │
              ┌──────▼───────┐
              │ ChromaVectorStore│
              │ (existente)    │
              └────────────────┘
```

### 3.3 Componentes por Módulo

#### Fase A — RAG Engines

| Módulo | Clase Principal | Dependencias | Patrón |
|--------|----------------|-------------|--------|
| `rag/hyde.py` | `HyDERetriever` | `ProviderRegistry`, `ChromaVectorStore` | Generación → Embedding → Search |
| `rag/fusion.py` | `RAGFusionEngine` | `ProviderRegistry`, `RAGEngine`, `asyncio` | Multi-query fan-out + RRF |
| `rag/hybrid.py` | `HybridSearchEngine` | `rank_bm25`, `ChromaVectorStore` | Score fusion lineal |
| `rag/self_rag.py` | `SelfRAGPipeline` | `ProviderRegistry`, `CRAGPipeline` | Token control + conditional retrieval |
| `rag/hierarchical.py` | `HierarchicalRAGEngine` | `ChromaVectorStore`, `DocumentProcessorFactory` | Parent-child index |
| `rag/adaptive.py` | `AdaptiveRAGEngine` | Todos los engines RAG | Facade + query classifier |
| `rag/multi_vector.py` | `MultiVectorRAGEngine` | `ProviderRegistry`, `ChromaVectorStore` | Multi-representation index |

#### Fase B — Agent Patterns

| Módulo | Clase/Función | Dependencias | Patrón |
|--------|--------------|-------------|--------|
| `patterns/tree_of_thoughts.py` | `tree_of_thoughts()` | `ProviderRegistry`, `parallel.py` | BFS/DFS tree + beam search |
| `patterns/debate.py` | `debate_round()` | `ProviderRegistry`, `reflection.py` | Multi-LLM + moderator synthesis |
| `patterns/constitutional.py` | `ConstitutionalFilter` | `ProviderRegistry`, `AuditLogger` | Principles check + revision loop |
| `patterns/lats.py` | `LATSAgent` | `ProviderRegistry`, `tool_registry` | MCTS: select→expand→simulate→backprop |
| `patterns/llm_compiler.py` | `LLMCompiler` | `ProviderRegistry`, `tool_registry`, DAG | Planner→Compile DAG→Execute→Join |
| `patterns/mixture_of_agents.py` | `MixtureOfAgents` | `ProviderRegistry` (multi-provider) | Layer N generate → Layer N+1 synthesize |
| `patterns/swarm.py` | `swarm_handoff()` | `AgentState`, `ProviderRegistry` | Peer handoff sin supervisor |

### 3.4 Flujos de Datos Detallados

#### Flujo A1: HyDE Retrieval

```
Query ──▶ [LLM: genera doc hipotético] ──▶ [EmbeddingsFactory.embed(doc)]
       ──▶ [ChromaVectorStore.similarity_search(hyp_embedding, k)]
       ──▶ [List[RetrievedChunk]] ──▶ downstream pipeline
```

#### Flujo A2: RAG-Fusion con RRF

```
Query ──▶ [LLM: genera N variantes Q1..QN]
       ──▶ asyncio.gather(
              search(Q1, k), search(Q2, k), ... search(QN, k)
           )
       ──▶ [RRF merge: score(d) = Σ 1/(60 + rank(d, Qi))]
       ──▶ [sort descending] ──▶ [top-k chunks]
```

#### Flujo A3: Hybrid Search

```
Query ──┬──▶ [BM25Index.search(query)] ──▶ [(doc_id, bm25_score)]
        └──▶ [ChromaVectorStore.search(query)] ──▶ [(doc_id, sem_score)]
        ──▶ [Fusion: alpha * sem + (1-alpha) * bm25_norm]
        ──▶ [Dedup + sort] ──▶ [List[RetrievedChunk]]
```

#### Flujo A4: Self-RAG

```
Query ──▶ [LLM prompt: "¿Necesitas recuperar información? RETRIEVE / NO_RETRIEVE"]
       ──┬── NO_RETRIEVE ──▶ [LLM genera respuesta directa]
         └── RETRIEVE ──▶ [CRAGPipeline.run(query)]
                       ──▶ [LLM evalúa: Supported / Unsupported / Utility:N]
                       ──▶ [SelfRAGResult con tokens de control]
```

#### Flujo A5: Parent-Child RAG

```
INDEXING:
  Doc ──▶ [split parent chunks ~500 tokens] ──▶ [split child chunks ~100 tokens]
       ──▶ [store child en ChromaDB con metadata.parent_id]
       ──▶ [store parent en dict/sqlite por parent_id]

RETRIEVAL:
  Query ──▶ [similarity_search en child chunks]
         ──▶ [load parent chunk para cada child encontrado]
         ──▶ [List[ParentChunk]] ──▶ LLM context
```

#### Flujo B1: Tree of Thoughts

```
State ──▶ [generate N thoughts (breadth)]
       ──▶ [eval_fn(thought) → score per thought]
       ──▶ [select top-k by score (beam)]
       ──▶ [expand each selected thought → sub-thoughts]
       ──▶ [repeat until depth reached or terminal state found]
       ──▶ [return best path: List[Thought] + final_answer]
```

#### Flujo B2: Debate / Society of Mind

```
Query ──▶ [Agent A genera posición A]
       ──▶ [Agent B genera posición B (puede ser opuesta)]
       ──▶ [Agent C genera posición neutral]
       ──▶ [Ronda 2: cada agente responde a las otras posiciones]
       ──▶ [Moderador: sintetiza consenso]
       ──▶ [DebateResult: answer + agreement_score + dissenting_views]
```

#### Flujo B3: LLM-Compiler

```
Goal + Tools ──▶ [Planner LLM: genera Task DAG JSON]
             ──▶ [Compiler: valida DAG, detecta ciclos]
             ──▶ [Topological sort → execution waves]
             ──▶ [Executor: asyncio.gather por wave]
             ──▶ [Joiner LLM: sintetiza resultados]
             ──▶ ¿Replanning necesario?
                 ├── NO ──▶ [CompilerResult]
                 └── SÍ ──▶ [volver a Planner con contexto]
```

#### Flujo B4: LATS (MCTS)

```
Root State ──▶ [Selection: UCB1 = Q/N + C*sqrt(ln(N_parent)/N)]
           ──▶ [Expansion: N acciones candidatas via LLM]
           ──▶ [Simulation: ejecutar acción → reward via eval_fn]
           ──▶ [Backpropagation: actualizar Q, N en camino root→nodo]
           ──▶ [Repeat hasta budget (max_simulations) o terminal]
           ──▶ [Return best path por mayor Q/N]
```

---

## 4. Decisiones de Diseño

### DD-001: AdaptiveRAGEngine como Facade, No Subclase

- **Decisión:** `AdaptiveRAGEngine` compone instancias de los engines específicos (recibe instancias ya configuradas) y delega, en vez de heredar de `RAGEngine`.
- **Contexto:** Los engines tienen configuraciones distintas (BM25 requiere corpus, GraphRAG requiere grafo); no tienen interfaz común perfecta.
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **Facade (elegida)** | Configuración flexible, lazy init, composable | Más código de delegación |
| Herencia múltiple | Menos código | Viola Liskov en algunos engines; Python MRO complejo |
| Protocol/ABC único | API uniforme | Overhead de implementar todos los métodos en cada engine |

- **Justificación:** La facade permite que los engines sean usables de forma independiente Y como parte de Adaptive, sin forzar herencia artificial.

### DD-002: BM25 In-Process (NetworkX para GraphRAG)

- **Decisión:** BM25 se implementa in-process con `rank_bm25` (índice en memoria). GraphRAG usa `networkx` (in-process). No se requiere servidor externo en Fase A/B.
- **Contexto:** Añadir Neo4j o ElasticSearch como dependencia requerida aumentaría drásticamente la complejidad de setup.
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **In-process (elegida)** | Zero-config, testeable sin servicios | Limitado a corpus que caben en RAM |
| ElasticSearch | Escala a TB de datos | Requiere servidor, Docker, ops overhead |
| Neo4j | Grafos nativos de producción | Requiere servidor; complejiza tests |

- **Consecuencias:** Documentar límite recomendado (~500K docs para BM25). Neo4j en Fase D.

### DD-003: Tree of Thoughts — Beam Search sobre DFS puro

- **Decisión:** ToT implementa beam search (mantener top-k mejores pensamientos por nivel) en vez de DFS puro o BFS completo.
- **Contexto:** DFS puede quedar atrapado en caminos subóptimos; BFS completo es exponencialmente costoso. Beam search balancea exploración y costo.
- **Alternativas evaluadas:**

| Opción | Pros | Contras |
|---|---|---|
| **Beam Search (elegida)** | Costo controlado; mejor que greedy | Puede perder soluciones fuera del beam |
| DFS puro | Simple de implementar | Puede ser subóptimo sin backtracking real |
| BFS completo | Garantiza encontrar óptimo | Costo exponencial; inviable con LLMs |
| MCTS | Óptimo teórico | Complejidad alta; ya es LATS |

### DD-004: Constitutional AI — Principios como Strings, No Hardcoded

- **Decisión:** `ConstitutionalFilter` acepta `principles: list[str]` en el constructor, sin principios hardcodeados en el código.
- **Contexto:** Distintos deployments necesitan distintos principios (regulatorios, de marca, de seguridad). Hardcodear viola el principio de configurabilidad.
- **Consecuencias:** El conjunto de principios por defecto se define en `core/config.py` como `constitutional_principles: list[str]`.

### DD-005: LLM-Compiler — DAG como JSON serializable

- **Decisión:** El DAG de tareas se representa como `list[TaskNode]` donde cada `TaskNode` es un dataclass con `id`, `tool`, `args`, `depends_on: list[str]`. Serializable a JSON para debugging.
- **Consecuencias:** Facilita logging, replay de planes, y testing sin ejecutar herramientas reales.

### DD-006: Swarm/Handoff — Estado compartido vía AgentState, No Memoria Global

- **Decisión:** El handoff entre agentes en `swarm.py` pasa el `AgentState` completo, no usa memoria global mutable.
- **Contexto:** Memoria global introduce race conditions en ejecución paralela con `asyncio`.
- **Consecuencias:** Cada handoff es un nuevo frame de `AgentState`; el historial de handoffs se registra en `state["metadata"]["handoff_history"]`.

### DD-007: Nuevos Nodos en graph.py — Registro Explícito

- **Decisión:** Cada nueva arquitectura que necesita un nodo de LangGraph se registra explícitamente en `agents/graph.py` siguiendo el patrón existente. No hay registro dinámico/automático.
- **Consecuencias:** `graph.py` sigue siendo el punto único de verdad de la topología del grafo. Los nuevos nodos siguen el naming convention `{nombre}_node`.

---

## 5. Estructura del Código

```
lightagent/
│
├── rag/
│   ├── __init__.py          ← exporta: HyDERetriever, RAGFusionEngine,
│   │                           HybridSearchEngine, SelfRAGPipeline,
│   │                           HierarchicalRAGEngine, AdaptiveRAGEngine,
│   │                           MultiVectorRAGEngine
│   ├── hyde.py
│   ├── fusion.py
│   ├── hybrid.py
│   ├── self_rag.py
│   ├── hierarchical.py
│   ├── adaptive.py
│   └── multi_vector.py
│
├── agents/
│   ├── patterns/
│   │   ├── __init__.py      ← exporta todos los patrones
│   │   ├── tree_of_thoughts.py
│   │   ├── debate.py
│   │   ├── constitutional.py
│   │   ├── lats.py
│   │   ├── llm_compiler.py
│   │   ├── mixture_of_agents.py
│   │   └── swarm.py
│   │
│   └── subgraphs/
│       ├── customer_service/
│       │   ├── __init__.py
│       │   ├── classifier_node.py
│       │   ├── faq_retrieval_node.py
│       │   ├── escalation_node.py
│       │   ├── response_node.py
│       │   └── ticket_node.py
│       ├── document_generation/
│       │   ├── __init__.py
│       │   └── [nodos...]
│       ├── data_etl/
│       │   ├── __init__.py
│       │   └── [nodos...]
│       ├── code_review/
│       │   ├── __init__.py
│       │   └── [nodos...]
│       └── debate_consensus/
│           ├── __init__.py
│           └── [nodos...]
│
tests/
├── unit/
│   ├── rag/
│   │   ├── test_hyde.py
│   │   ├── test_fusion.py
│   │   ├── test_hybrid.py
│   │   ├── test_self_rag.py
│   │   ├── test_hierarchical.py
│   │   ├── test_adaptive.py
│   │   └── test_multi_vector.py
│   └── agents/patterns/
│       ├── test_tree_of_thoughts.py
│       ├── test_debate.py
│       ├── test_constitutional.py
│       ├── test_lats.py
│       ├── test_llm_compiler.py
│       ├── test_mixture_of_agents.py
│       └── test_swarm.py
└── integration/
    ├── test_rag_advanced_integration.py
    └── test_agent_patterns_integration.py
```

### Patrones Aplicados

| Patrón | Dónde | Por qué |
|---|---|---|
| Facade | `AdaptiveRAGEngine` | Unifica interfaz sobre múltiples engines |
| Strategy | Todos los RAG engines | Intercambiables vía mismo método `search()` |
| Template Method | `BaseRAGEngine` (nuevo ABC) | Pasos comunes: log → span → search → return |
| Composite | `MixtureOfAgents` | Compone múltiples LLM providers |
| Chain of Responsibility | `ConstitutionalFilter` | Cada principio procesa el output en cadena |
| Interpreter | `LLMCompiler` DAG parser | Interpreta el plan JSON como grafo ejecutable |

### Manejo de Errores

```python
# Jerarquía de excepciones nuevas (en core/exceptions.py)
class RAGError(LightAgentError): ...           # ya existe
class HyDEError(RAGError): ...                 # fallo en generación hipotética
class FusionError(RAGError): ...               # fallo en multi-query
class GraphRAGError(RAGError): ...             # fallo en grafo de conocimiento

class PatternError(LightAgentError): ...       # nuevo
class ToTError(PatternError): ...              # fallo en tree of thoughts
class DebateError(PatternError): ...           # fallo en debate
class ConstitutionalError(PatternError): ...   # violación no resuelta
class LATSError(PatternError): ...             # MCTS fallo
class CompilerError(PatternError): ...         # DAG inválido o ciclo
```

---

## 6. Seguridad

### 6.1 Superficie de Ataque — Nuevas Arquitecturas

| Vector | Arquitectura | Mitigación |
|---|---|---|
| Prompt injection en doc hipotético (HyDE) | HyDE | `SecurePromptBuilder` para el prompt de generación hipotética |
| Variantes maliciosas de query (RAG-Fusion) | RAG-Fusion | Variantes pasan por `GuardrailsEngine` antes de search |
| Ejecución de tool calls no autorizados (LLM-Compiler) | LLM-Compiler | Validar tool names contra `tool_registry`; `ActionInterceptor.check()` antes de cada ejecución |
| Loop infinito en Constitutional AI | Constitutional AI | `max_revisions` hardcap = 5; timeout por revisión |
| MCTS explosion de estados (LATS) | LATS | `max_simulations` hardcap; budget en segundos |
| Handoff a agente no registrado (Swarm) | Swarm | Whitelist de agentes válidos para handoff |
| Grafo de conocimiento con datos PII (GraphRAG) | GraphRAG | Sanitizar entidades antes de almacenar; respetar `InputSanitizer` |

### 6.2 Reglas Transversales

1. **Ningún módulo nuevo importa providers directamente** — siempre vía `ProviderRegistry`.
2. **Todos los prompts de usuario pasan por `SecurePromptBuilder`** — nunca f-strings con user input.
3. **`ActionInterceptor.check()` antes de tool execution** en `LLMCompiler` y `LATSAgent`.
4. **`AuditLogger` registra** cada revisión de `ConstitutionalFilter` y cada handoff de `swarm_handoff`.

---

## 7. Observabilidad

### 7.1 OTel Spans por Arquitectura

| Arquitectura | Span Names |
|---|---|
| HyDE | `hyde.generate_hypothesis`, `hyde.embed`, `hyde.search` |
| RAG-Fusion | `fusion.generate_queries`, `fusion.search_parallel`, `fusion.rrf_merge` |
| Hybrid Search | `hybrid.bm25_search`, `hybrid.semantic_search`, `hybrid.score_fusion` |
| Self-RAG | `self_rag.decide`, `self_rag.retrieve`, `self_rag.generate`, `self_rag.evaluate` |
| Parent-Child | `hierarchical.index_doc`, `hierarchical.search_child`, `hierarchical.expand_parent` |
| ToT | `tot.generate_thoughts`, `tot.evaluate_thoughts`, `tot.beam_select` |
| Debate | `debate.generate_positions`, `debate.round`, `debate.synthesize` |
| Constitutional | `constitutional.check_principle`, `constitutional.revise` |
| LATS | `lats.select`, `lats.expand`, `lats.simulate`, `lats.backpropagate` |
| LLM-Compiler | `compiler.plan`, `compiler.compile_dag`, `compiler.execute_wave`, `compiler.join` |

### 7.2 Métricas Clave

```
# Contadores
rag_hyde_requests_total{status="success|error"}
rag_fusion_requests_total{n_queries="4"}
hybrid_search_requests_total{alpha="0.5"}
self_rag_retrieve_decisions_total{decision="retrieve|skip"}
tot_thoughts_generated_total
debate_rounds_total
constitutional_revisions_total{principle="..."}
lats_simulations_total
compiler_tasks_executed_total{status="success|failed"}

# Histogramas
rag_hyde_latency_seconds
rag_fusion_latency_seconds
tot_latency_seconds
lats_latency_seconds
compiler_latency_seconds
```

---

## 8. Testing Strategy

| Nivel | Cobertura | Herramientas | Qué cubre |
|---|---|---|---|
| Unit | ≥ 80% por módulo | pytest, unittest.mock | Algoritmos (RRF, UCB1, topological sort), dataclasses, validaciones |
| Integration | Flujos críticos | pytest + AsyncMock + ChromaDB in-memory | RAG end-to-end con vector store real, patterns con LLM mockeado |
| Markers | `@pytest.mark.unit`, `@pytest.mark.integration` | pytest | Separación de tiers |
| Live API | `@pytest.mark.live_api` | pytest (skip por defecto) | Validación real contra LLM providers |

### Estrategia de Mock para LLMs

Los tests de patrones de agente mockean `ProviderRegistry.get_llm()` para retornar un `AsyncMock` que devuelve respuestas deterministas. Esto permite:
- Tests rápidos sin latencia de red.
- Tests reproducibles (no dependientes de outputs del LLM).
- Cobertura de flujos de error (mock raises `Exception`).

---

## 9. Plan de Rollout

### 9.1 Estrategia de Integración en `graph.py`

Los nuevos nodos se añaden al grafo existente de forma **aditiva**: se añaden como destinos válidos del supervisor sin eliminar nodos existentes. El supervisor aprende a rutear a ellos basándose en el intent de la query.

```python
# En agents/graph.py — añadir después de los nodos existentes:
builder.add_node("tot_agent", tot_agent_node)
builder.add_node("debate_agent", debate_agent_node)
builder.add_node("constitutional_filter", constitutional_filter_node)
builder.add_node("llm_compiler", llm_compiler_node)
# etc.

# En agents/supervisor.py — añadir a VALID_NEXT_NODES:
VALID_NEXT_NODES = {
    ...,  # existentes
    "tot_agent", "debate_agent", "constitutional_filter",
    "llm_compiler", "lats_agent", "mixture_agent",
}
```

### 9.2 Backward Compatibility

- Todos los engines RAG nuevos tienen interfaz compatible con `RAGEngine` (`search(query, k) → List[RetrievedChunk]`).
- `AdaptiveRAGEngine` puede recibir `None` para engines no configurados y usará CRAG como fallback.
- Los subgraph pipelines nuevos siguen el patrón `SubgraphFactory` y se registran en `SubgraphRegistry`.

---

## 10. Preguntas Abiertas

- [ ] **GraphRAG**: ¿Usar NetworkX con pickle para persistencia o SQLite para el grafo? — Owner: Tech Lead, Deadline: inicio Fase A semana 2.
- [ ] **AdaptiveRAG classifier**: ¿LLM call para clasificar (más preciso pero más lento) o regex + heurísticas (rápido pero menos preciso)? — Owner: AI Architect, Deadline: inicio Fase A semana 1.
- [ ] **Constitutional principles defaults**: ¿Cuáles son los principios por defecto en `core/config.py`? — Owner: Ernesto Crespo, Deadline: inicio Fase B.
- [ ] **LATS reward function**: ¿El reward lo define el caller o hay una función por defecto? — Owner: AI Architect, Deadline: inicio Fase B semana 2.
- [ ] **MoA providers**: ¿Cuántos y cuáles providers en la capa de generación? ¿Cómo manejar si un provider falla? — Owner: Tech Lead, Deadline: inicio Fase B semana 3.

---

## Historial de Cambios

| Versión | Fecha | Autor | Cambios |
|---|---|---|---|
| 1.0 | 2026-04-19 | Ernesto Crespo | Versión inicial — diseño de 19 arquitecturas en 3 fases |
