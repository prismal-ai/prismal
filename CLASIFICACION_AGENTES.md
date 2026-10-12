# Prismal — Clasificación jerárquica (Agentes, Patrones de IA y RAG)

> Actualizado para v3.19.0 (2026-10-12). Referencia detallada en inglés:
> [`docs/agents.md`](./docs/agents.md), [`docs/patterns.md`](./docs/patterns.md),
> [`docs/rag.md`](./docs/rag.md) y [`docs/subgraphs.md`](./docs/subgraphs.md).

Núcleo: **LangGraph `StateGraph[AgentState]`** ensamblado en `prismal/agents/graph.py`.
Un `supervisor_node` enruta cada turno; `intent_router.match_intent()` (regex determinista)
actúa antes de la supervisión por LLM.

## 1. Rutas del supervisor (30 = 15 núcleo + 15 opt-in)

### 1.1 Rutas núcleo (`MEMBERS`, siempre presentes)

| Grupo | Rutas | Módulo |
|-------|-------|--------|
| Investigación y conocimiento | `researcher`, `rag_agent`, `parallel_researcher` | `researcher.py`, `rag_agent.py`, `parallel_research.py` |
| Razonamiento y planificación | `planner`, `critic` | `planner.py`, `critic.py` |
| Código y ejecución | `coder`, `codeact` (→ `coder` si `codeact_enabled=False`) | `coder.py`, `codeact_agent.py` |
| Computer Use | `cua` (→ `researcher` si `cua_enabled=False`) | `cua_agent.py` |
| Datos y archivos | `data_analyst`, `file_manager` | `data_analyst.py`, `file_manager.py` |
| Skills y tiempo | `skill_manager`, `cron_manager` | `skill_manager.py`, `cron_manager.py` |
| Pipelines (subgrafos) | `dev_pipeline`, `ml_pipeline`, `financial_analyst` | `subgraphs/dev_pipeline/`, `ml_pipeline/`, `financial/` |

### 1.2 Rutas opt-in (registradas solo con su flag)

| Grupo | Rutas | Flag |
|-------|-------|------|
| Patrones (`ADVANCED_MEMBERS`) | `tot_agent`, `debate_agent`, `constitutional_filter`, `lats_agent`, `llm_compiler`, `mixture_agent` | `enable_subgraphs` |
| Pipelines de dominio (`ADVANCED_MEMBERS`) | `customer_service`, `document_generation`, `data_etl`, `code_review`, `debate_consensus` | `enable_subgraphs` |
| Multimodal | `multimodal_pipeline` | `multimodal_enabled` |
| Deliberación | `kokoro` | `kokoro_enabled` |
| Enjambre | `skynet` | `skynet_enabled` |
| Revisión ciega | `blind_review_pipeline` | `blind_review_pipeline_enabled` |

### 1.3 Modo jerárquico y módulos auxiliares

- `hierarchical_mode=True` sustituye el grafo plano por una jerarquía de 3 niveles:
  `analysis_orchestrator`, `engineering_orchestrator` y `research_orchestrator`, construidos
  sobre `domain_supervisor.py`.
- **No son rutas del supervisor:** `meta_learner` (independiente), `skill_creator`
  (`skill_manager` usa `create_skill()`), `network_supervisor` (delegación remota; lo reutiliza
  `rag/federated.py`), `spawner` (`SubAgentSpawner`) y `factory` (`AgentFactory`, topologías
  alternativas).
- Agentes modales (`vision_agent`, `audio_agent`, `video_agent`, `modality_router`,
  `multimodal_fusion`) viven en `agents/multimodal/` y se usan dentro de `multimodal_pipeline`.
- Submódulos de orquestación avanzada: `skynet/` (supervisor → worker → reduce, S+ con roles
  heterogéneos y workers remotos A2A) y `kokoro/` (`soul_agent`, `deliberation`, `judge`).

## 2. Patrones de IA (`prismal/agents/patterns/`)

| Patrón | Descripción | Módulo | ¿Ruta? |
|--------|-------------|--------|--------|
| Tree-of-Thoughts | búsqueda beam / BFS / DFS | `tree_of_thoughts.py` | `tot_agent` |
| LATS | MCTS con UCB1 (exploración balanceada) | `lats.py` | `lats_agent` |
| LLM-Compiler | DAG de tareas, validación Kahn, olas paralelas | `llm_compiler.py` | `llm_compiler` |
| Mixture-of-Agents | proposers paralelos + agregador | `mixture_of_agents.py` | `mixture_agent` |
| Debate | N agentes, multironda + acuerdo Jaccard | `debate.py` | `debate_agent` |
| Constitutional | autorrevisión guiada por principios + audit | `constitutional.py` | `constitutional_filter` |
| Swarm | handoff descentralizado con auditoría | `swarm.py` | — (primitiva) |
| Reflection loop | generar → criticar → refinar | `reflection.py` | — (primitiva) |
| Parallel dispatch | fan-out vía LangGraph `Send()` | `parallel.py` | — (primitiva) |

Los adaptadores a nodo están en `nodes.py`.

## 3. RAG (`prismal/rag/`) — 11 motores

| Motor | Estrategia | Módulo |
|-------|-----------|--------|
| Standard | motor base sobre `VectorStorePort` | `engine.py` |
| CRAG | pipeline correctivo | `crag.py` |
| HyDE | embeddings de documento hipotético | `hyde.py` |
| RAG-Fusion | multi-query + reciprocal rank fusion | `fusion.py` |
| Hybrid | BM25 + semántico | `hybrid.py` |
| Self-RAG | recuperación bajo demanda + autoevaluación | `self_rag.py` |
| Hierarchical | indexado parent/child | `hierarchical.py` |
| Multi-Vector | chunk + resumen + N preguntas hipotéticas | `multi_vector.py` |
| Adaptive | facade que enruta por tipo de consulta | `adaptive.py` |
| Multimodal | CLIP / captions-transcripts | `multimodal.py` |
| Federated | búsqueda federada multifuente | `federated.py` |

Soporte: `context_budget.py`, `embeddings.py`, `loaders/`. Vector stores (`stores/`): Chroma
(por defecto), LanceDB, sqlite-vec, Qdrant y pgvector — ver [`docs/vector-stores.md`](./docs/vector-stores.md).

## 4. Subgrafos / Pipelines (`prismal/agents/subgraphs/`) — 15 paquetes

- `dev_pipeline` (PO → Architect → Developer → Tests → QA → Reviewer)
- `ml_pipeline` (Ingester → EDA → Features → Trainer → Evaluator → Exporter)
- `financial` (Collector → Technical → Fundamental → Risk → Report)
- `customer_service`, `document_generation`, `data_etl`, `code_review`, `debate_consensus`
- `multimodal_pipeline` (router → vision/audio/video/text → fusion → output)
- `kokoro` (load_souls → deliberate → judge → act? → output)
- `skynet` (plan → workers vía `Send` → reduce → evaluate, con re-planificación acotada)
- `blind_review_pipeline` (spec → implementer → 2 revisores ciegos → síntesis → corrección)
- Orquestadores (modo jerárquico): `research_orchestrator`, `engineering_orchestrator`,
  `analysis_orchestrator`
