# Ejemplos de prismal

Colección completa de ejemplos que cubre todas las arquitecturas de agentes IA
y sistemas RAG disponibles en el framework `prismal`.

## Prerrequisitos

```bash
uv pip install -e ".[dev,all]"
```

Configura al menos un proveedor LLM en `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Claude (recomendado)
OPENAI_API_KEY=sk-...          # GPT-4 (opcional)
```

---

## Patrones de Agentes IA (`examples/patterns/`)

| # | Archivo | Patrón | Dataset | Descripción |
|---|---------|--------|---------|-------------|
| 1 | `01_tree_of_thoughts.py` | Tree of Thoughts (ToT) | **GSM8K** (8 500 problemas matemáticos) | Búsqueda beam/BFS/DFS en árbol de razonamiento para resolver problemas de matemáticas escolares |
| 2 | `02_debate.py` | Debate / Society of Mind | **BoolQ + Ética IA** (preguntas controvertidas) | N agentes con roles distintos debaten M rondas; moderador sintetiza consenso con score Jaccard |
| 3 | `03_lats.py` | LATS (MCTS sobre acciones) | **WebArena-style** (tareas de planificación) | UCB1 para balancear exploración/explotación en espacio de acciones discreto |
| 4 | `04_llm_compiler.py` | LLM-Compiler (DAG paralelo) | **HotpotQA** (razonamiento multi-salto) | Descompone metas en DAG de tareas, valida con Kahn, ejecuta ondas paralelas con asyncio |
| 5 | `05_mixture_of_agents.py` | Mixture of Agents (MoA) | **MedQA USMLE** (QA médica) | N modelos propositores en paralelo + agregador LLM; tolerante a fallos parciales |
| 6 | `06_reflection_loop.py` | Reflection Loop | **Writing Prompts** (escritura técnica) | Loop generar-criticar-refinar con umbral de calidad configurable; incluye `@with_reflection` |
| 7 | `07_constitutional_ai.py` | Constitutional AI | **AdvBench** (prompts adversariales) | Filtrado y revisión por principios (P001 contenido dañino, P002 precisión, P003 PII) |
| 8 | `08_swarm.py` | Swarm / Handoff | **ATIS** (soporte al cliente) | Transferencia descentralizada entre agentes especializados con audit trail y allow-listing |
| 9 | `09_parallel_dispatcher.py` | Parallel Dispatcher | **FEVER** (verificación de hechos) | Fan-out/fan-in con `asyncio.gather` + `make_parallel_dispatcher` para LangGraph |

### Ejecutar patrones

```bash
# Tree of Thoughts en GSM8K
uv run python examples/patterns/01_tree_of_thoughts.py

# Debate sobre ética en IA
uv run python examples/patterns/02_debate.py

# LATS (MCTS) para planificación
uv run python examples/patterns/03_lats.py

# LLM-Compiler con DAG paralelo
uv run python examples/patterns/04_llm_compiler.py

# Mixture of Agents para QA médica
uv run python examples/patterns/05_mixture_of_agents.py

# Reflection Loop para escritura técnica
uv run python examples/patterns/06_reflection_loop.py

# Constitutional AI para seguridad
uv run python examples/patterns/07_constitutional_ai.py

# Swarm / Handoff para soporte
uv run python examples/patterns/08_swarm.py

# Parallel Dispatcher con FEVER
uv run python examples/patterns/09_parallel_dispatcher.py
```

---

## Arquitecturas RAG (`examples/rag/`)

| # | Archivo | Arquitectura | Dataset | Descripción |
|---|---------|-------------|---------|-------------|
| 1 | `01_crag.py` | CRAG (Corrective RAG) | **SQuAD 2.0** (Wikipedia QA) | 5 pasos: retrieve → grade → filter → decide → generate; fallback web automático |
| 2 | `02_adaptive_rag.py` | Adaptive RAG | **NQ + TriviaQA** (6 tipos de query) | Clasificador regex/LLM que enruta a CRAG/HyDE/Fusion/Hybrid según tipo de pregunta |
| 3 | `03_self_rag.py` | Self-RAG | **PubMedQA** (papers biomédicos) | LLM decide si recuperar o no; tokens RETRIEVE/SUPPORTED/UTILITY para auto-evaluación |
| 4 | `04_hyde.py` | HyDE (doc hipotético) | **MS MARCO** (queries Bing) | Genera documento hipotético con LLM para cerrar vocabulary gap en búsquedas abstractas |
| 5 | `05_hybrid_search.py` | Hybrid Search | **AG News** (noticias clasificadas) | BM25 léxico + semántico con fusión ponderada `score = α×semantic + (1-α)×bm25` |
| 6 | `06_hierarchical_rag.py` | Hierarchical RAG | **CUAD** (contratos legales) | Chunks hijo (~100 chars) para búsqueda + chunks padre (~500 chars) para generación |
| 7 | `07_rag_fusion.py` | RAG-Fusion + RRF | **BEIR** (IR multi-dominio) | N queries en paralelo + Reciprocal Rank Fusion `score = Σ 1/(k+rank)` (Cormack 2009) |
| 8 | `08_multi_vector_rag.py` | Multi-Vector RAG | **ArXiv Papers** (ML/AI) | Indexa chunk + resumen LLM + N preguntas hipotéticas por documento |
| 9 | `09_multimodal_rag.py` | Multimodal RAG | **arXiv + MedQuAD + ATIS + ActivityNet** (mezcla) | Indexa text/image/audio/video con `modality`+`source_uri`; `search(modalities=[...])` filtra por modalidad |

### Ejecutar RAG

```bash
# CRAG en SQuAD
uv run python examples/rag/01_crag.py

# Adaptive RAG (enrutamiento inteligente)
uv run python examples/rag/02_adaptive_rag.py

# Self-RAG en PubMedQA
uv run python examples/rag/03_self_rag.py

# HyDE para queries abstractas
uv run python examples/rag/04_hyde.py

# Hybrid Search en AG News
uv run python examples/rag/05_hybrid_search.py

# Hierarchical RAG en CUAD
uv run python examples/rag/06_hierarchical_rag.py

# RAG-Fusion en BEIR
uv run python examples/rag/07_rag_fusion.py

# Multi-Vector RAG en ArXiv
uv run python examples/rag/08_multi_vector_rag.py

# Multimodal RAG (text + image + audio + video)
uv run python examples/rag/09_multimodal_rag.py
```

---

## Subgraphs (`examples/subgraphs/`)

| # | Archivo | Subgraph | Dataset | Descripción |
|---|---------|---------|---------|-------------|
| 1 | `01_ml_pipeline.py` | ML Pipeline | **UCI Heart Disease** | data_ingester → EDA → features → train → eval → [quality gate ≥0.7] → export |
| 2 | `02_financial_analyst.py` | Financial Analyst | **Yahoo Finance** (NVDA, MSFT) | market_data → technical → fundamental → risk → [3 gates] → report BUY/HOLD/SELL |
| 3 | `03_dev_pipeline.py` | Dev Pipeline | **GitHub Issues** (prismal) | PO → Architect → Developer → unit tests [paralelo] → QA → Reviewer → [4 gates] |
| 4 | `04_code_review.py` | Code Review | **CodeSearchNet** (Python/GitHub) | linter → security_scanner → logic_reviewer → suggester → report [score ponderado] |
| 5 | `05_data_etl.py` | Data ETL + EDA | **Titanic** (Kaggle/OpenML) | extractor → validator [EDA] → [gate] → transformer [feature eng.] → loader → auditor |
| 6 | `06_customer_service.py` | Customer Service | **ATIS + Amazon Reviews** | classifier → faq_retrieval [RAG] → escalation_gate → response_generator \| ticket_creator |
| 7 | `07_document_generation.py` | Document Generation | **Wikipedia Technical Docs** | planner → researcher → writer → editor → formatter (markdown/html/plain) |
| 8 | `08_debate_consensus.py` | Debate Consensus | **AI Policy & Tech Ethics** | proponent → opponent → moderator → consensus [Jaccard agreement score] |
| 9 | `09_hitl_approval.py` | HITL Approval | **AI Governance Decisions** (custom) | proposal_writer → risk_assessor → approval_seed → interrupt() → hitl_gate → approve \| reject \| request_changes |
| 10 | `10_analysis_orchestrator.py` | Analysis Orchestrator | **Business Intelligence Center** (custom) | analysis_supervisor (LLM router) → data_analyst \| ml_pipeline \| dev_pipeline \| financial_analyst → END |
| 11 | `11_engineering_orchestrator.py` | Engineering Orchestrator | **GitHub Issues** (LangChain) | engineering_supervisor → coder \| codeact \| planner \| file_manager \| skill_manager → END |
| 12 | `12_research_orchestrator.py` | Research Orchestrator | **arXiv + MedQuAD** | research_supervisor → researcher \| rag_agent → END |

```bash
uv run python examples/subgraphs/01_ml_pipeline.py
uv run python examples/subgraphs/02_financial_analyst.py
uv run python examples/subgraphs/03_dev_pipeline.py
uv run python examples/subgraphs/04_code_review.py
uv run python examples/subgraphs/05_data_etl.py
uv run python examples/subgraphs/06_customer_service.py
uv run python examples/subgraphs/07_document_generation.py
uv run python examples/subgraphs/08_debate_consensus.py
uv run python examples/subgraphs/09_hitl_approval.py
uv run python examples/subgraphs/10_analysis_orchestrator.py
uv run python examples/subgraphs/11_engineering_orchestrator.py
uv run python examples/subgraphs/12_research_orchestrator.py
```

---

### ¿Qué subgraph usar?

| Situación | Subgraph recomendado |
|-----------|---------------------|
| Pipeline de ML end-to-end (ingestión → modelo → exportación) | ML Pipeline |
| Análisis bursátil con indicadores técnicos y fundamentales | Financial Analyst |
| Desarrollo de software PO → código → tests → QA | Dev Pipeline |
| Revisión automatizada de PRs con score de calidad | Code Review |
| Ingestión, limpieza y análisis exploratorio de datos (EDA) | Data ETL |
| Soporte al cliente con FAQ automático y escalación humana | Customer Service |
| Generación de documentos técnicos o reportes | Document Generation |
| Análisis de decisiones complejas con múltiples perspectivas | Debate Consensus |
| Cambios críticos que requieren aprobación humana antes de ejecutar | HITL Approval |
| Tareas analíticas mixtas (SQL, ML, dev, finanzas) con enrutamiento LLM | Analysis Orchestrator |
| Peticiones de ingeniería que mezclan código, planning y filesystem | Engineering Orchestrator |
| Preguntas de investigación (web/literatura + KB interna) | Research Orchestrator |

---

## Guía de selección de patrón/arquitectura

### ¿Qué patrón de agente usar?

| Situación | Patrón recomendado |
|-----------|-------------------|
| Problema de razonamiento multi-paso (matemáticas, planificación) | Tree of Thoughts |
| Decisión controvertida con múltiples perspectivas | Debate |
| Espacio de acciones grande con exploración/explotación | LATS (MCTS) |
| Meta descomponible en tareas paralelas independientes | LLM-Compiler |
| Consultas donde distintos modelos tienen fortalezas diferentes | Mixture of Agents |
| Generación que mejora con feedback iterativo | Reflection Loop |
| Output que debe cumplir principios de seguridad/ética | Constitutional AI |
| Sistema multi-agente sin supervisor central | Swarm / Handoff |
| N tareas independientes para procesamiento masivo | Parallel Dispatcher |

### ¿Qué arquitectura RAG usar?

| Situación | Arquitectura recomendada |
|-----------|-------------------------|
| QA general sobre documentos (baseline) | CRAG |
| Mix de tipos de queries (factual, abstracta, técnica) | Adaptive RAG |
| Deseas minimizar llamadas al vector store | Self-RAG |
| Queries abstractas/conceptuales ("¿por qué?", "¿cómo?") | HyDE |
| Necesitas keywords exactas Y semántica | Hybrid Search |
| Documentos muy largos (contratos, libros, papers) | Hierarchical RAG |
| Recall es crítico (no puedes perder documentos relevantes) | RAG-Fusion |
| Usuarios con diferentes estilos de pregunta | Multi-Vector RAG |
| Corpus mixto (texto + imagen + audio + video) con filtrado por modalidad | Multimodal RAG |

---

## Multimodal (`examples/multimodal/`)

Ejemplos granulares del *Fase F* multimodal layer — todos corren con
*callables* inyectados (sin Whisper / VLM / FFmpeg reales).

| # | Archivo | Componente | Dataset | Descripción |
|---|---------|-----------|---------|-------------|
| 1 | `01_vision_agent.py` | VisionAgent | **arXiv cs.CV** (figuras sintéticas) | Análisis con vision_fn inyectado, OCR opcional, fallback en validación |
| 2 | `02_audio_agent.py` | AudioAgent  | **ATIS** (voice intents) | Pipeline STT → reason → TTS con clientes mock; WAV de silencio para validación |
| 3 | `03_video_agent.py` | VideoAgent  | **ActivityNet Captions** (clips sintéticos) | Frame-extractor + transcribe + fusión, todo inyectable |
| 4 | `04_modality_router.py` | classify_modality + router node | **ATIS + arXiv + ActivityNet** (mezclado) | 18 casos etiquetados: attachments → blocks → intent-regex |
| 5 | `05_multimodal_fusion.py` | MultimodalFusion | **VQA-style** (3 escenas) | Estrategias `concat`, `moderator` (1 LLM), `moa` (N propositores) |

Más el demo end-to-end existente `examples/multimodal_pipeline.py` que
combina router + vision + fusion.

```bash
uv run python examples/multimodal/01_vision_agent.py
uv run python examples/multimodal/02_audio_agent.py
uv run python examples/multimodal/03_video_agent.py
uv run python examples/multimodal/04_modality_router.py
uv run python examples/multimodal/05_multimodal_fusion.py
uv run python examples/multimodal_pipeline.py
```

---

## Tool Providers (Fase Y)

Inyección de proveedores de herramientas desde el host (`ToolProviderPort`) —
guía completa en [`docs/tool-providers.md`](../docs/tool-providers.md). Ambos
corren offline (sin LLM ni servidores MCP).

| Archivo | Descripción |
|---------|-------------|
| `tool_provider_host.py` | Composición tipo host (MCP opcional + Skills + stubs) + `set_tool_provider` (variante A) y toolsets por sesión (variante B) |
| `tool_provider_custom.py` | Proveedor propio que conforma `ToolProviderPort` estructuralmente y sustituye el merge completo |

```bash
uv run python examples/tool_provider_host.py     # EXAMPLE_USE_MCP=1 para conectar MCP real
uv run python examples/tool_provider_custom.py
```

---

## Estructura de los ejemplos

Cada ejemplo sigue la misma estructura:

```
"""
[Nombre del patrón/arquitectura]
[Dataset usado y por qué]
[Descripción técnica]
[Uso]
"""

# Dataset: datos de ejemplo incrustados o cargados de HuggingFace
DATASET = [...]

# Callables/configuración del patrón
async def setup():
    ...

# Ejecución
async def main():
    # Inicialización
    # Ejecución con el dataset
    # Análisis de resultados
    # Comparativas

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Arquitectura del framework

```
prismal/
├── agents/
│   ├── patterns/          ← Patrones de agentes (ToT, Debate, LATS, etc.)
│   ├── subgraphs/         ← Pipelines completos (ML, Financial, Dev, etc.)
│   ├── graph.py           ← Grafo supervisor principal (26 agentes)
│   └── state.py           ← AgentState (TypedDict con add_messages)
├── rag/                   ← Motores RAG (CRAG, HyDE, Fusion, etc.)
├── providers/             ← Abstracción de LLMs (Anthropic, OpenAI, etc.)
├── security/              ← 5 capas de defensa en profundidad
└── memory/                ← Memoria corto/largo plazo
```

Referencia completa: `CLAUDE.md` en la raíz del repositorio.
