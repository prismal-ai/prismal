# Prismal — Clasificación jerárquica (Agentes, Patrones de IA y RAG)

Núcleo: **LangGraph `StateGraph[AgentState]`** ensamblado en `prismal/agents/graph.py`.
Un `supervisor_node` enruta cada turno; `intent_router.match_intent()` (regex determinista)
actúa antes de la supervisión por LLM.

## 1. Agentes (26 especialistas)

| Grupo | Agentes | Módulo |
|-------|---------|--------|
| Orquestación | `supervisor`, `domain_supervisor`, `network_supervisor` | `supervisor.py`, `domain_supervisor.py`, `network_supervisor.py` |
| Razonamiento y planificación | `planner`, `critic`, `meta_learner` | `planner.py`, `critic.py`, `meta_learner.py` |
| Código y ejecución | `coder`, `codeact_agent` | `coder.py`, `codeact_agent.py` |
| Investigación y conocimiento | `researcher`, `rag_agent`, `parallel_research` | `researcher.py`, `rag_agent.py`, `parallel_research.py` |
| Datos y archivos | `data_analyst`, `file_manager` | `data_analyst.py`, `file_manager.py` |
| Skills y planificación temporal | `skill_manager`, `skill_creator`, `cron_manager` | `skill_manager.py`, `skill_creator.py`, `cron_manager.py` |
| Computer Use | `cua_agent` | `cua_agent.py` |
| Multimodal (opt-in, `multimodal_enabled`) | `vision_agent`, `audio_agent`, `video_agent` (+ `modality_router`, `multimodal_fusion`) | `agents/multimodal/` |

Submódulos de orquestación avanzada: `skynet/` (swarm supervisor: supervisor → worker → reduce)
y `kokoro/` (deliberación: `soul_agent`, `deliberation`, `judge`).

## 2. Patrones de IA (`prismal/agents/patterns/`)

| Patrón | Descripción | Módulo |
|--------|-------------|--------|
| Tree-of-Thoughts | búsqueda beam / BFS / DFS | `tree_of_thoughts.py` |
| LATS | MCTS con UCB1 (exploración balanceada) | `lats.py` |
| LLM-Compiler | DAG de tareas, validación Kahn, olas paralelas | `llm_compiler.py` |
| Mixture-of-Agents | proposers paralelos + agregador | `mixture_of_agents.py` |
| Debate | N agentes, multironda + acuerdo Jaccard | `debate.py` |
| Swarm | handoff descentralizado con auditoría | `swarm.py` |
| Constitutional | autorrevisión guiada por principios + audit | `constitutional.py` |
| Reflection loop | generar → criticar → refinar | `reflection.py` |
| Parallel dispatch | fan-out vía LangGraph `Send()` | `parallel.py` / `nodes.py` |

## 3. RAG (`prismal/rag/`)

| Motor | Estrategia | Módulo |
|-------|-----------|--------|
| Adaptive | facade que enruta por tipo de consulta | `adaptive.py` |
| HyDE | embeddings de documento hipotético | `hyde.py` |
| RAG-Fusion | multi-query + reciprocal rank fusion | `fusion.py` |
| Hybrid | BM25 + semántico | `hybrid.py` |
| Self-RAG | recuperación bajo demanda + autoevaluación | `self_rag.py` |
| CRAG | pipeline correctivo | `crag.py` |
| Hierarchical | indexado parent/child | `hierarchical.py` |
| Multi-Vector | chunk + resumen + N preguntas hipotéticas | `multi_vector.py` |
| Multimodal | CLIP / captions-transcripts | `multimodal.py` |
| Federated | búsqueda federada multifuente | `federated.py` |
| Engine / Vector store | motor base + ChromaDB | `engine.py`, `vector_store.py` |

## 4. Subgrafos / Pipelines (`prismal/agents/subgraphs/`)

Componen agentes + patrones + RAG en flujos reutilizables:

- `dev_pipeline` (PO → Architect → Developer → Tests → QA → Reviewer)
- `ml_pipeline` (Ingester → EDA → Features → Trainer → Evaluator → Exporter)
- `financial` (Collector → Technical → Fundamental → Risk → Report)
- Orquestadores: `research_orchestrator`, `engineering_orchestrator`, `analysis_orchestrator`
- `customer_service`, `document_generation`, `data_etl`, `code_review`, `debate_consensus`
- `multimodal_pipeline` (router → vision/audio/video/text → fusion → output)
