# prismal · Guía de ejemplos

Documentación completa del directorio `examples/`. Cubre los **34 ejemplos
ejecutables** organizados en 5 categorías (patterns, RAG, subgraphs, multimodal,
extension) más la plantilla de plugin.

> Para descripciones cortas y tablas comparativas de "¿qué usar?", ver
> [`examples/README.md`](examples/README.md). Este archivo es la referencia
> larga: incluye comandos, datasets, prerequisitos y **lo que se espera ver
> impreso** en cada ejemplo.

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
9. [Utilidades](#9-utilidades)
10. [Troubleshooting](#10-troubleshooting)

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
Anthropic, OpenAI), LangChain, scikit-learn, ruff/mypy/pytest. Para los
ejemplos multimodales completos añade el extra que corresponda:

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
imprimen un resumen. Ninguno requiere argumentos posicionales.

---

## 2. Variables de entorno

Crea `.env` en la raíz del repo. Mínimo recomendado:

```bash
# Al menos uno de estos
ANTHROPIC_API_KEY=sk-ant-…   # Claude (recomendado para mayoría de demos)
OPENAI_API_KEY=sk-…          # GPT-4 / Whisper / DALL-E

# Multimodal premium (opcional)
ELEVENLABS_API_KEY=…
GOOGLE_API_KEY=…             # Gemini multimodal

# Plugin discovery (opcional)
PRISMAL_PLUGINS_ALLOWLIST=prismal-x-mydomain
PRISMAL_PLUGINS_DENYLIST=

# Modo jerárquico (opcional, activa los 3 orquestadores)
PRISMAL_HIERARCHICAL_MODE=true
```

**Los siguientes ejemplos NO necesitan API keys** (corren con stubs/mocks):

- `examples/patterns/09_parallel_dispatcher.py`
- `examples/multimodal/*` (todos)
- `examples/multimodal_pipeline.py`
- `examples/visualize_graphs.py`
- `examples/extension/*`
- `examples/subgraphs/10_analysis_orchestrator.py` (modo simulación)
- `examples/subgraphs/11_engineering_orchestrator.py` (simulación + LangGraph stub)
- `examples/subgraphs/12_research_orchestrator.py` (simulación + LangGraph stub)

El resto **usan llamadas LLM reales** — sin clave se obtendrá un error en
tiempo de ejecución desde el provider.

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

Carpeta: `examples/patterns/` · 9 ejemplos.

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

Carpeta: `examples/rag/` · 9 ejemplos.

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
```

> Los ejemplos 01–08 escriben a una colección ChromaDB persistente bajo
> `data/chroma/`. Bórralo entre corridas si quieres ver el re-índice desde
> cero: `rm -rf data/chroma/`. El 09 usa un store en memoria.

---

## 6. Subgraphs

Carpeta: `examples/subgraphs/` · 12 ejemplos.

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

Carpeta: `examples/multimodal/` · 5 ejemplos granulares + `examples/multimodal_pipeline.py` end-to-end.

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

```bash
uv run python examples/multimodal/01_vision_agent.py
uv run python examples/multimodal/02_audio_agent.py
uv run python examples/multimodal/03_video_agent.py
uv run python examples/multimodal/04_modality_router.py
uv run python examples/multimodal/05_multimodal_fusion.py
uv run python examples/multimodal_pipeline.py
```

---

## 8. Extensión y plugins

### 8.1 `examples/extension/` · 4 demos del extension surface (Fase X)

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

## 9. Utilidades

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

## 10. Troubleshooting

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

Si encuentras un fallo no listado, abre un issue con la traza completa y el
comando exacto que ejecutaste.

---

## Apéndice — Mapa rápido de archivo → patrón / componente

```
examples/
├── README.md                         ← guía corta + tablas "¿qué usar?"
├── examples.md                       ← este archivo
├── multimodal_pipeline.py            ← Multimodal end-to-end demo
├── visualize_graphs.py               ← Mermaid de subgraphs
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
├── rag/                              ← 9 motores RAG
│   ├── 01_crag.py                    ← Corrective RAG
│   ├── 02_adaptive_rag.py            ← Routing por tipo de query
│   ├── 03_self_rag.py                ← Auto-evaluación + tokens
│   ├── 04_hyde.py                    ← Documento hipotético
│   ├── 05_hybrid_search.py           ← BM25 + semántico
│   ├── 06_hierarchical_rag.py        ← Chunks padre/hijo
│   ├── 07_rag_fusion.py              ← RRF
│   ├── 08_multi_vector_rag.py        ← chunk + summary + Q
│   └── 09_multimodal_rag.py          ← text/image/audio/video
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
│
├── extension/                        ← Fase X surface
│   ├── custom_node.py                ← @prismal_node
│   ├── custom_subgraph.py            ← Builder fluent API
│   ├── discover_plugins_demo.py      ← Entry points discovery
│   └── langchain_migration.py        ← Runnable → node adapter
│
└── plugin_template/                  ← Skeleton instalable
    ├── pyproject.toml                ← entry points
    ├── src/prismal_x_example/
    │   ├── nodes.py
    │   └── plugin.py
    └── tests/test_plugin.py
```
