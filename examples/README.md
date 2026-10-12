# Ejemplos de prismal

Colección completa de ejemplos que cubre todas las arquitecturas de agentes IA,
sistemas RAG y capas opt-in disponibles en el framework `prismal` (v3.19.0).
Salvo que se indique lo contrario, los ejemplos de la raíz de `examples/` corren
**offline** con fakes inyectados (sin LLM, red ni API keys).

## Prerrequisitos

```bash
uv pip install -e ".[dev,all]"
```

Solo los ejemplos que llaman a un LLM real (patrones, RAG y subgraphs con su
backend por defecto) necesitan un proveedor configurado en `.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Claude (recomendado; alias de PRISMAL_ANTHROPIC_API_KEY)
OPENAI_API_KEY=sk-...          # GPT (opcional)
```

Todas las variables usan el prefijo `PRISMAL_`. La plantilla completa es
[`env.example`](../env.example), generada desde `Settings` junto con
[`docs/settings-reference.md`](../docs/settings-reference.md).

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
| 10 | `10_federated_rag.py` | Federated RAG | **KB multi-nodo** (sintético) | Búsqueda federada sobre N nodos remotos + KB local; `asyncio.gather`, tolerancia a nodos caídos y `_merge_and_rerank` con dedup por `chunk_id` |

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

# Federated RAG (multi-nodo, tolerante a fallos)
uv run python examples/rag/10_federated_rag.py
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

## Supervisor principal y multiagentes

El corazón del framework: el grafo SUPERVISOR de LangGraph y sus modos de
composición multiagente. Todos corren offline con fakes inyectados.

| Archivo | Descripción |
|---------|-------------|
| `supervisor_quickstart.py` | Grafo supervisor completo: `match_intent()` (enrutamiento determinista pre-LLM), `FakeToolProvider` vía `set_tool_provider` (Fase Y), `build_supervisor_graph()` con checkpointer en memoria, listado de nodos y `create_initial_state()` |
| `hierarchical_supervisor.py` | Modo jerárquico: `make_domain_supervisor()` standalone (3 salidas de enrutamiento) + `NetworkSupervisorAgent`/`NetworkNode` con delegación por capacidades y fallback local |
| `parallel_research_fanout.py` | Fan-out/fan-in de investigación paralela (Phase 34): `parallel_researcher_node` → dispatcher (`Send`) → workers → `research_aggregator_node`, incluido el camino `on_empty` |
| `subagent_spawner.py` | `SubAgentSpawner`: spawn dinámico de subagentes con semáforo de concurrencia, timeouts y `cancel_all()`/`cleanup()` |

```bash
uv run python examples/supervisor_quickstart.py
uv run python examples/hierarchical_supervisor.py
uv run python examples/parallel_research_fanout.py
uv run python examples/subagent_spawner.py
```

---

## Agentes especializados

| Archivo | Descripción |
|---------|-------------|
| `meta_learner.py` | `MetaLearner` + `TraceScore`: puntúa trazas de ejecución por dimensión, detecta agentes bajo umbral y genera propuestas de mejora con revisión humana obligatoria |
| `skill_creator_agent.py` | `create_skill` + `skill_creator_node`: generación de skills con quality gates (ruff/mypy/bandit), rechazo de código peligroso (AC-017-8) y sentinel de revisión humana |
| `memory_management.py` | Subsistema de memoria: `ShortTermMemory` (FIFO), `ConversationHistory` (transcript multi-canal) y `LongTermMemory` con redacción de PII/secretos y recall por sesión |

```bash
uv run python examples/meta_learner.py
uv run python examples/skill_creator_agent.py
uv run python examples/memory_management.py
```

---

## Capas opcionales (Kokoro · Skynet · Budget · A2A · Multimodal)

Ejemplos de las fases opt-in del framework. Todos deterministas y offline.

| Archivo | Fase | Descripción |
|---------|------|-------------|
| `kokoro_deliberation.py` | K | Subgraph kokoro completo (load_souls → deliberate → judge → act → output) con personas/juez falsos |
| `skynet_swarm.py` | S | Swarm map-reduce vía `build_skynet_subgraph` con fakes |
| `skynet_specialist_swarm.py` | S | Swarm con `RoleRegistry`/`SpecialistRole` (roles especializados) |
| `skynet_direct_api.py` | S | API directa sin subgraph: `SkynetSupervisor.plan/evaluate`, `SwarmWorker`, `reduce_results` (concat/first_success) y re-plan determinista de órdenes no cumplidas |
| `budget_governance.py` | C | `CostMeter` + `BudgetGuard` + `make_budget_guard_fn` a bajo nivel |
| `budget_graph_integration.py` | C | Seeding en el seam del grafo: `resolve_budget` → `seed_budget_run` → `get_budget_guard` → soft/hard → `BudgetExceeded` → `clear_budget_run` |
| `a2a_server.py` | I | Lado inbound: `AgentCard` + `A2AServerHandler` (JSON-RPC/SSE) |
| `a2a_remote_node.py` | I | Lado outbound: `A2AAgentNode` como nodo del grafo |
| `a2a_tool_provider.py` | I | `A2AToolProvider`: skills remotas como tools `a2a__{agent}__{skill}`, composición con `CompositeToolProvider` y wiring vía `build_runtime(a2a_agents=...)` |
| `multimodal_ingestion.py` | F | `ingest_media()` (descriptor por path bajo `metadata.mm.media`, validación de magic bytes) + `register_multimodal_pipeline()` en un registry propio |
| `blind_review_pipeline.py` | BRP | Pipeline de revisión ciega: spec → implementación → dos revisores ciegos → `synthesize_verdicts` determinista → bucle de corrección acotado ([`docs/blind-review-pipeline.md`](../docs/blind-review-pipeline.md)) |

```bash
uv run python examples/kokoro_deliberation.py
uv run python examples/skynet_swarm.py
uv run python examples/skynet_specialist_swarm.py
uv run python examples/skynet_direct_api.py
uv run python examples/budget_governance.py
uv run python examples/budget_graph_integration.py
uv run python examples/a2a_server.py
uv run python examples/a2a_remote_node.py
uv run python examples/a2a_tool_provider.py          # requiere el extra [a2a]
uv run python examples/multimodal_ingestion.py
uv run python examples/blind_review_pipeline.py
```

Guías: [`docs/kokoro.md`](../docs/kokoro.md), [`docs/skynet.md`](../docs/skynet.md),
[`docs/budget.md`](../docs/budget.md), [`docs/a2a.md`](../docs/a2a.md).

---

## Extensión, composición y configuración

| Archivo | Fase | Descripción |
|---------|------|-------------|
| `extension/custom_node.py` | X | `@prismal_node` con la cadena de middleware completa |
| `extension/custom_subgraph.py` | X | `PrismalStateGraphBuilder` fluent API → `SubgraphDefinition` |
| `extension/discover_plugins_demo.py` | X | Descubrimiento de plugins por entry points |
| `extension/langchain_migration.py` | X | `LangChainRunnableAdapter`: cualquier `Runnable` como nodo prismal |
| `plugin_template/` | X | Scaffold completo de plugin instalable (`prismal.subgraphs`) |
| `composition_root.py` | R | `build_runtime()`/`build_test_runtime()`: los 5 puertos compuestos + multi-tenant (`collection_for`) |
| `config_source_env.py` / `config_source_custom.py` | W | `ConfigSourcePort`: fuentes env/mapping/encadenadas inyectadas en `Settings` |
| `vector_store_lancedb.py` | Z | `VectorStoreFactory` con backend alternativo (LanceDB) |
| `node_typesafety.py` | NTS | Validación tipada (Pydantic `input_model`/`output_model`) de entrada/salida en nodos `@prismal_node` (`off\|warn\|enforce`) |
| `visualize_graphs.py` | — | `to_mermaid()`/`visualize()` sobre grafos y `SubgraphDefinition` |
| `agent_eval.py` | V | Harness de evaluación sobre el grafo compilado real con fakes (`EvalRunner`, aserciones, gate de regresión) |
| `agent_identity.py` | IDN | DID por agente, credenciales con scopes desde un vault, delegación on-behalf-of y `PolicyEngine` |
| `runtime_hardening.py` | H | Taint tracking, `IndirectInjectionDetector`, `OutputValidator`, `ToolPolicyEngine`, `RunawayGuard` |
| `guardrails_modernization.py` | GRD | Rails NeMo + clasificador de seguridad con razonamiento y `StructuredOutputGuard` |
| `loop_hardening.py` | LH | `ContextCompactor` + `resolve_phase()` y gating de herramientas por fase |
| `observability_integration.py` | OBS | `ObservabilityPort`: `RunSummary` consultable, paridad LangSmith/Langfuse, export de datasets |

```bash
uv run python examples/extension/custom_node.py
uv run python examples/extension/custom_subgraph.py
uv run python examples/extension/discover_plugins_demo.py
uv run python examples/extension/langchain_migration.py
uv run python examples/composition_root.py
uv run python examples/config_source_env.py
uv run python examples/config_source_custom.py
uv run python examples/vector_store_lancedb.py      # requiere el extra [lancedb]
uv run python examples/node_typesafety.py
uv run python examples/visualize_graphs.py
uv run python examples/agent_eval.py
uv run python examples/agent_identity.py
uv run python examples/runtime_hardening.py
uv run python examples/guardrails_modernization.py
uv run python examples/loop_hardening.py
uv run python examples/observability_integration.py
```

El scaffold `plugin_template/` es un paquete instalable aparte (ver su
[`README.md`](./plugin_template/README.md)): `uv pip install -e examples/plugin_template`
y luego `uv run python -m prismal.plugins list` / `doctor` para verificar el descubrimiento.

Guías: [`docs/extension.md`](../docs/extension.md), [`docs/composition-root.md`](../docs/composition-root.md),
[`docs/configuration.md`](../docs/configuration.md), [`docs/vector-stores.md`](../docs/vector-stores.md),
[`docs/node-typesafety.md`](../docs/node-typesafety.md), [`docs/eval.md`](../docs/eval.md),
[`docs/identity.md`](../docs/identity.md), [`docs/security/`](../docs/security/),
[`docs/loop-hardening.md`](../docs/loop-hardening.md), [`docs/observability-integration.md`](../docs/observability-integration.md).

---

## Wave 2026-10 (TKE · GW · DEC · AMR · PRT · MEM)

Las seis fases de la ola 2026-10 (v3.13.0 → v3.18.0). Todas son **opt-in**: con
su flag maestro apagado el grafo es byte a byte idéntico (snapshot-tested). Los
ejemplos activan los flags en un `Settings(...)` local, así que **no hace falta
tocar `.env`** ni instalar extras, y ninguno envía peticiones reales (sin API
keys: las claves de gateway se inyectan con un `secret_resolver` falso).

| Archivo | Fase | Qué muestra | Flags (en producción) / extras / keys | Guía |
|---------|------|-------------|---------------------------------------|------|
| `token_efficiency.py` | TKE | (1) precio v3.12 vs. contabilidad cache-aware de `CostMeter`; (2) layout `cache_stable` de `SecurePromptBuilder` (system prompt idéntico + canario de prefijo detectable); (3) breakpoints de `apply_cache_policy` (Anthropic); (4) `ToolOutputBudget` recorta una salida enorme y la guarda íntegra en `InMemoryArtifactStore`; (5) tabla uniforme codificada en TOON | `token_efficiency_enabled` + `prompt_layout="cache_stable"`, `prompt_canary_secret`, `tool_output_budget_enabled`, `tool_output_toon_enabled`. Extras opcionales de contexto RAG: `[rerank]`, `[compression]` | [`docs/token-efficiency.md`](../docs/token-efficiency.md) |
| `gateways_openrouter.py` | GW | Resolución de `gw:openrouter/...` (modelo LiteLLM, headers, `extra_body` con `data_collection="deny"` + `zdr=true` y `session_id` hasheado); fallback del gateway (`models[]`) sin doble fallback; gate de política fail-closed (variante `:free` fuera de la allow-list); medición del coste facturado (`source="gateway"`) | `llm_gateways_enabled` + `openrouter_enabled`; key `PRISMAL_OPENROUTER_API_KEY` (o vault por tenant) | [`docs/gateways.md`](../docs/gateways.md) |
| `gateways_opencode.py` | GW | OpenCode Zen: protocolo de wire por modelo (Anthropic Messages / Responses / Chat Completions); guardia de ToS de OpenCode Go (solo agentes de código); `QuotaLedger` de tarifa plana con downgrade antes de cualquier llamada | `llm_gateways_enabled` + `opencode_zen_enabled` / `opencode_go_enabled`, `opencode_go_monthly_cap_usd`, `opencode_go_quota_action`; key `PRISMAL_OPENCODE_API_KEY` | [`docs/gateways.md`](../docs/gateways.md) |
| `gateways_omniroute.py` | GW | Gate de arranque fail-closed de OmniRoute contra un `/api/monitoring/health` falso (`httpx.MockTransport`): 3.8.50 (CVE-2026-88062) se rechaza y el router lo deshabilita; 3.8.51 resuelve modelos | `llm_gateways_enabled` + `omniroute_enabled`, `omniroute_upstream_attestation`; key `PRISMAL_OMNIROUTE_API_KEY` | [`docs/gateways.md`](../docs/gateways.md) |
| `typed_decisions.py` | DEC | Calibración (temperatura) sobre un set etiquetado sintético con caída de ECE; ruteo del supervisor resuelto por la cascada sin llamada al LLM; inyección "pre-approved" ⇒ estado tainted y escalado; sesgo de primera opción detectado por el shuffle en una `Choice` de riesgo HIGH | `decisions_enabled` + `decision_routing_enabled` (y demás `decision_<integración>_enabled`), `decision_modes`. Backends reales: extras `[laya]` / `[jev]` (key `PRISMAL_JEV_API_KEY`) u Ollama local; el ejemplo usa `FakeDecisionAdapter` | [`docs/decisions.md`](../docs/decisions.md) |
| `adaptive_routing.py` | AMR | Mapa de tiers de paridad (cada agente conserva `default_model`); cascada que escala tras errores de herramienta + afinidad de caché; suelo de riesgo `critical` ⇒ frontier; downgrade por soft cap de presupuesto; asignación por lotes (Skynet) bajo un suelo de precisión | `adaptive_routing_enabled`, `routing_mode` (`shadow` por defecto / `enforce`), `routing_policy`, `routing_tier_models`, `routing_predictor_path` | [`docs/routing.md`](../docs/routing.md) |
| `agentic_memory.py` | MEM | Escrituras tipadas con dedupe y taint (contenido de herramienta ⇒ solo episodio), consolidación idempotente (episodios ⇒ hecho semántico), recall con presupuesto de tokens, recall cross-tenant rechazado, borrado por agente rechazado y borrado aprobado por un humano | `memory_v2_enabled`, `memory_mode` (`shadow` / `enforce`), `memory_v2_backend`, `memory_write_gate`, `memory_graph_backend` (`sqlite`; Graphiti vía extra `[graph-memory]`) | [`docs/memory.md`](../docs/memory.md) |
| `agui_translate.py` | PRT | Traduce un `graph.astream(stream_mode=["messages","updates","custom"])` falso a eventos AG-UI 1.0 y frames SSE (`start_run` → `to_agui_events` → `finish_run` con `RunSummary`/usage) | Ninguno para el traductor puro; en un host, `agui_events_enabled`. A2A v1.0 y MCP stateless (`a2a_protocol_version`, `mcp_protocol_mode`) no tienen ejemplo propio | [`docs/protocols-2026.md`](../docs/protocols-2026.md) |

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
│   ├── patterns/          ← 7 patrones de razonamiento + 2 primitivas (ToT, Debate, LATS, …)
│   ├── subgraphs/         ← 15 paquetes de subgraphs (ML, Financial, Dev, Kokoro, Skynet, …)
│   ├── graph.py           ← Grafo supervisor principal (30 rutas: 15 core + 15 opt-in)
│   └── state.py           ← AgentState (TypedDict con add_messages)
├── rag/                   ← 11 motores RAG + VectorStorePort (rag/stores/)
├── providers/             ← Abstracción de LLMs (LiteLLM) + gateways/ (OpenRouter, OpenCode, OmniRoute)
├── security/              ← 5 capas de defensa en profundidad + hardening
├── memory/                ← Memoria corto/largo plazo + v2/ (memoria agéntica, opt-in)
├── budget/ · routing/ · decisions/   ← Presupuesto, ruteo adaptativo de modelos, decisiones tipadas
├── a2a/ · mcp/ · events/             ← A2A v0.3/v1.0, cliente MCP, traductor AG-UI
└── composition/ · identity/ · eval/  ← build_runtime(), identidad de agentes, harness de evaluación
```

Referencia completa: `CLAUDE.md` en la raíz del repositorio, las guías de
[`docs/`](../docs/) y la guía en español [`examples.md`](../examples.md).
