# prismal · Guía de ejemplos

> Última actualización: 2026-10-12 · prismal-ai **v3.19.0**

Documentación completa del directorio `examples/`. Cubre los **81 ejemplos
ejecutables** (9 patterns, 10 RAG, 12 subgraphs, 5 multimodal, 4 extension y
41 scripts en la raíz de `examples/`) más la plantilla de plugin
`examples/plugin_template/`.

> Para descripciones cortas y tablas comparativas de "¿qué usar?", ver
> [`examples/README.md`](examples/README.md). Este archivo es la referencia
> larga: incluye comandos, datasets, prerequisitos y **lo que se espera ver
> impreso** en cada ejemplo. Las guías de cada capa viven en [`docs/`](docs/).

---

## Tabla de contenidos

1. [Prerrequisitos y setup](#1-prerrequisitos-y-setup)
2. [Variables de entorno](#2-variables-de-entorno)
3. [Datasets disponibles](#3-datasets-disponibles)
4. [Patrones de agente (`examples/patterns/`)](#4-patrones-de-agente)
5. [Arquitecturas RAG (`examples/rag/`)](#5-arquitecturas-rag)
6. [Subgraphs (`examples/subgraphs/`)](#6-subgraphs)
7. [Capa multimodal (`examples/multimodal/`)](#7-capa-multimodal)
8. [Extensión y plugins (`examples/extension/`, `examples/plugin_template/`)](#8-extensión-y-plugins)
9. [Supervisor, multiagentes y tool providers](#9-supervisor-multiagentes-y-tool-providers)
10. [Agentes especializados y memoria](#10-agentes-especializados-y-memoria)
11. [Capas opt-in: Kokoro, Skynet, Budget, A2A y Blind Review](#11-capas-opt-in-kokoro-skynet-budget-a2a-y-blind-review)
12. [Composición, configuración y vector stores](#12-composición-configuración-y-vector-stores)
13. [Seguridad, fiabilidad, identidad y observabilidad](#13-seguridad-fiabilidad-identidad-y-observabilidad)
14. [Wave 2026-10 (TKE · GW · DEC · AMR · PRT · MEM)](#14-wave-2026-10-tke--gw--dec--amr--prt--mem)
15. [Utilidades](#15-utilidades)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Prerrequisitos y setup

### 1.1 Toolchain

- Python **3.13+**
- `uv` (gestor de dependencias y entornos)

### 1.2 Instalación

```bash
# Desde la raíz del repo
uv pip install -e ".[dev,all]"
```

Los extras `[dev,all]` incluyen ChromaDB, LangGraph, providers (LiteLLM,
Anthropic, OpenAI), LangChain, scikit-learn, ruff/mypy/pytest. Otros extras
opcionales que usan algunos ejemplos: `[a2a]` (A2A), `[lancedb]` /
`[sqlite-vec]` / `[qdrant]` / `[pgvector]` (vector stores), `[guardrails-ai]`,
`[rerank]` / `[compression]` (TKE), `[laya]` / `[jev]` (decisiones tipadas) y
`[graph-memory]` (Graphiti para memoria v2). Para los ejemplos multimodales
completos añade el extra que corresponda:

```bash
uv pip install -e ".[multimodal]"          # Whisper API + Pillow + imagehash
uv pip install -e ".[multimodal-local]"    # faster-whisper local
uv pip install -e ".[multimodal-premium]"  # ElevenLabs TTS
uv pip install -e ".[multimodal-embed]"    # CLIP cross-modal embeddings
```

> Los ejemplos de `examples/multimodal/` **no requieren** ninguno de esos
> extras: usan callables/clientes mockeados. Sólo necesitas los extras si
> quieres ejecutar agentes contra Whisper / ElevenLabs / VLMs reales.

### 1.3 Ejecutar un ejemplo

```bash
uv run python examples/<categoría>/<archivo>.py
```

Todos los archivos son self-contained: cargan su dataset, montan el patrón y
imprimen un resumen. Ninguno requiere argumentos posicionales. Los ejemplos de
las capas opt-in (secciones 9–14) activan sus flags en un `Settings(...)`
local, así que no hace falta tocar `.env` para ejecutarlos.

---

## 2. Variables de entorno

Todas las variables usan el prefijo `PRISMAL_` (algunas claves de proveedor
aceptan además el nombre sin prefijo como alias, p. ej. `ANTHROPIC_API_KEY`).
La plantilla completa es [`env.example`](env.example): **se genera** desde
`prismal/core/config.py` (`Settings`) con `scripts/gen_settings_docs.py`, junto
con la referencia [`docs/settings-reference.md`](docs/settings-reference.md),
así que siempre refleja los valores por defecto reales. Cómo se componen las
fuentes de configuración (env, `.env`, secrets managers, por tenant):
[`docs/configuration.md`](docs/configuration.md).

```bash
cp env.example .env   # y descomenta lo que necesites
```

Mínimo recomendado para los ejemplos que llaman a un LLM real:

```bash
# Al menos uno de estos
ANTHROPIC_API_KEY=sk-ant-…   # Claude (alias de PRISMAL_ANTHROPIC_API_KEY)
OPENAI_API_KEY=sk-…          # GPT / Whisper (alias de PRISMAL_OPENAI_API_KEY)
GOOGLE_API_KEY=…             # Gemini (alias de PRISMAL_GOOGLE_API_KEY)

# Multimodal premium (opcional)
PRISMAL_ELEVENLABS_API_KEY=…

# Plugin discovery (opcional; listas en JSON)
PRISMAL_PLUGINS_ALLOWLIST='["prismal-x-mydomain"]'
PRISMAL_PLUGINS_DENYLIST='[]'

# Modo jerárquico (opcional, activa los 3 orquestadores)
PRISMAL_HIERARCHICAL_MODE=true
```

**Los siguientes ejemplos NO necesitan API keys** (corren con stubs/fakes):

- `examples/patterns/09_parallel_dispatcher.py`
- `examples/rag/10_federated_rag.py`
- `examples/multimodal/*` (todos) y `examples/multimodal_pipeline.py`
- `examples/extension/*`
- `examples/subgraphs/10_analysis_orchestrator.py` (modo simulación)
- `examples/subgraphs/11_engineering_orchestrator.py` (simulación + LangGraph stub)
- `examples/subgraphs/12_research_orchestrator.py` (simulación + LangGraph stub)
- **Todos los scripts de la raíz de `examples/`** (secciones 9–15): usan fakes
  inyectados. Excepciones parciales: `vector_store_lancedb.py` necesita el
  extra `[lancedb]` y el modelo de embeddings configurado;
  `tool_provider_host.py` con `EXAMPLE_USE_MCP=1` conecta servidores MCP
  reales; `guardrails_modernization.py` degrada con un aviso si falta
  `[guardrails-ai]`.

El resto (patterns 01–08, RAG 01–09, subgraphs 01–09) **usan llamadas LLM
reales** — sin clave se obtendrá un error en tiempo de ejecución desde el
provider.

---

## 3. Datasets disponibles

La mayoría de ejemplos embeben pequeños subsets representativos para que el
script corra sin descargas externas. Cuando necesitan más datos los toman de
`../Langgraph_tutorials/data/` (el repo hermano):

| Path local | Contenido | Usado por |
|-----------|-----------|-----------|
| `data/arxiv/arxiv_papers.csv` | Abstracts de arXiv (todas las categorías) | `rag/08`, `rag/09`, `multimodal/01`, `subgraphs/12` |
| `data/github-issues/github_issues.csv` | Issues abiertos de langchain | `subgraphs/03`, `subgraphs/11` |
| `data/medquad/medquad.csv` | NIH MedQuAD (QA médica) | `patterns/05`, `rag/03`, `rag/09`, `subgraphs/12` |
| `data/customer_support_tickets.csv` | 8 469 tickets reales | `subgraphs/06` |
| `data/enhanced_health_insurance_claims.csv` | Reclamos de seguros enriquecidos | `subgraphs/09` |
| `data/loan_approval_dataset.csv` | Préstamos UCI Adult-like | `subgraphs/09` (alterno) |

Los datasets externos referenciados por nombre (sin path local) deben
descargarse vía `datasets` de HuggingFace o el script los embebe inline. En las
tablas siguientes la columna **Dataset** marca:

- **«embedded»** = el script trae el subset hard-codeado.
- **«local CSV»** = carga `../Langgraph_tutorials/data/...`.
- **«HuggingFace»** = `datasets.load_dataset(...)` (puede requerir token).

---

## 4. Patrones de agente

Carpeta: `examples/patterns/` · 9 ejemplos. Guía: [`docs/patterns.md`](docs/patterns.md).

| # | Archivo | Patrón | Dataset | Resultado esperado al ejecutar |
|---|---------|--------|---------|-------------------------------|
| 1 | `01_tree_of_thoughts.py` | Tree of Thoughts (ToT) | **GSM8K** (embedded — 5 problemas matemáticos) | Por problema: pensamiento ganador con score, profundidad del mejor camino y camino de razonamiento paso a paso. Comparativa final de estrategias `beam` vs `bfs` vs `dfs`. |
| 2 | `02_debate.py` | Debate / Society of Mind | **BoolQ + dilemas éticos IA** (embedded) | Para cada pregunta: rondas de debate por agente (proponent/opponent), consenso del moderador y Jaccard agreement score (0–1). |
| 3 | `03_lats.py` | LATS (MCTS sobre acciones) | **WebArena-style** (embedded, 3 escenarios) | Árbol MCTS con conteos de visitas y valores UCB1; ruta de acción ganadora y reward final. |
| 4 | `04_llm_compiler.py` | LLM-Compiler (DAG paralelo) | **HotpotQA** (embedded multi-hop) | DAG validado por Kahn (lista topológica); ejecución por ondas con tiempos paralelos por tarea; respuesta sintetizada. |
| 5 | `05_mixture_of_agents.py` | Mixture of Agents (MoA) | **MedQA USMLE** (embedded, 4 preguntas) | Por pregunta: respuestas de los N propositores, síntesis del agregador, métricas (`providers_used`, capas) y verificación de la opción correcta. |
| 6 | `06_reflection_loop.py` | Reflection Loop | **Writing Prompts** (embedded) | Iteraciones de generar→criticar→refinar con su score; demostración del decorator `@with_reflection`. |
| 7 | `07_constitutional_ai.py` | Constitutional AI | **AdvBench** (embedded, prompts adversariales) | Por prompt: principios disparados (P001 daño, P002 precisión, P003 PII), texto revisado y log de audit. |
| 8 | `08_swarm.py` | Swarm / Handoff | **ATIS** (embedded, 6 consultas de vuelos) | Trail de handoffs entre agentes (booking, info, support); audit JSON con allow-list y razón de cada transferencia. |
| 9 | `09_parallel_dispatcher.py` | Parallel Dispatcher | **FEVER** (embedded, 6 claims) | Fan-out con `asyncio.gather`; resultados paralelos + agregación; timing comparativo serial vs paralelo. |

```bash
uv run python examples/patterns/01_tree_of_thoughts.py
uv run python examples/patterns/02_debate.py
uv run python examples/patterns/03_lats.py
uv run python examples/patterns/04_llm_compiler.py
uv run python examples/patterns/05_mixture_of_agents.py
uv run python examples/patterns/06_reflection_loop.py
uv run python examples/patterns/07_constitutional_ai.py
uv run python examples/patterns/08_swarm.py
uv run python examples/patterns/09_parallel_dispatcher.py
```

---

## 5. Arquitecturas RAG

Carpeta: `examples/rag/` · 10 ejemplos. Guía: [`docs/rag.md`](docs/rag.md).

| # | Archivo | Arquitectura | Dataset | Resultado esperado |
|---|---------|-------------|---------|-------------------|
| 1 | `01_crag.py` | Corrective RAG | **SQuAD 2.0** (embedded — contextos Wikipedia) | Pipeline de 5 pasos por query: retrieve, grade (score 0–1 por chunk), filter, decide (fallback web si vacío), generate con citas. Imprime `CRAGResult.answer`, fuentes, `relevance_scores`. |
| 2 | `02_adaptive_rag.py` | Adaptive RAG | **NQ + TriviaQA** (embedded, 6 tipos de query) | Clasificador imprime tipo de query → ruta elegida (CRAG/HyDE/Fusion/Hybrid) → respuesta. |
| 3 | `03_self_rag.py` | Self-RAG | **PubMedQA** (embedded biomédico) | Tokens `RETRIEVE`/`SUPPORTED`/`UTILITY` por turno; muestra cuándo el LLM decide saltar la recuperación. |
| 4 | `04_hyde.py` | HyDE (doc hipotético) | **MS MARCO** (embedded, queries Bing) | Genera el "documento hipotético" antes del retrieve y compara recall vs query directa. |
| 5 | `05_hybrid_search.py` | Hybrid Search | **AG News** (embedded, noticias clasificadas) | Score combinado `α·semantic + (1-α)·BM25`; barrido de α=0.0/0.3/0.5/0.7/1.0 y mejor configuración. |
| 6 | `06_hierarchical_rag.py` | Hierarchical RAG | **CUAD** (embedded, contratos legales) | Indexa con chunks hijo (~100 chars) + padre (~500 chars); muestra el padre devuelto al LLM tras encontrar el hijo. |
| 7 | `07_rag_fusion.py` | RAG-Fusion + RRF | **BEIR** (embedded, IR multi-dominio) | N queries generadas, ranking por consulta y score RRF `Σ 1/(k+rank)`. |
| 8 | `08_multi_vector_rag.py` | Multi-Vector RAG | **arXiv** (local CSV — ML/AI) | Indexa por doc: chunk + summary + N preguntas hipotéticas; recall@k para los tres índices. |
| 9 | `09_multimodal_rag.py` | Multimodal RAG | **arXiv + MedQuAD + ATIS + ActivityNet** (mezcla — local CSV + embedded) | Indexa text/image/audio/video con `modality`+`source_uri`; demuestra `search(query, modalities=[…])` filtrado y multi-modal (fallback textual al no haber CLIP). |
| 10 | `10_federated_rag.py` | Federated RAG | **KB multi-nodo** (embedded, sintético — 3 nodos + 1 caído) | `FederatedRAGEngine` consulta el motor local y N nodos remotos en paralelo (`asyncio.gather`); el nodo caído se omite (resultados parciales) y `_merge_and_rerank` deduplica por `chunk_id`. 100 % offline, sin LLM. |

```bash
uv run python examples/rag/01_crag.py
uv run python examples/rag/02_adaptive_rag.py
uv run python examples/rag/03_self_rag.py
uv run python examples/rag/04_hyde.py
uv run python examples/rag/05_hybrid_search.py
uv run python examples/rag/06_hierarchical_rag.py
uv run python examples/rag/07_rag_fusion.py
uv run python examples/rag/08_multi_vector_rag.py
uv run python examples/rag/09_multimodal_rag.py
uv run python examples/rag/10_federated_rag.py
```

> Los ejemplos 01–08 escriben a una colección ChromaDB persistente bajo
> `data/chroma/`. Bórralo entre corridas si quieres ver el re-índice desde
> cero: `rm -rf data/chroma/`. El 09 y el 10 usan stores en memoria.

---

## 6. Subgraphs

Carpeta: `examples/subgraphs/` · 12 ejemplos. Guía: [`docs/subgraphs.md`](docs/subgraphs.md).

| # | Archivo | Subgraph | Dataset | Resultado esperado |
|---|---------|---------|---------|-------------------|
| 1 | `01_ml_pipeline.py` | ML Pipeline | **UCI Heart Disease** (embedded) | `data_ingester → EDA → features → train → eval → [quality gate ≥0.7] → export`. Métricas por etapa (AUC, accuracy) y artefactos exportados. |
| 2 | `02_financial_analyst.py` | Financial Analyst | **Yahoo Finance NVDA, MSFT** (embedded snapshots) | `market_data → technical → fundamental → risk → [3 gates] → report`. Decisión BUY/HOLD/SELL con score de riesgo. |
| 3 | `03_dev_pipeline.py` | Dev Pipeline | **GitHub Issues** (embedded subset) | `PO → Architect → Developer → unit tests [paralelo] → QA → Reviewer → [4 gates]`. Para cada issue: spec, código generado, resultado de tests y review score. |
| 4 | `04_code_review.py` | Code Review | **CodeSearchNet** (embedded, snippets Python) | `linter → security_scanner → logic_reviewer → suggester → report`. Score ponderado por etapa + dictamen final. |
| 5 | `05_data_etl.py` | Data ETL + EDA | **Titanic** (embedded) | `extractor → validator [EDA] → [gate] → transformer → loader → auditor`. Estadísticas EDA, feature engineering y log de audit. |
| 6 | `06_customer_service.py` | Customer Service | **ATIS + Amazon Reviews** (embedded) | `classifier → faq_retrieval [RAG] → escalation_gate → response_generator \| ticket_creator`. Por ticket: rama tomada (FAQ vs escalación). |
| 7 | `07_document_generation.py` | Document Generation | **Wikipedia Technical Docs** (embedded) | `planner → researcher → writer → editor → formatter`. Documento final en `markdown`, `html` y `plain` con tabla de contenidos. |
| 8 | `08_debate_consensus.py` | Debate Consensus | **AI Policy & Tech Ethics** (embedded) | `proponent → opponent → moderator → consensus`. Argumentos de cada ronda + Jaccard agreement score. |
| 9 | `09_hitl_approval.py` | HITL Approval | **AI Governance Decisions** (embedded custom) | `proposal_writer → risk_assessor → approval_seed → interrupt() → hitl_gate → approve | reject | request_changes`. Demuestra el `interrupt()` con simulación de la respuesta humana. |
| 10 | `10_analysis_orchestrator.py` | Analysis Orchestrator | **Business Intelligence Center** (embedded — 6 tareas mixtas) | 4 modos: simulación, comparación plano/jerárquico, LangGraph real con stubs y diagrama de jerarquía. Routing supervisor → `data_analyst \| ml_pipeline \| dev_pipeline \| financial_analyst`. |
| 11 | `11_engineering_orchestrator.py` | Engineering Orchestrator | **GitHub Issues** (local CSV) | Routing simulado + LangGraph real con stubs. Supervisor → `coder \| codeact \| planner \| file_manager \| skill_manager`. Imprime accuracy del routing y el resultado de cada hoja. |
| 12 | `12_research_orchestrator.py` | Research Orchestrator | **arXiv + MedQuAD** (local CSV) | Routing simulado + LangGraph real con stubs. Supervisor → `researcher \| rag_agent`. Métricas de accuracy + citas devueltas por cada hoja. |

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

## 7. Capa multimodal

Carpeta: `examples/multimodal/` · 5 ejemplos granulares + `examples/multimodal_pipeline.py` end-to-end + `examples/multimodal_ingestion.py`.

Todos usan callables / clientes inyectados — **no requieren** Whisper,
ElevenLabs, FFmpeg ni un VLM real. Sirven como base para escribir tus propios
agentes multimodales con backends reales.

| # | Archivo | Componente | Dataset | Resultado esperado |
|---|---------|-----------|---------|-------------------|
| 1 | `01_vision_agent.py` | `VisionAgent` | **arXiv cs.CV** (local CSV, figuras sintéticas) | (1) Análisis sin OCR por paper, (2) Análisis con OCR (texto fijo `arXiv preprint · 2024`), (3) `MediaValidator` rechaza un blob no-imagen y el agente degrada a `VisionResult(used_fallback=True)`. |
| 2 | `02_audio_agent.py` | `AudioAgent` | **ATIS** (embedded, 4 utterancias) | Pipeline completo STT→reason→TTS para una; lote sin TTS imprimiendo `[intent]` y respuesta; validación rechaza un blob no-audio. |
| 3 | `03_video_agent.py` | `VideoAgent` | **ActivityNet** (embedded, 3 clips) | Por clip: transcript, frame descriptions con timestamps, summary fusionado. Validación: archivo no-video → `VideoResult` vacío. |
| 4 | `04_modality_router.py` | `classify_modality` + `make_modality_router_node` | **mixto** (embedded, 18 mensajes etiquetados) | (1) Accuracy de clasificación, (2) destinos LangGraph agrupados por nodo (`vision_agent`/`audio_agent`/…), (3) demo de `force_modality` override. |
| 5 | `05_multimodal_fusion.py` | `MultimodalFusion` | **VQA-style** (embedded, 3 escenas) | Para cada escena, fusión bajo las 3 estrategias (`concat` determinístico, `moderator` mock LLM, `moa` duck-typed MoA) — muestra el cambio de estilo de la respuesta. |
| – | `multimodal_pipeline.py` | Subgraph multimodal end-to-end | bytes PNG inline | Recorre nodos `router → vision → fusion → output_formatter`. Imprime decisión del router y respuesta final. |
| – | `multimodal_ingestion.py` | `ingest_media()` + `register_multimodal_pipeline()` | PNG 1×1 real generado | Valida magic bytes/tamaño (`MediaValidator`) → sanitiza (EXIF) → vuelca a un archivo direccionado por contenido → audita solo el hash → registra un descriptor *por path* en `state["metadata"]["mm"]["media"]`; luego registra el subgraph multimodal con `multimodal_enabled=True`. |

```bash
uv run python examples/multimodal/01_vision_agent.py
uv run python examples/multimodal/02_audio_agent.py
uv run python examples/multimodal/03_video_agent.py
uv run python examples/multimodal/04_modality_router.py
uv run python examples/multimodal/05_multimodal_fusion.py
uv run python examples/multimodal_pipeline.py
uv run python examples/multimodal_ingestion.py
```

---

## 8. Extensión y plugins

### 8.1 `examples/extension/` · 4 demos del extension surface (Fase X)

Guía: [`docs/extension.md`](docs/extension.md).

| Archivo | Demuestra | Resultado esperado |
|---------|-----------|-------------------|
| `custom_node.py` | `@prismal_node` decorator | Un `sentiment_classifier` mockeado clasifica un mensaje. Imprime `state_update` y el metadato `__prismal_node__` (capabilities, security, audit, retry). |
| `custom_subgraph.py` | `PrismalStateGraphBuilder` fluent API | Compila un mini-subgraph con 2 nodos y lo ejecuta. Imprime los `messages` finales. |
| `discover_plugins_demo.py` | `discover_plugins(settings)` | Itera los entry points `prismal.subgraphs`/`nodes`/`tools`/`rag_engines`. Sin plugins instalados imprime un resumen vacío; con `plugin_template` instalado registra `prismal-x-example`. |
| `langchain_migration.py` | `LangChainRunnableAdapter` | Envuelve un `Runnable` LCEL como nodo prismal y lo invoca. Imprime el resultado del Runnable a través del wrapper. |

```bash
uv run python examples/extension/custom_node.py
uv run python examples/extension/custom_subgraph.py
uv run python examples/extension/discover_plugins_demo.py
uv run python examples/extension/langchain_migration.py
```

### 8.2 `examples/plugin_template/` · Plantilla instalable

Plantilla de un plugin externo (`prismal-x-example`) con `pyproject.toml`,
`src/prismal_x_example/{nodes,plugin}.py` y `tests/`. Para usarla:

```bash
cp -r examples/plugin_template /tmp/prismal-x-mydomain
cd /tmp/prismal-x-mydomain
mv src/prismal_x_example src/prismal_x_mydomain      # renombra
# edita pyproject.toml e imports
pip install -e .
python -m prismal.plugins list                       # → 'prismal-x-mydomain'
python -m prismal.plugins doctor                     # health-check de entry points
```

Grupos de entry-points expuestos:

| Group | Export | Contrato |
|-------|--------|----------|
| `prismal.subgraphs` | `register_<name>(registry)` | Se auto-registra o devuelve un `SubgraphDefinition` |
| `prismal.nodes` | callable `@prismal_node` | Importar = registrar |
| `prismal.tools` | `BaseTool` o factory sin args | Añadido al pool de tools (cap 120) |
| `prismal.rag_engines` | clase RAG engine | Registrada en `RAGEngineRegistry` |

---

## 9. Supervisor, multiagentes y tool providers

El corazón del framework: el grafo SUPERVISOR de LangGraph (30 rutas: 15 core +
15 opt-in), sus modos de composición multiagente y la inyección de
herramientas (`ToolProviderPort`, Fase Y). Todos corren offline con fakes.
Guías: [`docs/agents.md`](docs/agents.md), [`docs/tool-providers.md`](docs/tool-providers.md).

| Archivo | Demuestra | Resultado esperado |
|---------|-----------|-------------------|
| `supervisor_quickstart.py` | `match_intent()` + `build_supervisor_graph()` | Enrutamiento determinista pre-LLM sobre un lote de frases (cron, ToT, debate, multimodal, kokoro, skynet…), inyección de `FakeToolProvider` con `set_tool_provider`, construcción del grafo real con checkpointer en memoria, listado de nodos y `create_initial_state()`. |
| `hierarchical_supervisor.py` | `make_domain_supervisor()` + `NetworkSupervisorAgent` | Las 3 salidas de un supervisor de dominio (agente hoja válido / loop-breaker ⇒ END / basura ⇒ END) y delegación por capacidades a nodos remotos con fallback local. |
| `parallel_research_fanout.py` | Fan-out/fan-in de investigación (Phase 34) | `parallel_researcher` → dispatcher (`Send`) → N workers deterministas → `research_aggregator` (resumen markdown), incluido el camino `on_empty`. |
| `subagent_spawner.py` | `SubAgentSpawner` | Workers falsos con semáforo de concurrencia (`max_concurrent_agents`), timeouts por tarea y `cancel_all()`/`cleanup()`. |
| `tool_provider_host.py` | Composición tipo host + `set_tool_provider` | Variante A (global) y B (toolset por sesión); Skills + stubs por defecto, `EXAMPLE_USE_MCP=1` usa `build_default_tool_provider` con `config/mcp_servers.yaml`. |
| `tool_provider_custom.py` | Proveedor propio estructural | Una clase con `get_tools()` (sin herencia ni registro) sustituye el merge completo. |

```bash
uv run python examples/supervisor_quickstart.py
uv run python examples/hierarchical_supervisor.py
uv run python examples/parallel_research_fanout.py
uv run python examples/subagent_spawner.py
uv run python examples/tool_provider_host.py        # EXAMPLE_USE_MCP=1 para MCP real
uv run python examples/tool_provider_custom.py
```

---

## 10. Agentes especializados y memoria

| Archivo | Demuestra | Resultado esperado |
|---------|-----------|-------------------|
| `meta_learner.py` | `MetaLearner` + `TraceScore` | Puntúa trazas falsas por dimensión, marca agentes bajo umbral y guarda propuestas de mejora con sentinel de revisión humana (en un directorio temporal, nunca en `skills/custom/`). |
| `skill_creator_agent.py` | `create_skill` + `skill_creator_node` | Flujo GENERATE → guardia de shell → VALIDATE (ruff/mypy/bandit reales si están instalados) → WRITE → REPORT con un LLM falso; rechazo de ejecución de shell (AC-017-8). |
| `memory_management.py` | `ShortTermMemory`, `ConversationHistory`, `LongTermMemory` | Buffer FIFO, transcript Markdown multi-canal y hechos de largo plazo en SQLite + un vector store en memoria, con redacción de secretos/PII y expiración por retención. |

La memoria agéntica v2 (tipada, con decisiones y presupuesto) tiene su propio
ejemplo, `agentic_memory.py`, en la [sección 14](#14-wave-2026-10-tke--gw--dec--amr--prt--mem).

```bash
uv run python examples/meta_learner.py
uv run python examples/skill_creator_agent.py
uv run python examples/memory_management.py
```

---

## 11. Capas opt-in: Kokoro, Skynet, Budget, A2A y Blind Review

Cada capa está detrás de su flag (`kokoro_enabled`, `skynet_enabled`,
`budget_enabled`, `a2a_enabled`, `blind_review_pipeline_enabled`; todos `False`
por defecto, grafo byte a byte idéntico con el flag apagado). Los ejemplos
inyectan backends deterministas: sin LLM ni red.

| Archivo | Fase | Demuestra | Resultado esperado |
|---------|------|-----------|-------------------|
| `kokoro_deliberation.py` | K | Subgraph kokoro (`load_souls → deliberate → judge → act → output`) | Las tres almas (espíritu 魂 / mente 知 / corazón 情) deliberan una pregunta y el juez emite el veredicto final. |
| `skynet_swarm.py` | S | `build_skynet_subgraph` (plan → `Send` → workers → reduce → evaluate) | Una orden paralelizable resuelta por un enjambre de 3 workers. |
| `skynet_specialist_swarm.py` | S+ | `RoleRegistry` + `CostMeter` compartido + worker remoto A2A falso | Rol local `researcher` con su propio modelo/persona/tools y rol `legal` remoto; el uso de tokens de todo el enjambre aparece en `SwarmResult.usage`. |
| `skynet_direct_api.py` | S | `SkynetSupervisor.plan/evaluate`, `SwarmWorker`, `reduce_results` sin subgraph | Una orden falla en el primer intento y se re-planifica de forma determinista (`attempt + 1`, sin consultar al planner). |
| `budget_governance.py` | C | `CostMeter` + `BudgetGuard` + `make_budget_guard_fn` | `Usage` acumulándose, el guard pasando de within → soft → hard y un `reflection_loop` detenido por el soft cap. |
| `budget_graph_integration.py` | C | Seeding en el seam del grafo | `resolve_budget` → `seed_budget_run` → `get_budget_guard` → soft/hard → `BudgetExceeded` → `clear_budget_run` (motor en un registro en proceso, nunca en el estado checkpointeado). |
| `a2a_server.py` | I | Lado inbound: `AgentCard` + `A2AServerHandler` | Agent Card desde el registry de capacidades, `message/send` JSON-RPC sobre un grafo eco, tarea + artefactos y la misma tarea por SSE. |
| `a2a_remote_node.py` | I | Lado outbound: `A2AAgentNode.as_node()` + `A2AToolProvider` | Un agente remoto falso como nodo del grafo (respuesta saneada y fusionada en `messages`) y sus skills como tools. |
| `a2a_tool_provider.py` | I | `A2AToolProvider` + `CompositeToolProvider` | Tools `a2a__{agent}__{skill}` desde una `AgentCard` construida en código, invocación contra un transporte falso y composición con tools locales. |
| `blind_review_pipeline.py` | BRP | Subgraph `blind_review_pipeline` | spec → implementación → dos revisores **ciegos** → `synthesize_verdicts` determinista; HITL deshabilitado para llegar a `END`. |

```bash
uv run python examples/kokoro_deliberation.py
uv run python examples/skynet_swarm.py
uv run python examples/skynet_specialist_swarm.py
uv run python examples/skynet_direct_api.py
uv run python examples/budget_governance.py
uv run python examples/budget_graph_integration.py
uv run python examples/a2a_server.py
uv run python examples/a2a_remote_node.py
uv run python examples/a2a_tool_provider.py
uv run python examples/blind_review_pipeline.py
```

Guías: [`docs/kokoro.md`](docs/kokoro.md), [`docs/skynet.md`](docs/skynet.md),
[`docs/budget.md`](docs/budget.md), [`docs/a2a.md`](docs/a2a.md),
[`docs/blind-review-pipeline.md`](docs/blind-review-pipeline.md).

---

## 12. Composición, configuración y vector stores

| Archivo | Fase | Demuestra | Resultado esperado |
|---------|------|-----------|-------------------|
| `composition_root.py` | R | `build_test_runtime()` / `build_runtime(..., mode="context")` | Runtime con fakes deterministas, modo por tenant sin estado global y aislamiento multi-tenant con colecciones sufijadas (`collection_for`). |
| `config_source_env.py` | W | `EnvConfigSource` + `set_config_source` | El camino por defecto (env + `.env` + espejo legacy `LIGHTAGENT_`) inyectado una vez; reproduce el comportamiento previo byte a byte. Prueba con `PRISMAL_DEFAULT_MODEL=… uv run python examples/config_source_env.py`. |
| `config_source_custom.py` | W | Fuente tipo Vault + `MappingConfigSource` + `build_settings` | Un `ConfigSourcePort` propio encadenado delante de `EnvConfigSource` (el vault gana) y `Settings` aislados por tenant sin estado global. |
| `vector_store_lancedb.py` | Z | `VectorStoreFactory` con `vector_store_backend="lancedb"` | Indexa 3 documentos y busca con score normalizado en `[0, 1]`; sin el extra imprime cómo instalarlo. Requiere `[lancedb]`. |

```bash
uv run python examples/composition_root.py
uv run python examples/config_source_env.py
uv run python examples/config_source_custom.py
uv pip install -e ".[lancedb]" && uv run python examples/vector_store_lancedb.py
```

Guías: [`docs/composition-root.md`](docs/composition-root.md),
[`docs/configuration.md`](docs/configuration.md), [`docs/vector-stores.md`](docs/vector-stores.md).

---

## 13. Seguridad, fiabilidad, identidad y observabilidad

| Archivo | Fase | Demuestra | Resultado esperado |
|---------|------|-----------|-------------------|
| `runtime_hardening.py` | H | Controles de hardening en tiempo de ejecución | Inyección indirecta en un payload RAG bloqueada (enforce), path fuera del workspace rechazado + HTML escapado, `ToolPolicyEngine` (deny / HITL / rate-limit) y `RunawayGuard` deteniendo un bucle estancado. |
| `guardrails_modernization.py` | GRD | `content_safety_reasoning` + `StructuredOutputGuard` | Bloqueo, paso seguro y contrato fail-open por timeout del clasificador; re-ask acotado sobre salida inválida (requiere `[guardrails-ai]`, si falta degrada con un aviso). |
| `loop_hardening.py` | LH | `ContextCompactor` + `resolve_phase()` | Un historial de 200 mensajes recortado a una cola literal y el catálogo de tools estrechado entre las fases "planning" y "executing". |
| `node_typesafety.py` | NTS | `input_model`/`output_model` en `@prismal_node` | Modos `off`, `warn` (log + contador, se deja pasar) y `enforce` (`NodeValidationError` mapeado a `metadata.error`). |
| `agent_identity.py` | IDN | DID, vault, `PolicyEngine`, on-behalf-of | Identidad `did:key` con scopes mínimos, secreto con scope desde `FakeVault`, autorización in-scope / out-of-scope / HITL y token OBO cuyos scopes solo se estrechan. |
| `agent_eval.py` | V | `EvalRunner` con fakes | Un eval-set pequeño sobre un grafo guionizado y un runtime no-op, sin API keys. |
| `observability_integration.py` | OBS | `ObservabilityPort` | Run simulado (nodos, tool call, uso LLM), `RunSummary` consultable con un score de juez y export del run como dataset LangSmith y Langfuse. |

```bash
uv run python examples/runtime_hardening.py
uv run python examples/guardrails_modernization.py
uv run python examples/loop_hardening.py
uv run python examples/node_typesafety.py
uv run python examples/agent_identity.py
uv run python examples/agent_eval.py
uv run python examples/observability_integration.py
```

Guías: [`docs/security/`](docs/security/), [`docs/loop-hardening.md`](docs/loop-hardening.md),
[`docs/node-typesafety.md`](docs/node-typesafety.md), [`docs/identity.md`](docs/identity.md),
[`docs/eval.md`](docs/eval.md), [`docs/observability-integration.md`](docs/observability-integration.md).

---

## 14. Wave 2026-10 (TKE · GW · DEC · AMR · PRT · MEM)

Las seis fases de la ola 2026-10 (v3.13.0 → v3.18.0; v3.19.0 añade el backend
Ollama de decisiones). Todas son opt-in y los ejemplos corren offline: ningún
request sale a la red (las claves de gateway las da un `secret_resolver` falso).

| Archivo | Fase | Demuestra | Resultado esperado | Guía |
|---------|------|-----------|-------------------|------|
| `token_efficiency.py` | TKE | Contabilidad cache-aware, layout `cache_stable`, prompt caching, presupuesto de salida de tools, TOON | `[1]` precio v3.12 → precio cache-aware y hit ratio; `[2]` system prompt idéntico entre requests y canario de prefijo detectado; `[3]` familia `anthropic_explicit` y breakpoints; `[4]` salida de 15 000 tokens recortada, íntegra en el artifact store; `[5]` tabla en TOON. | [`docs/token-efficiency.md`](docs/token-efficiency.md) |
| `gateways_openrouter.py` | GW | `gw:openrouter/...` | Modelo LiteLLM, headers y `extra_body` (`data_collection="deny"`, `zdr=true`, `session_id` hasheado; la key no aparece en el `repr`), sin doble fallback, variante `:free` rechazada (`variant_not_allowed`) y coste facturado con `source=gateway`. | [`docs/gateways.md`](docs/gateways.md) |
| `gateways_opencode.py` | GW | OpenCode Zen / Go | Protocolo de wire por modelo (Claude/GPT/GLM), ToS de Go (solo `coder`; `researcher` rechazado), `QuotaLedger` agotando la ventana de 5 h ⇒ downgrade y snapshot de ventanas 5h/semana/mes. | [`docs/gateways.md`](docs/gateways.md) |
| `gateways_omniroute.py` | GW | Gate de arranque de OmniRoute | 3.8.50 (CVE-2026-88062) deshabilitado y `resolve` rechazado (`omniroute_vulnerable_version`); 3.8.51 resuelve `openai/my-combo`. | [`docs/gateways.md`](docs/gateways.md) |
| `typed_decisions.py` | DEC | Cascada de decisiones tipadas | ECE antes/después de calibrar; ruta `coder` aceptada sin llamar al LLM; inyección "pre-approved" ⇒ escalado (`route=None`); sesgo de primera opción ⇒ `escalated (shuffle_inconsistent)`. | [`docs/decisions.md`](docs/decisions.md) |
| `adaptive_routing.py` | AMR | `RoutingPolicyPort` | Paridad (cada agente conserva `default_model`), cascada con escalado por errores de tool + afinidad, suelo `critical` ⇒ frontier, downgrade por soft cap y asignación por lotes de 4 sub-órdenes al tier `small`. | [`docs/routing.md`](docs/routing.md) |
| `agentic_memory.py` | MEM | Memoria v2 (`build_memory_service`) | Escrituras tipadas con dedupe y taint, consolidación idempotente, recall con presupuesto de tokens, recall cross-tenant rechazado, borrado por agente rechazado y borrado aprobado por un humano. | [`docs/memory.md`](docs/memory.md) |
| `agui_translate.py` | PRT | Traductor AG-UI 1.0 (`prismal.events.agui`) | Un `astream` falso traducido a frames SSE: `RUN_STARTED`, tool calls, eventos de subagente, mensajes de texto y `RUN_FINISHED` con `usage[]`. | [`docs/protocols-2026.md`](docs/protocols-2026.md) |

Flags en producción: `token_efficiency_enabled`, `llm_gateways_enabled` (+
`openrouter_enabled` / `opencode_zen_enabled` / `opencode_go_enabled` /
`omniroute_enabled`), `decisions_enabled` (+ `decision_<integración>_enabled`),
`adaptive_routing_enabled` (+ `routing_mode`), `memory_v2_enabled` (+
`memory_mode`), `agui_events_enabled`, `a2a_protocol_version`,
`mcp_protocol_mode`. Valores y descripciones en
[`docs/settings-reference.md`](docs/settings-reference.md).

```bash
uv run python examples/token_efficiency.py
uv run python examples/gateways_openrouter.py
uv run python examples/gateways_opencode.py
uv run python examples/gateways_omniroute.py
uv run python examples/typed_decisions.py
uv run python examples/adaptive_routing.py
uv run python examples/agentic_memory.py
uv run python examples/agui_translate.py
```

---

## 15. Utilidades

### `examples/visualize_graphs.py`

Imprime el diagrama Mermaid de cada subgraph reusable (sin compilar, sin red).
Útil para README/docs y para verificar la topología antes de compilar.

```bash
uv run python examples/visualize_graphs.py
```

Resultado: bloques Mermaid `flowchart TD` para customer_service,
document_generation, data_etl, code_review, debate_consensus y
multimodal_pipeline, más una nota de cómo renderizar el grafo supervisor
principal.

---

## 16. Troubleshooting

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| `ImportError: prismal.*` | Paquete no instalado | `uv pip install -e ".[dev,all]"` desde la raíz del repo |
| `langchain_core` falla con `pydantic_core._pydantic_core` | venv de otro host (símbolos de C++ no portables) | Recrear `.venv` con `uv venv && uv pip install -e ".[dev,all]"` |
| `litellm.exceptions.AuthenticationError` | Falta `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Añade la clave a `.env` o exporta como env var |
| `ChromaStoreError: collection already exists` | Colección persistente de una corrida anterior | `rm -rf data/chroma/` o usa otro `collection_name` |
| `FileNotFoundError: arxiv_papers.csv` | Falta el repo hermano `Langgraph_tutorials` | El ejemplo cae a su dataset embebido por defecto; clona el repo para usar el grande |
| `ModuleNotFoundError: faster_whisper` | Extras multimodales no instalados | `uv pip install -e ".[multimodal-local]"` |
| `MediaValidationError: media too large` | Archivo excede `settings.max_{image,audio,video}_bytes` | Sube el límite o recorta el archivo |
| El nodo se queda en bucle | El `domain_supervisor` no recibió `next_agent=None` | El loop-breaker dispara al detectar `AIMessage` de una hoja; si tu hoja devuelve `HumanMessage`, ajusta el supervisor |
| `Send` importado de `langgraph.constants` lanza warning | LangGraph v1.0 deprecó esa ruta | Importa desde `langgraph.types` (ya hecho en prismal) |
| `VectorStoreBackendUnavailable` | Falta el extra del backend elegido | `uv pip install -e ".[lancedb]"` (o `[sqlite-vec]`, `[qdrant]`, `[pgvector]`) |
| `ModuleNotFoundError` al usar A2A real | Falta el extra `[a2a]` | `uv pip install -e ".[a2a]"` (los ejemplos A2A usan transportes falsos) |
| Un setting en `.env` no tiene efecto | Falta el prefijo `PRISMAL_` o el nombre no existe | Busca el campo en `env.example` / `docs/settings-reference.md` (generados desde `Settings`) |
| `GatewayPolicyError` en un ejemplo de gateway | Es el comportamiento esperado (gate fail-closed) | Ver la sección 14 y [`docs/gateways.md`](docs/gateways.md) |

Si encuentras un fallo no listado, abre un issue con la traza completa y el
comando exacto que ejecutaste.

---

## Apéndice — Mapa rápido de archivo → patrón / componente

```
examples.md                           ← este archivo (raíz del repo)
examples/
├── README.md                         ← guía corta + tablas "¿qué usar?"
│
├── patterns/                         ← 9 patrones de agente
│   ├── 01_tree_of_thoughts.py        ← ToT (beam/BFS/DFS)
│   ├── 02_debate.py                  ← Debate + consenso Jaccard
│   ├── 03_lats.py                    ← MCTS sobre acciones
│   ├── 04_llm_compiler.py            ← DAG paralelo + Kahn
│   ├── 05_mixture_of_agents.py       ← N propositores + agregador
│   ├── 06_reflection_loop.py         ← Generar→criticar→refinar
│   ├── 07_constitutional_ai.py       ← P001/P002/P003 self-revision
│   ├── 08_swarm.py                   ← Handoff descentralizado
│   └── 09_parallel_dispatcher.py     ← Fan-out asyncio.gather
│
├── rag/                              ← 10 arquitecturas RAG
│   ├── 01_crag.py                    ← Corrective RAG
│   ├── 02_adaptive_rag.py            ← Routing por tipo de query
│   ├── 03_self_rag.py                ← Auto-evaluación + tokens
│   ├── 04_hyde.py                    ← Documento hipotético
│   ├── 05_hybrid_search.py           ← BM25 + semántico
│   ├── 06_hierarchical_rag.py        ← Chunks padre/hijo
│   ├── 07_rag_fusion.py              ← RRF
│   ├── 08_multi_vector_rag.py        ← chunk + summary + Q
│   ├── 09_multimodal_rag.py          ← text/image/audio/video
│   └── 10_federated_rag.py           ← N nodos + dedup por chunk_id
│
├── subgraphs/                        ← 12 pipelines compuestos
│   ├── 01_ml_pipeline.py             ← Ingester→…→Exporter
│   ├── 02_financial_analyst.py       ← Mercados + riesgo
│   ├── 03_dev_pipeline.py            ← PO→Arch→Dev→QA→Review
│   ├── 04_code_review.py             ← Lint + sec + lógica
│   ├── 05_data_etl.py                ← ETL + EDA + audit
│   ├── 06_customer_service.py        ← FAQ + escalación
│   ├── 07_document_generation.py     ← Plan→write→edit→format
│   ├── 08_debate_consensus.py        ← 2 voces + moderador
│   ├── 09_hitl_approval.py           ← interrupt() humano
│   ├── 10_analysis_orchestrator.py   ← Dominio analítico (4 hojas)
│   ├── 11_engineering_orchestrator.py← Dominio ingeniería (5 hojas)
│   └── 12_research_orchestrator.py   ← Dominio research (2 hojas)
│
├── multimodal/                       ← 5 componentes Fase F
│   ├── 01_vision_agent.py            ← Imagen + OCR
│   ├── 02_audio_agent.py             ← STT→reason→TTS
│   ├── 03_video_agent.py             ← Frames + audio + fusión
│   ├── 04_modality_router.py         ← Classifier + LangGraph node
│   └── 05_multimodal_fusion.py       ← concat / moderator / MoA
├── multimodal_pipeline.py            ← Multimodal end-to-end
├── multimodal_ingestion.py           ← ingest_media() + registro (F)
│
├── extension/                        ← Fase X surface
│   ├── custom_node.py                ← @prismal_node
│   ├── custom_subgraph.py            ← Builder fluent API
│   ├── discover_plugins_demo.py      ← Entry points discovery
│   └── langchain_migration.py        ← Runnable → node adapter
├── plugin_template/                  ← Skeleton instalable
│   ├── pyproject.toml                ← entry points
│   ├── src/prismal_x_example/{nodes,plugin}.py
│   └── tests/test_plugin.py
│
│   # Supervisor, multiagentes y tools (§9)
├── supervisor_quickstart.py          ← match_intent + grafo supervisor
├── hierarchical_supervisor.py        ← Domain + network supervisors
├── parallel_research_fanout.py       ← Send fan-out / fan-in
├── subagent_spawner.py               ← SubAgentSpawner
├── tool_provider_host.py             ← ToolProviderPort (host, Y)
├── tool_provider_custom.py           ← ToolProviderPort propio (Y)
│
│   # Agentes especializados y memoria (§10)
├── meta_learner.py                   ← MetaLearner + TraceScore
├── skill_creator_agent.py            ← create_skill + quality gates
├── memory_management.py              ← Memoria corto/largo plazo
│
│   # Capas opt-in (§11)
├── kokoro_deliberation.py            ← Kokoro (K)
├── skynet_swarm.py                   ← Skynet subgraph (S)
├── skynet_specialist_swarm.py        ← Skynet S+ (roles, metering)
├── skynet_direct_api.py              ← Skynet sin subgraph (S)
├── budget_governance.py              ← CostMeter + BudgetGuard (C)
├── budget_graph_integration.py       ← Seeding en el grafo (C)
├── a2a_server.py                     ← A2A inbound (I)
├── a2a_remote_node.py                ← A2A outbound (I)
├── a2a_tool_provider.py              ← A2A skills como tools (I)
├── blind_review_pipeline.py          ← Revisión ciega (BRP)
│
│   # Composición, configuración y vector stores (§12)
├── composition_root.py               ← build_runtime (R)
├── config_source_env.py              ← EnvConfigSource (W)
├── config_source_custom.py           ← Vault + por tenant (W)
├── vector_store_lancedb.py           ← VectorStoreFactory (Z)
│
│   # Seguridad, fiabilidad y observabilidad (§13)
├── runtime_hardening.py              ← Hardening (H)
├── guardrails_modernization.py       ← NeMo + StructuredOutputGuard (GRD)
├── loop_hardening.py                 ← Compactación + tool gating (LH)
├── node_typesafety.py                ← I/O tipado de nodos (NTS)
├── agent_identity.py                 ← DID + vault + políticas (IDN)
├── agent_eval.py                     ← EvalRunner (V)
├── observability_integration.py      ← ObservabilityPort (OBS)
│
│   # Wave 2026-10 (§14)
├── token_efficiency.py               ← Token efficiency (TKE)
├── gateways_openrouter.py            ← OpenRouter (GW)
├── gateways_opencode.py              ← OpenCode Zen/Go (GW)
├── gateways_omniroute.py             ← OmniRoute (GW)
├── typed_decisions.py                ← Decisiones tipadas (DEC)
├── adaptive_routing.py               ← Ruteo adaptativo (AMR)
├── agentic_memory.py                 ← Memoria v2 (MEM)
├── agui_translate.py                 ← AG-UI 1.0 (PRT)
│
└── visualize_graphs.py               ← Mermaid de subgraphs (§15)
```
