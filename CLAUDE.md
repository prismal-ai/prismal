# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Package context

`prismal` is the **agent framework layer** extracted from the larger `lightagent` monorepo. It is a standalone, publishable PyPI package containing everything needed to build and run AI agents — no web server, dashboard, or CLI. It was published as `lightagent-agents` through v2.x and **rebranded to `prismal` in v3.0.0** (distribution name plus the `lightagent.*` → `prismal.*` import namespace; see `docs/history/PLAN_MIGRACION.md`). The sibling app package (still named `lightagent`) historically depended on this one and shared the import namespace — see the namespace note below.

Current version: **3.19.0** (single source of truth: `pyproject.toml`; history in `CHANGELOG.md`; release tag format `prismal/vMAJOR.MINOR.PATCH`). Pushing a release tag triggers both `release.yml` (PyPI + GitHub Release with notes extracted from the CHANGELOG section) and `docker-publish.yml` (container image to GHCR `ghcr.io/prismal-ai/prismal`, tagged `X.Y.Z` + `latest`; manual dispatch publishes `dev`).

## Common commands

All commands assume `uv` and Python 3.13+. Dev tooling lives in `pyproject.toml` under `[project.optional-dependencies].dev`.

```bash
# Install with dev tools
uv pip install -e ".[dev]"
# or with extras:
uv pip install -e ".[dev,all]"

# Test suite (pytest with asyncio_mode=auto, filterwarnings=error)
uv run pytest                                        # full suite
uv run pytest tests/unit                             # one tier
uv run pytest -m unit                                # by marker (unit|integration|security|slow|live_api)
uv run pytest tests/unit/security/test_sanitizer.py::test_strip_removes_null_byte  # single test
uv run pytest -n auto                                # parallel (pytest-xdist)
uv run pytest --cov=prismal --cov-report=term-missing   # coverage (target fail_under=80)

# Lint + format (ruff is the only linter/formatter; line-length=100, target py313)
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .

# Type-check (mypy strict mode; namespace_packages=true)
uv run mypy prismal

# Security linting
uv run bandit -r prismal -c pyproject.toml

# Build distribution
uv run python -m build

# Container image (multi-stage; base install only — extras via a derived image)
docker build -t prismal .
```

`live_api` tests call real LLM APIs and require provider keys; skip them locally with `-m "not live_api"`.

## Architecture

### Namespace package

`prismal/` has **no `__init__.py`** — it is a PEP 420 implicit namespace package (renamed from `lightagent/` in v3.0.0). Do not add `prismal/__init__.py`; it must stay an implicit namespace package.

During the migration a transitional shim `lightagent/__init__.py` redirected `lightagent.* → prismal.*`; it was **removed in v3.0.0** once all code, tests, and examples were migrated, so `from lightagent. …` no longer resolves in this repo. End-user backward compatibility is instead provided by the deprecated `lightagent-agents` distribution (a thin package that depends on `prismal`). The `lightagent.*` → `prismal.*` rename **breaks the sibling app package** that previously shared the namespace — it must be rebranded/coordinated in tandem (tracked as a post-migration step).

### LangGraph SUPERVISOR state machine

The core is a LangGraph `StateGraph[AgentState]` assembled in `prismal/agents/graph.py`. A central `supervisor_node` routes each turn to one of **30 routes**: the 15 core `MEMBERS` (`researcher`, `coder`, `codeact`, `cua`, `rag_agent`, `planner`, `critic`, `data_analyst`, `file_manager`, `skill_manager`, `cron_manager`, `dev_pipeline`, `ml_pipeline`, `financial_analyst`, `parallel_researcher`) plus 15 opt-in members (`ADVANCED_MEMBERS` ×11, `MULTIMODAL_MEMBERS`, `KOKORO_MEMBERS`, `SKYNET_MEMBERS`, `BLIND_REVIEW_MEMBERS`), which each return control to the supervisor; the supervisor routes to `END` when done. `meta_learner`, `skill_creator`, `network_supervisor`, `spawner` and `factory` are standalone helper modules, not routes; `domain_supervisor` backs the hierarchical mode (`hierarchical_mode=True` → analysis/engineering/research orchestrators). Full inventory: `docs/agents.md`.

- **Entry points**: `get_compiled_graph()` (sync) and `get_async_compiled_graph()` (async, module-level singleton; the sync one is `lru_cache`d and never adds optional nodes). Async contexts must use the async variant — it wires `AsyncSqliteSaver` and the opt-in routes. Call `await aclose_async_compiled_graph()` at process shutdown: the aiosqlite worker thread is non-daemon, so an unclosed checkpointer keeps the process alive.
- **State**: `AgentState` is a `TypedDict`. `messages` uses `add_messages`; `retrieved_docs`, `tool_results` and `parallel_results` use `operator.add`; all other fields use plain merge semantics.
- **Routing wrapper**: `_supervisor_router` in `graph.py` exists purely so LangGraph's `get_type_hints()` can resolve `AgentState` — do not remove it.
- **Checkpointing**: `build_checkpointer()` supports SQLite (default) and PostgreSQL (via `[postgres]` extra).
- **Tool cap**: `agents/tool_registry.py` enforces a global `_MAX_TOTAL_TOOLS = 120` (Phase 44).
- **Intent routing**: `agents/intent_router.py::match_intent()` is deterministic regex-based, ahead of LLM supervision.

### Subgraphs and patterns

`agents/subgraphs/` holds composed multi-node pipelines wrapped as reusable subgraphs: `dev_pipeline` (PO → Architect → Developer → Tests → QA → Reviewer), `ml_pipeline` (Ingester → EDA → Features → Trainer → Evaluator → Exporter), `financial` (Collector → Technical → Fundamental → Risk → Report), plus `analysis_orchestrator`, `engineering_orchestrator`, `research_orchestrator`. They are built by `SubgraphFactory` and registered in `SubgraphRegistry`.

**Advanced architectures (Fase A/B/C — `specs/advanced-architectures/`)** added 7 RAG engines (HyDE … Adaptive; with the standard engine, CRAG, Multimodal and Federated the package now has 11 — `docs/rag.md`), 7 agent patterns (6 of them wired as supervisor routes under `enable_subgraphs`; swarm is a library primitive — `docs/patterns.md`), and 5 subgraph pipelines (15 subgraph packages in total — `docs/subgraphs.md`). All follow the same factory-injection pattern: business logic accepts callables (`generate_fn`, `evaluate_fn`, `reward_fn`, `plan_fn`, `tool_executor`, `linter_fn`, …) so tests run without LLM backends. Defaults wire `ProviderRegistry().get_llm()` lazily.

- `rag/hyde.py` — HyDE retriever (hypothetical doc embeddings).
- `rag/fusion.py` — RAG-Fusion (multi-query + `reciprocal_rank_fusion`).
- `rag/hybrid.py` — BM25 + semantic hybrid (`rank-bm25` dep).
- `rag/self_rag.py` — retrieval on demand + self-assessment.
- `rag/hierarchical.py` — parent/child chunk indexing.
- `rag/multi_vector.py` — chunk + summary + N hypothetical questions.
- `rag/adaptive.py` — facade routing to the above by query type.
- `agents/patterns/tree_of_thoughts.py` — ToT with beam / BFS / DFS.
- `agents/patterns/debate.py` — N-agent multi-round debate + Jaccard agreement.
- `agents/patterns/constitutional.py` — principle-driven self-revision + audit.
- `agents/patterns/lats.py` — MCTS (UCB1 balanced exploration).
- `agents/patterns/llm_compiler.py` — DAG of tasks, Kahn validation, parallel waves.
- `agents/patterns/mixture_of_agents.py` — parallel proposers + aggregator synthesis.
- `agents/patterns/swarm.py` — decentralised agent handoff with audit.
- `agents/subgraphs/customer_service/` — classifier → faq_retrieval → (escalation gate) → response | ticket.
- `agents/subgraphs/document_generation/` — planner → researcher → writer → editor → formatter.
- `agents/subgraphs/data_etl/` — extractor → validator → (gate) → transformer → loader → auditor.
- `agents/subgraphs/code_review/` — linter → security_scanner → logic_reviewer → suggester → report_generator.
- `agents/subgraphs/debate_consensus/` — proponent → opponent → moderator → consensus.

These newer subgraphs export both `build_<name>_subgraph()` (returns `SubgraphDefinition`) and `register_<name>()` (idempotent registry install); the older dev/ml/financial pipelines and the three orchestrators use `_make_definition()` + `get_compiled_*` instead (9 `build_*`, 15 `register_*` in total).

`agents/patterns/` provides composable primitives: `reflection_loop()` (generate → critique → refine) and `make_parallel_dispatcher()` (fan-out via LangGraph `Send()`).

`agents/subgraphs/gates.py::hitl_gate()` uses `interrupt()` for Human-in-the-Loop pauses.

### Multimodal layer (Fase F — `specs/multimodal-agents/`, implemented, opt-in)

A multimodal capability layer covers audio, image, and video on top of the existing text agents. It is **opt-in**: gated by `settings.multimodal_enabled` (default `False`). Without the toggle, the text routes behave identically to today. When `multimodal_enabled=True`, `get_async_compiled_graph()` wires a single `multimodal_pipeline` supervisor route (the subgraph fans out to vision/audio/video internally and emits the answer); `effective_valid_routes`/`build_system_prompt` gate on the flag, and `intent_router.match_intent()` returns `multimodal_pipeline` for media intents. Incoming attachments reach the pipeline via `agents/multimodal/ingestion.py::ingest_media()`, which spills bytes to a content-addressed file and records a path-based descriptor under `state["metadata"]["mm"]["media"]` (never raw bytes in checkpointed state).

- `providers/stt.py`, `providers/tts.py`, `providers/vision.py`, `providers/multimodal.py`, `providers/cross_modal_embeddings.py` — provider wrappers (Whisper, pyttsx3/openai/elevenlabs, vision LLM, Gemini/GPT-4o/Sonnet, CLIP). All provider SDK imports stay isolated here.
- `agents/multimodal/vision_agent.py` — `VisionAgent` (analyze + optional OCR).
- `agents/multimodal/audio_agent.py` — `AudioAgent` (STT → reason → optional TTS).
- `agents/multimodal/video_agent.py` — `VideoAgent` (FFmpeg frame extraction via `SandboxExecutor` + audio transcribe + fusion).
- `agents/multimodal/modality_router.py` — heuristic classifier `classify_modality()` + LangGraph node factory; LLM fallback opt-in.
- `agents/multimodal/multimodal_fusion.py` — `MultimodalFusion` with `moa | moderator | concat` strategies (reuses `patterns/mixture_of_agents.py`).
- `agents/subgraphs/multimodal_pipeline/` — `router → [vision | audio | video | text] → fusion → output_formatter`; exports `build_multimodal_subgraph()` + `register_multimodal_pipeline()`.
- `rag/multimodal.py` — `MultimodalRAGEngine` with `MultimodalRetrievedChunk` and `modality` metadata on Chroma; cross-modal embeddings (CLIP) are an opt-in extra, otherwise the engine falls back to textual captions/transcripts.
- `rag/loaders/{image,audio,video}_loader.py` — multimodal loaders composing the agents.
- `security/media_validator.py` — `MediaValidator` (magic bytes, size/duration limits) runs **before** `InputSanitizer.sanitize_media()`; `AuditLogger.log_media()` records hash + modality (never content); FFmpeg always inside `SandboxExecutor`.

Heavy dependencies (Whisper local, ElevenLabs, CLIP, FFmpeg wrappers, Pillow, imagehash) are gated by optional extras `[multimodal]`, `[multimodal-local]`, `[multimodal-premium]`, `[multimodal-embed]` so the base install stays slim.

All multimodal state lives under `state["metadata"]["mm"]` to isolate the new layer from the rest of `AgentState`.

### Extension surface (Fase X — `specs/extension-surface/`, implemented)

A public extension API exposes LangGraph as a first-class build target for users and third-party plugins, without forcing them to fork the repo. It is opt-in and additive — existing nodes/subgraphs are unaffected. All public symbols are re-exported from `prismal/agents/extension/__init__.py`; user guide in `docs/extension.md`, runnable examples in `examples/extension/` + `examples/plugin_template/`.

- `prismal/langgraph.py` — official re-export of `StateGraph`, `START`, `END`, `Send`, `interrupt`, `add_messages`, `CompiledStateGraph` plus `AgentState`, `SubgraphDefinition`, `SubgraphRegistry`, and a `VERSION` constant resolved dynamically from `importlib.metadata`. Importing from here (rather than `langgraph.*` directly) guarantees version compatibility.
- `agents/extension/decorators.py` — `@prismal_node(name=..., capabilities=..., security=..., audit=..., retry=..., timeout_s=...)` wraps any `async (state) → state_update` with a middleware chain: `InputSanitizer`+`SecurePromptBuilder`+`ActionInterceptor` → OTel span → structured logger bind → retry/backoff → `asyncio.wait_for` → user fn → audit log → error mapping to `NodeExecutionError`. Side effect: registers the node's capabilities in `tool_registry.DEFAULT_CAPABILITY_MAP`.
- `agents/extension/builder.py` — `PrismalStateGraphBuilder` fluent API over `StateGraph[AgentState]`. `add_node()` auto-wraps callables with `@prismal_node` if they lack the `__prismal_node__` attribute. `compile()` returns a `SubgraphDefinition` ready for `SubgraphRegistry`; `compile_raw()` is the escape hatch returning `CompiledStateGraph`.
- `agents/extension/plugins.py` — `discover_plugins(settings)` iterates `importlib.metadata.entry_points()` across four groups (`prismal.subgraphs`, `prismal.nodes`, `prismal.tools`, `prismal.rag_engines`), applies allowlist/denylist, and registers each plugin in isolation (failures don't abort startup). `prismal/plugins.py` is a `python -m prismal.plugins` CLI (`list`, `info`, `doctor`).
- `agents/extension/adapters.py` — `LangChainRunnableAdapter(runnable).as_node(name=..., capabilities=...)` converts any `Runnable` or `AgentExecutor` into a prismal node, with auto input/output mapping between `state["messages"]` and the Runnable's signature.
- `agents/extension/ports.py` — formal `Protocol`s for `CheckpointPort`, `AuditPort`, `EmbeddingsPort`, `ToolPort`, `ToolProviderPort`. Existing implementations (`AsyncSqliteSaver`, `AuditLogger`, ChromaDB embeddings, `BaseTool`) conform structurally; users substitute with their own adapters without modifying the core.

Plugin authors declare entry points in their `pyproject.toml` (e.g. `[project.entry-points."prismal.subgraphs"] my_pipeline = "my_pkg:register_my_pipeline"`); after `pip install`, `discover_plugins()` auto-registers them. Allowlist/denylist via `settings.plugins_allowlist` / `plugins_denylist`. Recommended convention: namespace plugins as `prismal-x-<domain>`.

**Implementation notes (deviations from the spec text — keep in mind when editing):**
- `Send` is imported from `langgraph.types` (not `langgraph.constants`, deprecated in LangGraph v1.0 and an error under `filterwarnings=error`).
- The `@prismal_node` middleware order is **outermost→innermost: error_mapping → otel → logger → security → audit → retry → timeout → user fn** (intentional fix of the SPEC's contradictory ordering so retries run before error mapping and audit duration spans all attempts). `_middleware._tool_call_checker` is a monkeypatchable seam used by `security="strict"`.
- `builder.compile()` returns the **real** `SubgraphDefinition` (`nodes`/`edges`/`conditional_edges`/`entry_point`), not a `compiled_graph`; `compile_raw()` returns a `CompiledStateGraph`.
- Subgraph plugins register via the new **sync** `SubgraphRegistry.register_sync()` (the existing `register_<name>()` functions remain async and use the singleton). `discover_plugins()` is sync; a subgraph entry point either self-registers via `register_sync` or returns a `SubgraphDefinition`.
- The 8th SPEC middleware "validation" is folded into `error_mapping` (`NodeValidationError` available for callers that validate explicitly).

### Tool provider injection (Fase Y — `specs/tool-provider-injection/`, implemented)

Tool resolution is inverted as a hexagonal port: the agent core asks an injected `ToolProviderPort` (`get_tools(*, agent_name, capabilities) -> list[BaseTool]`, sync, must not raise) and **no longer imports `prismal.mcp` / `prismal.skills`** — enforced by an AST architecture test (`tests/unit/agents/extension/test_no_mcp_skills_imports.py`; exemptions: `extension/providers.py` and `skill_manager.py`). User guide: `docs/tool-providers.md`; runnable examples: `examples/tool_provider_{host,custom}.py`.

- `agents/extension/providers.py` — host-facing adapters (allowed to lazy-import mcp/skills): `McpToolProvider` (cap 60), `SkillToolProvider` (fresh `SkillsManager` per call), `StubToolProvider` (the old `stub_map`), `CompositeToolProvider` (exact merge parity: MCP→Skills→stubs priority, name dedupe, `max_total=120`, fixed-tool agents `{cron_manager, critic}` get stubs only), `FakeToolProvider` (tests), and async `build_default_tool_provider(settings)` for the host lifespan.
- `agents/tool_registry.py` — `set_tool_provider()` / `get_tool_provider()` (variante A, global); `get_tools_for_agent()` keeps its signature and delegates; **no provider → stub-only fallback + `tool_registry.no_provider` warning** (behaviour change: skills are no longer loaded implicitly), or `ToolProviderNotConfigured` when `settings.tool_provider_strict`. Variante B (multi-tenant): `get_async_compiled_graph(tool_provider=...)` + `tool_provider_mode="context"` binds a per-session provider via `with_config`; nodes resolve with `get_tools_for_agent_ctx(name, config)` / `resolve_provider(config)`.
- Deprecated shims (`init_mcp`, `get_mcp_tools`, `get_skill_tools`) delegate to the injected provider, emit `DeprecationWarning`, and are removed in the next minor. The policy caps live in `providers.py`; `tool_registry` keeps equal legacy constants (a parity test asserts both stay in sync).
- Observability: span `prismal.tools.resolve` + counters `prismal.tool_provider_resolved_total{provider}`, `prismal.tools_injected_total{agent}`, `prismal.tool_provider_fallback_total`, `prismal.tool_provider_subprovider_errors_total{provider}` (registered in `OTelManager`).

### Kokoro deliberation (Fase K — `specs/kokoro-deliberation/`, implemented, opt-in)

A persona-driven deliberation layer: three Markdown-authored **souls** (心 — Spirit / Mind / Heart) argue a question toward agreement, then a single **judge** ("the whole, more than the sum of its parts") renders the accountable decision and optionally executes one gated tool action. Gated by `settings.kokoro_enabled` (default `False`) — with the flag off the compiled supervisor graph is byte-for-byte unchanged. User guide: `docs/kokoro.md`; example: `examples/kokoro_deliberation.py`.

- `prismal/souls/` — the **souls tier** (mirrors `skills/`): `available/{spirit,mind,heart}/SOUL.md` Markdown personas (committed), `base.py` (`Soul`, `parse_soul_md()`, `load_soul()`), `manager.py` (`SoulsManager.load_triad()`). Souls are user-controlled content — their bodies reach the model only through `SecurePromptBuilder`.
- `agents/kokoro/soul_agent.py` — `SoulAgent` (one persona's argument turn).
- `agents/kokoro/deliberation.py::deliberate()` — bounded multi-round argue→agree loop (`kokoro_max_rounds`, `kokoro_agreement_threshold`).
- `agents/kokoro/judge.py` — `KokoroJudgeAgent` renders the `Verdict`; executes at most one action, only when `kokoro_execute_actions=True`, through the existing security gates.
- `agents/subgraphs/kokoro/` — `load_souls → deliberate → judge → (act?) → output`; exports `build_kokoro_subgraph()` + `register_kokoro()`. Intent routing: `intent_router.match_intent()` returns `kokoro` for deliberation intents ("deliberate on…", "weigh the perspectives", or any mention of *kokoro*).

All Kokoro state lives under `state["metadata"]["kokoro"]` (e.g. `["verdict"]`). Settings: `kokoro_souls`, `kokoro_max_rounds`, `kokoro_agreement_threshold`, `kokoro_execute_actions`, `kokoro_judge_model`.

### Skynet swarm supervisor (Fase S — `specs/skynet-swarm/`, implemented, opt-in)

A swarm map-reduce layer over agents: a meta-supervisor decomposes one goal into N independent sub-orders, fans out a dynamically-sized worker swarm (LangGraph `Send`), reduces their outputs into one answer, and re-plans unmet orders until done — bounded, observable, audited. Gated by `settings.skynet_enabled` (default `False`) — flag off ⇒ supervisor graph byte-for-byte unchanged (snapshot-tested). User guide: `docs/skynet.md`; example: `examples/skynet_swarm.py`.

- `agents/skynet/types.py` — frozen value objects: `SwarmOrder`, `SwarmPlan` (`.size`, `deferred` overflow set), `WorkerResult`, `SwarmResult`.
- `agents/skynet/supervisor.py` — `SkynetSupervisor` owns sizing + control loop: `plan()` decomposes a goal into N sub-orders (dynamic by default; fixed-K via `skynet_swarm_size`), hard-caps N at `min(skynet_max_swarm, parallel_max_workers)` and **defers** the overflow (never drops it); re-plans unmet orders deterministically (attempt+1, no LLM call). `evaluate()` returns `(complete, answer)`. Goal + worker outputs reach the model only via `SecurePromptBuilder`; audit is hash-first (`skynet_plan` / `skynet_evaluate`).
- `agents/skynet/worker.py` — `SwarmWorker` (resolves tools via `ToolProviderPort`; actions pass the security gates).
- `agents/skynet/reduce.py::reduce_results()` — `synthesis | concat | first_success` (`skynet_reduce_strategy`).
- `agents/subgraphs/skynet/` — `skynet_plan ──[one Send per order, ≤ cap]──► worker ⇉ … → reduce → evaluate` with a bounded re-plan conditional edge (`skynet_max_rounds`); exports `build_skynet_subgraph()` + `register_skynet()`. Intent routing: `match_intent()` returns `skynet` for swarm intents ("fan this out", "run a swarm over…", or any mention of *skynet*).

`Settings._validate_skynet()` clamps `skynet_max_swarm` to `parallel_max_workers` and rejects a fixed `skynet_swarm_size` above the effective cap. All Skynet state lives under `state["metadata"]["skynet"]` (e.g. `["result"]` → `SwarmResult`). Settings also: `skynet_worker_model`, `skynet_planner_model`, `skynet_token_budget`.

### Vector store port (Fase Z — `specs/vector-store-port/`, implemented)

Vector search is inverted as a hexagonal port (mirror of Fase Y): RAG patterns and the memory layer depend on `VectorStorePort` and never construct a backend — `VectorStoreFactory.create(settings, collection_name)` selects the adapter from `settings.vector_store_backend`. **Chroma stays the default** (zero breakage, base install); alternatives are opt-in via extras. Additive and backward-compatible. User guide: `docs/vector-stores.md`; example: `examples/vector_store_lancedb.py`.

- `agents/extension/ports.py::VectorStorePort` — `@runtime_checkable Protocol` (`collection_name`, `add_documents`, `similarity_search`, `delete_by_source`, `delete_collection`); re-exported from `agents/extension/__init__.py`. **Score contract (SPEC-VS-002):** `similarity_search` returns `(Document, score)` with `score ∈ [0, 1]`, higher = more relevant — the port *defines* it, each adapter *normalizes* its native metric.
- `rag/stores/` — adapters: `chroma.py` (default, **moved** from `rag/vector_store.py`, which stays a re-export shim), `lancedb.py`, `sqlite_vec.py` (both embedded, no server), `qdrant.py` (embedded/server), `pgvector.py` (server). Backend SDK imports are **deferred** inside each adapter; absent extra → `VectorStoreBackendUnavailable`. Score normalization lives in `rag/stores/_normalize.py`.
- `rag/vector_store_factory.py` — `VectorStoreFactory` (mirror of `EmbeddingsFactory`) + `FakeVectorStore` (deterministic, I/O-free test double).
- `core/config.py` — `vector_store_backend` (default `chroma`), `vector_store_path` (embedded), `vector_store_url` + `vector_store_api_key`/`user`/`password` (server). `chroma_path` is kept as a backward-compatible alias via `Settings.resolve_vector_store_path()`.
- `core/exceptions.py` — `VectorStoreError` (generalizes `ChromaStoreError`, which now subclasses it) + `VectorStoreBackendUnavailable`.
- Consumers (`rag/engine`, `hyde`, `fusion`, `self_rag`, `hybrid`, `hierarchical`, `multi_vector`, `multimodal`, `crag`, `memory/long_term`, `memory/mongodb_store`) type against `VectorStorePort` and build defaults via the factory. Extras: `[lancedb]`, `[sqlite-vec]`, `[qdrant]`, `[pgvector]`.

### Runtime composition root (Fase R — `specs/composition-root/`, implemented)

A single composition *facade* that assembles every core port from `settings` plus an optional tenant (`org_id`): tool provider (Y), vector store (Z), embeddings, checkpointer, audit. The host (`prismal-server`) calls `build_runtime()` once in its lifespan and gets a `RuntimeContext` grouping the ports with a coordinated teardown. **Additive and opt-in** — code using `set_tool_provider`/`VectorStoreFactory` directly is unaffected. Guiding principle: **orchestrate, do not reimplement** (it reuses the Y/Z builders + existing factories). User guide: `docs/composition-root.md`; example: `examples/composition_root.py`.

- `prismal/composition/` — package. `__init__.py` is a **thin re-export**; the logic lives in `runtime.py` (so it is covered — the repo omits `*/__init__.py` from coverage).
- `composition/runtime.py` — `build_runtime(settings=None, *, org_id=None, overrides=None, mode=None, collection_base="default", mcp_config_path=None)` composes the 5 ports reusing `build_default_tool_provider` (Y), `VectorStoreFactory` (Z), `EmbeddingsFactory`, `build_checkpointer`, `AuditLogger`. On any sub-port failure it tears down what was created and raises `RuntimeCompositionError`. Also `RuntimeContext` (groups ports + `org_id`; idempotent `aclose()` disconnects MCP / closes checkpointer / releases built stores; async context manager), `RuntimeConfig` (frozen resolved view; sensitive fields stay referenced from `settings`), `VectorStoreProvider`, and `build_test_runtime(...)` (deterministic fakes, no I/O, no-op `aclose`).
- `composition/config_sources.py` — pure, side-effect-free loaders the dashboard reads too: `load_mcp_config`, `resolve_skills_source`, `resolve_vector_store`, `apply_org_overrides`, `collection_for`.
- **Two modes** via `settings.runtime_mode` (`core/config.py`): `global` injects the tool-provider singleton (`set_tool_provider`); `context` keeps every port in the `RuntimeContext` (no global state). Backward-compat: derived from `tool_provider_mode` when unset (`_derive_runtime_mode` before-validator).
- **Tenant resolution** — `collection_for(base, org_id)` = `f"{base}_{org_id}"`, applied identically to RAG and memory; parallel tenants stay isolated.
- `agents/extension/ports.py` — adds `VectorStoreProviderPort` (`get_store(collection_name=None) -> VectorStorePort`); `core/exceptions.py` — `RuntimeCompositionError(port, cause)`.

**Implementation note (deviation from the SPEC text):** Fase Z ships a *factory*, not a process singleton or a graph-bound provider, so the vector store is **always** carried in the `RuntimeContext` via `VectorStoreProvider` — there is no `set_vector_store_provider` global to inject in global mode (the SPEC assumed a Z singleton + `get_async_compiled_graph(vector_store_provider=...)`). Consumers resolve tenant stores with `ctx.vector_store_provider.get_store(...)`; the graph signature is unchanged.

### Config source injection (Fase W — `specs/config-source-injection/`, implemented)

Configuration is inverted as a hexagonal port (mirror of Y/Z): the core stops *reading* `.env`/`os.environ` and instead *consumes* an injected `ConfigSourcePort` that *supplies* raw values; `Settings` keeps its schema and only validates. **Additive and opt-in** — with no source injected the default `EnvConfigSource` reproduces today's behaviour byte-for-byte, so the ~151 `get_settings()` call sites are untouched. User guide: `docs/configuration.md`; examples: `examples/config_source_{env,custom}.py`.

- `core/config_source.py` — `ConfigSourcePort` (`@runtime_checkable` Protocol, sync `load() -> Mapping[str, str|SecretStr]`, must not raise) + sources: `EnvConfigSource` (the **only** core reader of `os.environ`/`.env` for `Settings` — `MaintenanceSettings` still declares `env_file=".env"` on its own; folds in the legacy `LIGHTAGENT_` mirror **into the returned mapping**, no global mutation; honours unprefixed provider keys), `MappingConfigSource`, `ChainedConfigSource` (first-wins, sub-error skipped), `FakeConfigSource`. Global registry `set_config_source()`/`get_config_source()` (invalidates the `get_settings` cache).
- `core/config.py` — `env_file` dropped from `Settings.model_config`; `settings_customise_sources()` adapts the injected port via a `_ConfigSourceSettingsSource(EnvSettingsSource)` subclass (preserves prefix/`AliasChoices`/JSON-list decoding); init kwargs still win. `build_settings(source=None)` (pure, per-tenant; uses a `ContextVar` for isolation), `get_settings()` delegates behind `@lru_cache`, `reload_settings()`. New fields `tavily_api_key`, `config_source_strict`.
- `core/env_compat.py` — the legacy `LIGHTAGENT_` mirror moved into `EnvConfigSource`; `apply_legacy_env_aliases()` is a deprecated **no-op** shim and is **no longer called at import** (`core/__init__.py` import-time side effect removed → importing `prismal.core` mutates zero `os.environ`).
- `core/exceptions.py` — `ConfigSourceError(source, cause)` (raised by `build_settings` when `config_source_strict` and no source).
- **Relocated reads (W4):** `agents/tools.py` `TAVILY_API_KEY` → `settings.tavily_api_key`; `mcp/connection.py` `token_env` → `resolve_secret(name)` (prefers injected source, falls back to `os.environ`). The single LiteLLM `os.environ.setdefault` write-bridge in `providers/registry.py` stays, fed only from injected `Settings`.
- `composition/config_sources.py` — `apply_org_overrides(settings, org_id, overrides, *, source=None)` threads a per-tenant source via `build_settings(source)` (no global mutation).
- **AST guard** `tests/unit/core/test_no_env_reads.py` forbids new literal config `os.getenv`/`os.environ` reads in `prismal/**` (exempt: `core/config_source.py`, `providers/registry.py`, `skills/` plugins, `mcp/servers/` standalone subprocesses). Test `.env` isolation is now handled by an autouse fixture in `tests/conftest.py` that injects `EnvConfigSource(dotenv_path=None)` + clears LLM-provider env keys (Phase W replaced the old `model_config["env_file"]=None` patch, which is now a no-op).

### Cost & Budget Governance (`specs/cost-budget-governance/`, implemented, opt-in)

The **enforcement** layer atop `prismal/monitoring/` (observation): meter real per-run usage, compare it to a `Budget`, and cut off (soft = degrade, hard = abort) when exceeded. Gated by `settings.budget_enabled` (default `False`) — flag off ⇒ zero extra state and the disabled path is byte-for-byte unchanged (graph snapshot-tested). User guide: `docs/budget.md`; example: `examples/budget_governance.py`. (Internally tracked as *Phase C*; not the same axis as the advanced-architectures *Fase A/B/C*.)

- `budget/types.py` — frozen value objects: `Budget` (per-dimension ceiling; `0` = unlimited on that dimension, mirroring the `skynet_token_budget` convention), `BudgetScope` (`turn | session | tenant`), `TokenCounts`, `Usage` (cumulative, summable with `+`; `estimated` is the OR of its operands), `BudgetStatus`, `Degradation`.
- `budget/usage.py::extract_token_usage()` — reads LangChain `usage_metadata` → OpenAI-style `response_metadata['token_usage']` → zeros. Never raises; these are LangChain conventions (not provider SDK types) so it stays out of `providers/` per Rule #4.
- `providers/cost.py` — `CostEstimate` + `compute_cost_usd()`: prices a call from LiteLLM's native model map first, then the `settings.budget_pricing` fallback table, else a zero-cost `"none"` estimate. **The only new module importing `litellm`** (Rule #4); never raises.
- `budget/meter.py` — `CostMeter` accumulates `Usage` for one run (in-memory, O(1), no hot-path I/O), emits OTel counters tagged for attribution (`agent`/`pattern`/`model`/`tenant`), and — when a `CostTracker` + `session_id` are injected — bridges each call to FinOps history. `record_response()` does extract→price→record. Enforcement reads `meter.usage`; it never depends on the persistence bridge.
- `budget/guard.py` — `BudgetGuard.check()` is a pure O(1) verdict (within / soft / hard, per dimension); `enforce()` adds a hash-first audit record + raises `BudgetExceeded` on a hard cap (only when `hard_cap`); `degradation()` translates status into pattern advice. `make_budget_guard_fn(guard)` adapts a guard to the `budget_guard_fn` the expensive patterns consume (`None` → zero-overhead always-True — the disabled path).
- `budget/resolve.py` — `resolve_budget(settings, *, org_id=None)` builds a `Budget` from settings; `seed_budget_run` / `maybe_seed_budget_run` install the per-run `{meter, guard}` in an **in-process registry keyed by `session_id`** — never in checkpointed state, so a live `CostMeter` (which may hold a SQLite `CostTracker`) never reaches the serializer (same principle as the path-based media descriptors). Seeding is idempotent per user-turn signature, so usage accumulates within a turn and resets between turns (turn-scope default). `get_budget_guard` / `clear_budget_run` read/release it. State carries only the serializable marker `state["metadata"]["budget"] = {"enabled": True, "scope": ...}`.
- **Integration**: `react_loop(..., budget_guard=None)` records usage after each call and checks before the next (hard → partial answer + break); the per-run guard is wired from `state["metadata"]["budget"]` at the node seam. The expensive patterns honour `budget_guard_fn`: `debate_round`, `tree_of_thoughts`, `LATSAgent.search`, `MixtureOfAgents.generate`, `reflection_loop`. Skynet's supervisor/worker build `Budget(max_tokens=skynet_token_budget)` over a shared `CostMeter` and raise `SkynetBudgetExceeded` (now `SkynetBudgetExceeded(BudgetExceeded, SkynetError)`) on breach — unifying the legacy token budget with this layer.
- Settings (`core/config.py`): `budget_enabled`, `budget_max_tokens`, `budget_max_cost_usd`, `budget_max_calls`, `budget_max_wall_clock_s`, `budget_scope`, `budget_soft_ratio`, `budget_hard_cap`, `budget_pricing`, `budget_alert_usd`; `_validate_budget` rejects an unknown `budget_scope` at load time. `core/exceptions.py` — `BudgetExceeded`. `monitoring/otel.py` — counters `prismal.budget_tokens_total`, `prismal.budget_cost_usd_total`, `prismal.budget_cutoffs_total` + histogram `prismal.cost_per_call_usd`.

### A2A interop (Phase I — `specs/a2a-interop/`, implemented, opt-in)

Bidirectional **A2A** (Agent2Agent) interoperability in `prismal/a2a/` (`[a2a]` extra; HTTP/SSE imports deferred). Gated by `settings.a2a_enabled` (default `False`) — flag off ⇒ the compiled supervisor graph is byte-for-byte unchanged (A2A adds no supervisor route). A2A is the *agent*-level complement to MCP (*tools*): `prismal/mcp/` stays for tools, `prismal/a2a/` is added for agents. All remote content crosses a trust boundary → it is L1-sanitized (`InputSanitizer`) before touching `AgentState`, and every in/out task is audited without content. Docs: `docs/a2a.md`; examples: `examples/a2a_server.py`, `examples/a2a_remote_node.py`.

- `a2a/types.py` — A2A v0.3.x Pydantic models (`AgentCard`, `AgentSkill`, `A2ATask`, `A2AMessage`, `A2AArtifact`, `A2APart`, `A2AAuth`). camelCase wire aliases (`protocolVersion`, `inputModes`, `messageId`, …) via `alias_generator=to_camel` + `populate_by_name`; serialize with `model_dump(by_alias=True)`. `A2AAuth` secrets are `SecretStr`.
- `a2a/card.py` — `build_agent_card(settings, registry, *, org_id, did)` derives one `AgentSkill` per published capability (`a2a_published_skills` allowlist; empty = all registry entries) from a capability map like `DEFAULT_CAPABILITY_MAP`; tenant-scopes the URL; embeds the DID (`did:web` from the Phase IDN identity settings or an explicit override); adds media output modes when `multimodal_enabled`; cached per (settings fingerprint, org) — `clear_agent_card_cache()`.
- `a2a/server.py` — **inbound** `A2AServerHandler(graph, *, settings, audit, sanitizer)`: `handle_rpc` (`message/send`/`tasks/get`/`tasks/cancel`, JSON-RPC) + `stream_rpc` (SSE `data:` lines). `_run_task` sanitizes → invokes the graph with `thread_id=task_id` → yields `A2AArtifact` → audits `a2a.inbound`. `AuthContext` gate: strict mode rejects unauthenticated calls (`-32001`). The HTTP routes are mounted by the host (`prismal-server`).
- `a2a/client.py` — **outbound** `A2AClient` (discover card, `send_task` via JSON-RPC `message/send` + SSE, `cancel`, bearer/`oauth2_client_credentials` auth with token caching), `A2AConnectionManager` (fnmatch allowlist + deny-all-in-strict + client pool; mirror of `mcp/connection.py`), and `A2AAgentNode.as_node()` — wraps a remote agent as a `@prismal_node` graph node (the A2A analogue of `LangChainRunnableAdapter`); a remote failure yields a graceful `metadata.a2a.error` update instead of aborting the graph.
- `a2a/provider.py` — `A2AToolProvider` conforms to the Phase Y `ToolProviderPort`: surfaces remote skills as `a2a__{agent}__{skill}` tools (composable in `CompositeToolProvider`); sync `get_tools` never raises and filters by capabilities; async `prepare()` discovers URL-only agents.
- Settings (`core/config.py`): `a2a_enabled`, `a2a_inbound_enabled`, `a2a_outbound_enabled`, `a2a_base_url`, `a2a_published_skills`, `a2a_outbound_allowlist`, `a2a_strict`. Exceptions (`core/exceptions.py`): `A2AError`, `A2AAgentUnavailable`. Composition root (`composition/runtime.py`): `build_runtime(..., graph=, a2a_agents=)` composes `A2AToolProvider` into the tool provider (outbound) and exposes an `A2AServerHandler` on `RuntimeContext.a2a_handler` (inbound) when `a2a_enabled`; Agent Card per `org_id`.

### Token Efficiency (Phase TKE — `specs/token-efficiency/`, implemented, opt-in)

Measure first, then cut tokens. Gated by `settings.token_efficiency_enabled` (default `False`); every sub-flag is effective only with the master on, and with it off the graph, tool lists, `SecurePromptBuilder` output, `ChatLiteLLM` construction and `Usage` numbers equal v3.12.0 (snapshot + contract tests). User guide: `docs/token-efficiency.md`; example: `examples/token_efficiency.py`; TKE0 facts: `specs/token-efficiency/VERIFY.md`.

- **Accounting (TKE1)** — `budget/types.py` cache/reasoning fields + `cost_source`; `budget/usage.py` reads LangChain `input_token_details`/`output_token_details` then raw provider shapes; `providers/cost.py` precedence per ADR-WAVE-001 (gateway → litellm cache-aware → table → none); `CostMeter` normalizes + kind-splits only with the master on. `eval/metrics_tokens.py` (`TokenReport`, baseline, `check_token_regression`) + `python -m prismal.eval token-gate`.
- **Prompt caching (TKE2)** — `providers/prompt_cache.py`: `detect_cache_family`, `CachePolicy` (`config/prompt_cache.yaml`), `apply_cache_policy` (never raises), and `CacheAwareChatModel` (outer composition wrapper; also applies per-agent effort, TKE6). `security/prompt_builder.py` `PromptLayout.CACHE_STABLE`: HMAC prefix canary in the system prompt, per-request canary in a tail `<turn_context>`; `detect_leak` / `canaries` cover both — **any change must keep red-team leak parity** (`tests/unit/security/test_canary_leak_cache_stable.py`).
- **Tools (TKE3/TKE5)** — `agents/tool_gate.py` + `CompositeToolProvider(tool_gating_mode="mask")`/`allowed_tool_names()`; enforced in `react_loop(tool_allow_set=...)` and `ActionInterceptor.set_tool_allow_set()`; `tool_gate_react_kwargs()` fails **closed**. `agents/tool_search.py` (BM25, `DeferredToolSet`, `plan_deferred_tools`, native `defer_loading`). `agents/tool_output_budget.py` applied in `react_loop` and `tool_output_budget_middleware` (just outside `hardening_middleware`, i.e. after sanitize/taint).
- **Context (TKE4/TKE7/TKE8)** — `agents/artifacts.py` (`ArtifactStorePort`, tenant+run scoped, `read_artifact`); `CompactionStrategy.MASK_OBSERVATIONS` + high/low-water hysteresis in `agents/context_compaction.py`; `agents/handoff.py` envelopes; `rag/context_budget.py` (+ `providers/{rerank,compression}.py`, extras `[rerank]`/`[compression]`).

### LLM Gateway Providers (Phase GW — `specs/llm-gateway-providers/`, implemented, opt-in)

Route LLM calls through gateways with `gw:<gateway>/<model>[:<variant>]` refs (explicit model > `llm_gateway_agent_map[agent]` > legacy). Gated by `settings.llm_gateways_enabled` (default `False`) plus a per-gateway `*_enabled`; flag off ⇒ graph and `get_llm()` kwargs equal 3.13.0 (snapshot + `tests/unit/providers/test_registry_contract.py`, the shared ADR-WAVE-001 contract test — extend it, never replace it). Guide: `docs/gateways.md`; GW0 facts: `specs/llm-gateway-providers/VERIFY.md`.

- `providers/gateways/` — `base.py` (`GatewayPort`, `ResolvedModel` with `SecretStr` key `repr=False`, `GatewayCallMeta`), `policy.py` (`parse_model_ref`), `openai_compatible.py` (generic base adapter; `_deny()` = count + `GatewayPolicyError`), `openrouter.py`, `opencode.py` (+ `config/opencode_models.yaml`), `omniroute.py`, `quota.py` (`QuotaLedger`), `router.py` (`GatewayRouter`, `build_gateway_router`, `get/set/use_gateway_router`, `vault_secret_resolver`), `chat_model.py` (`GatewayChatLiteLLM`), `reconcile.py` (`CostReconciler`), `poller.py` (`GatewayQuotaPoller`).
- **Invariants:** `resolve()` is pure (no I/O); policy/ToS/security guards fail **closed** before any request (incl. a crashing startup gate and OmniRoute before `astartup()`); lookups fail **open**; keys only unwrapped at the `GatewayChatLiteLLM` constructor, never in `os.environ`/logs/URLs/`repr`; OpenRouter always sends `provider.data_collection="deny"` + `zdr=true` by default; no double fallback (gateway-owned fallback ⇒ `max_retries=0`, unwrapped).
- `GatewayChatLiteLLM` captures served model / generation id / billed cost at `(a)completion_with_retry` + `_create_chat_result` (streaming bypasses `_create_chat_result` — VERIFY.md GW0-03) into `response_metadata["prismal_gateway"]`; `CostMeter` reads it (`subscription` ⇒ `cost_usd=0` + `equivalent_usd`), records the ledger, schedules reconciliation; `BudgetGuard.degradation()` reports `gateway_quota:<window>` after a downgrade.
- `build_runtime(..., gateway_vault=)` composes `RuntimeContext.gateways` with tenant keys `<auth_ref>@<org_id>` (no global fallback unless `llm_gateway_tenant_key_fallback`), runs `astartup()`, owns reconciler/poller lifecycles.

### Typed Decision Models (Phase DEC — `specs/typed-decision-models/`, implemented, opt-in)

Closed questions (Choice / Score / Noul) answered by a cascade **rule → Ollama (`ollama:fast` → `ollama:accurate`) → Jev → LLM** (Laya optional) behind `DecisionPort`; a decision *proposes*, deterministic code *authorizes* (DD-DEC-001). Gated by `settings.decisions_enabled` (default `False`) plus one `decision_<integration>_enabled` flag each; enabled ⇒ `shadow` unless `decision_modes` says `enforce`. Flags off ⇒ graph and node outputs equal 3.14.0 (snapshot-tested). Guide + DEC0 verified facts: `docs/decisions.md`; example: `examples/typed_decisions.py`.

- `prismal/decisions/` — `types.py` (`QuestionSpec`, `Decision`, `GatePolicy`), `ports.py` (`DecisionPort`, `guarded_decide` — never raises), `state.py` (`DecisionStateBuilder`: the **only** way untrusted text reaches a decision model — sanitized, fenced, EN/ES authority-claim/injection flags taint the state), `validation.py` (forbidden-use lint at registration), `gate.py` (τ per tier, margin, taint, derangement shuffle for HIGH/CRITICAL Choice), `cascade.py`, `calibration.py` (temperature/isotonic, model-pinned `CalibrationStore`; `enforce` without a valid record fails `Settings` load), `shadow.py` (`ShadowRunner`, `promotion_ready`, drift demotion), `audit.py` (hash-only `decision.made`), `runtime.py` (`get/set/use_decision_runtime`), `adapters/` (systemone HTTP, **Ollama** — `ollama.py`: sub-tiers, preflight + `name@digest12`, limits, tier routing, own `budget_ms` — Laya local, Jev SDK, LLM fallback, fake), `integrations/` (route, eval_judge, hitl, tool, rag, ctx, swarm, safety), `cli.py` (`python -m prismal.decisions calibrate|bench`).
- **Invariants:** failure is escalation, never "allow" (incl. the terminal LLM tier for tainted / over-limit states); CRITICAL never auto-accepted by a non-LLM tier; HITL `auto` only from a calibrated typed decision, irreversible/critical ⇒ `human`; DEC-SAFE is escalate-only. `laya` / `typesafe_sdk` imports live only in `decisions/adapters/` (deferred; extras `[laya]`, `[jev]`); `security/**` (except `security/decision_bridge.py`), `budget/**`, `scheduler/**`, `identity/policy.py` never import `prismal.decisions` (AST test). Extend the `CASES` of `tests/unit/decisions/test_port_contract.py` for every new adapter; the injection corpus `tests/security/decisions/test_injection_corpus.py` is append-only.
- **Ollama delta (3.19.0, `DELTA-2026-10-ollama-backend.md`, PHASE DEC7):** `"ollama"` expands to `ollama:fast` → `ollama:accurate`; each sub-tier has its own `budget_ms` (`decision_ollama_timeout_ms`) that the cascade never charges to `decision_timeout_ms`; calibration keys are `<id>@<name>@<digest12>` (stale digest ⇒ uncalibrated + metric); a non-loopback Ollama is refused unless `decision_ollama_allow_remote` (the factory re-checks it — tenant overrides skip validators). **HITL is one-way:** never ask a model an abstract "needs a human?" question; review = deterministic OR of `rule_flags` and five one-fact questions; any tier's raise is sticky (`cascade.decide(observe=)`), `ollama:fast`/`llm` negatives give at most `confirm`; a model never compares amounts (trusted `GatedAction.amount` vs `decision_hitl_money_limit`). Live contract: `PRISMAL_OLLAMA_CONTRACT=1 pytest -m ollama tests/live/decisions/test_ollama_contract.py`.
- In-graph wiring: `supervisor_node` (after `match_intent`), `CRAGPipeline` grading, `GuardrailsEngine.validate_input`. `build_runtime(..., decision_vault=)` composes `RuntimeContext.decisions`; in `context` mode bind it with `use_decision_runtime()` — nothing is built implicitly there.

### Adaptive Model Routing (Phase AMR — `specs/adaptive-model-routing/`, implemented, opt-in)

A `RoutingPolicyPort` chooses the model **string** per agent call (ADR-WAVE-001 pipeline step 1 — AMR never wraps the model). Gated by `settings.adaptive_routing_enabled` (default `False`) and `routing_mode` (`shadow` default ⇒ choice recorded, 3.15.0 model used). Flag off ⇒ `get_llm()` kwargs and graph equal 3.15.0 (registry contract + snapshot tests). Guide + AMR0 verified facts: `docs/routing.md`; example: `examples/adaptive_routing.py`.

- `prismal/routing/` — `types.py` (`ModelTier`, `TIER_ORDER`; `auto` terminal), `ports.py`, `policies/` (`static` + `config/routing_tiers.yaml` parity map, `budget_aware`, `cascade` + `AdequacyChecker` (DEC Noul via `DecisionStateBuilder`), `composite`, `batch_assignment` (dual + greedy, never raises)), `predictor.py`, `features.py` (deterministic, LLM-free), `select.py` (per-run registry keyed by session_id; affinity; `metadata["routing"]`), `outcome.py` (agent seams `llm_route_kwargs` / `track_outcome`; `react_loop` calls `note_tool_error`), `fanout.py` (Skynet/MoA `assign_fanout`, `build_batch_policy`), `plan_cache.py`, `response_cache.py`, `batch.py` (`BatchExecutor`).
- **Invariants:** policies fail **open** (static `fallback:<err>`); security floors fail **closed** (risk floor, no `auto` for critical, tainted never below static, startup refusal without a `critical` floor); explicit `model=` beats routing; under a cascade a `gw:` call has no `models[]` and no `fallback_model`; `routing/` imports no provider SDK (AST test) — provider batch APIs live only in `providers/batch_adapters.py`; tainted / HITL-gated requests are never batched.

### Protocol upgrades + host read surface (Phase PRT + HRS — `specs/protocol-upgrades-2026/`, `specs/composition-root/DELTA-2026-10-host-read-surface.md`, implemented, opt-in)

A2A v0.3 + v1.0 dual-stack, MCP 2026-07-28 stateless + tasks extension, AG-UI 1.0 translator, a 12-key usage wire and a read-only host surface. All switches default off ⇒ graph, v0.3 Agent Card and MCP request sequence equal 3.16.0 (snapshot-tested). **Wire names are pinned in `docs/adr/ADR-PRT-001-protocol-pins.md`, which overrides the SPEC's [verify] names.** Guide: `docs/protocols-2026.md`; example: `examples/agui_translate.py`.

- **A2A** (`prismal/a2a/`): `v1/{constants,types}.py`, `negotiation.py` (`A2A-Version` header ⇒ method family ⇒ header-less **0.3**; unsupported ⇒ -32009), `convert.py`, `card.py` (`build_agent_card(protocol_version=)`, `a2a_route_paths`), `task_store.py`, `oauth.py`, `client.py`; `A2AServerHandler.handle_rpc(headers=None)` keeps 3.16.0 behaviour. Settings `a2a_protocol_version`, `a2a_accept_legacy`, `a2a_tasks_list_max_page_size`, `a2a_inbound_auth_schemes`, `a2a_oauth_redirect_uri`.
- **MCP** (`prismal/mcp/`): `constants.py`, `protocol.py` (`LegacySessionAdapter`, `StatelessHttpAdapter`, `select_adapter`), `tool_cache.py`, `tasks.py`, `input_requests.py`; selection on connect in `MCPServerConnection` from `mcp_protocol_mode` (`legacy` default). The `mcp` SDK stays at **1.28.1** (no 2026-07-28 support) — the stateless wire is in-house on `httpx`; `build_default_tool_provider` forwards settings to `MCPClientManager` only when a PRT MCP flag is on.
- **AG-UI** (`prismal/events/agui.py`): pure translator (no HTTP — AST guard `tests/unit/events/test_no_http_imports.py`, exemption `webhook_dispatcher.py`); never raises. No `USAGE`/`INTERRUPT` events in AG-UI 1.0: usage ⇒ `RUN_FINISHED.usage[]` + `CUSTOM prismal.usage`, interrupts ⇒ `RUN_FINISHED.outcome`. Regenerate the 12 golden transcripts with `uv run python -m tests.unit.events._transcripts --write`.
- **Usage wire**: `RunSummary.to_usage_wire()` + `prismal.monitoring.get_run_summary()`; contract fixture `tests/fixtures/protocols/usage_wire.json` is shared with prismal-server / SDK — change both sides together.
- **Host read surface** (`composition/host_surface.py`, `monitoring/host_stats.py`): `RuntimeContext.budget_status / quota_windows / audit_page / verify_audit_chain / decisions_report / routing_report / a2a_routes` (fail-open, `host_surface_errors_total`; `audit_page` shows only allowlisted scalars — other strings and nested values are hashed — and scans at most 100k records); hosts wrap graph runs in `ctx.bind_org()` so tenant-scoped reports see their samples; `build_runtime(config_source=)`. Signatures are frozen for 3.x by `tests/unit/composition/test_host_surface_signatures.py` — update it deliberately.
- **Invariants:** A2A tenant/principal mismatch (incl. a reused task id) ⇒ TaskNotFound (v1 -32001, v0.3 -32004) + audit, never an overwrite — ownership is reserved under a lock before the graph runs and scoped runs use a per-owner graph thread id; OAuth refresh tokens are keyed by token origin + agent URL in the **local** org's vault partition and bound to their `token_url`; MCP task results only when the tasks extension was declared. Conformance suites run only under the opt-in `interop` marker (`PRISMAL_INTEROP=1`).

### Agentic memory v2 (Phase MEM — `specs/agentic-memory/`, implemented, opt-in)

Typed, decision-gated, budgeted, tenant-scoped memory in `prismal/memory/v2/`, beside (never replacing) the legacy `memory/long_term.py` / `long_term_store.py`, which are wrapped, never edited. Gated by `settings.memory_v2_enabled` (default `False`; `memory_mode` `shadow` first) — flag off ⇒ graph unchanged (`tests/unit/agents/test_graph_snapshot_memory.py`), `RuntimeContext.memory is None`, no scheduler job. Guide + MEM0 verified facts + legacy signatures: `docs/memory.md`; example: `examples/agentic_memory.py`.

- `types.py` / `ports.py` (`MemoryPort`, `MemoryBackendPort`, `MemoryGraphPort`) · `service.py` (`MemoryService` = one tenant; `build_memory_service(settings, org_id=, backend=, graph=, decisions=, summarizer=)`) · `adapters/` (`LongTermMemoryBackend` — `mv2_*` side table `memory_v2_meta`; `LangGraphStoreBackend` — namespace `("prismal", org, user|"_", type)`; `InMemoryBackend`) · `write_gate.py` + `taint.py` (T1–T7) · `recall.py` + `render.py` · `consolidation.py` (`register_consolidation_job(..., consolidator_for=)`) · `forget.py` · `playbooks.py` (`PlaybookStore`, `PlaybookCurator`) · `graph/{sqlite,graphiti}.py` (`[graph-memory]` extra, client always injected) · `facades.py` + `migration.py` · `decision_bridge.py` (`CascadeDecisionPort`: the DEC cascade as the controllers' `DecisionPort`, integration `memory`, every state field re-added untrusted) · eval `prismal/eval/memory_locomo.py` (synthetic LoCoMo-style; LoCoMo is CC BY-NC — never commit it).
- `build_runtime(org_id=, memory_backend=, memory_graph=)` composes `RuntimeContext.memory`; enabled without `org_id` ⇒ `RuntimeCompositionError("memory")`.
- Agent adoption: `memory/v2/agent_prompt.py::memory_turn_message()` (pilot: `researcher`) appends one tail `<turn_context>` message after the history — only with `memory_mode="enforce"` + the `cache_stable` layout + a bound service (`ctx.bind_org()` binds `ctx.memory`; else `use_memory_service`/`set_memory_service`).
- **Invariants:** a `MemoryService` serves exactly one `org_id` (other scopes ⇒ `MemoryTenancyError`, counted + hash-audited); derived records keep the exact source scope (no widening); tainted candidates are stored only `EPISODIC` and never supersede/promote; untrusted text reaches decisions only via `DecisionStateBuilder.add_untrusted` and prompts only via `memory_block_for_prompt` (tail `<turn_context>`, never the cached prefix); **only `memory/v2/forget.py` may call `.purge(`** (AST guard `tests/unit/memory/v2/test_import_guards.py`, which also bans graph SDK imports outside `memory/v2/graph/`); forget accepts only `rule` (selectors issued by `retention_rules()`, single use) or `human` (approved, single-use, equal-selector request); consolidation supersedes, never deletes.
- Test gotcha: `tests/unit/agents/conftest.py` and `tests/integration/conftest.py` replace `apscheduler` (and more) with `MagicMock` in `sys.modules` — patch module seams (`scheduler.executor._crontab_trigger`), never `apscheduler`, and never run `tests/integration` in the same pytest session as `tests/unit`.

### Security (5-layer defense-in-depth)

All layers live in `prismal/security/` and are re-exported from its `__init__.py`:

- **L1 `InputSanitizer`** — strip control chars, normalize unicode, enforce `MAX_INPUT_LENGTH`.
- **L2 `GuardrailsEngine`** — regex + risk scoring; `nemo_rails.py` integrates NVIDIA NeMo Guardrails (L3).
- **L4 `ActionInterceptor`** — LangChain callback, pre-tool permission checks. Call `ActionInterceptor.check()` before any tool that writes files or executes code.
- **L5 `AuditLogger`** — append-only JSONL audit log with xxhash chaining.
- **`SecurePromptBuilder`** — isolates user input with canary tokens. All prompts built from user input MUST go through this; never f-string user data into a prompt template.
- **`PermissionManager`** — TTL-based SQLite permission grants.
- **`filesystem_guard.py`** — path confinement via `resolve().is_relative_to()` (Phase 31).

### Provider isolation

All LLM calls go through `prismal/providers/` (LiteLLM wrapper + per-provider configs). Provider-specific imports (`anthropic`, `openai`, `google.generativeai`, `ollama`, etc.) must live only inside this package — never import them from agents, memory, RAG, or elsewhere.

### Other subsystems (one-liners)

- `core/` — Pydantic Settings config (`get_settings()`), logging (`get_logger()`), exceptions, `database.py` helpers, `UserStore` (plain `sqlite3`). Every setting is listed in `docs/settings-reference.md` (generated — re-run `scripts/gen_settings_docs.py` after adding/changing a field; `tests/unit/core/test_settings_docs_sync.py` fails otherwise).
- `memory/` — short-term conversation history + long-term PII-sanitized store (SQLite + ChromaDB; optional MongoDB via `[mongodb]`).
- `mcp/` — Model Context Protocol client, adapter, connection manager.
- `rag/` — RAG engine, CRAG pipeline, interchangeable vector store (`VectorStorePort` + `rag/stores/` adapters, default Chroma; Fase Z), document loaders, embeddings, federated search.
- `scheduler/` — APScheduler-based `CronExecutor`, `DateTimeService` (single time-of-truth, timezone-aware), Prefect flows.
- `sandbox/` — `SandboxExecutor` (Phase 43): `run_code` can use docker/podman/nsjail/bwrap/firejail isolation backends (Python only, off by default); `run_command` runs a time-limited host subprocess. AST denylist in `codeact_agent.py`. See `docs/sandbox.md`.
- `events/` — AG-UI 1.0 translator, file watcher and webhook dispatcher adapters (not a pub/sub bus). See `docs/events.md`.
- `scheduler/`, `skills/`, `mcp/`, `providers/`, `data/` — user guides in `docs/scheduler.md`, `docs/skills.md`, `docs/mcp.md`, `docs/providers.md`, `docs/data-utilities.md`; the docs index is `docs/README.md`.
- `monitoring/` — Langfuse traces, OpenTelemetry spans, structlog.
- `data/` — DuckDB + Polars utilities.
- `agents/visualization.py` — `to_mermaid()`/`to_mermaid_png()`/`visualize()`/`save_graph_image()` for any graph-based architecture (compiled graph, `SubgraphDefinition`, `PrismalStateGraphBuilder`); re-exported from `prismal.langgraph`. `SubgraphDefinition` gains `.to_mermaid()`/`.visualize()`/`.save_image()`; `agents.graph.visualize_supervisor_graph()` renders the main graph. `subgraphs/factory.py::assemble_state_graph()` is the shared sync topology builder. Non-graph architectures (patterns, modal agents) raise `TypeError`.
- `skills/` — `available/` (source, committed) · `active/` (runtime-enabled, gitignored) · `custom/` (AI-generated, gitignored).
- `souls/` — Kokoro personas (Fase K): `available/{spirit,mind,heart}/SOUL.md` (committed). Loaded via `SoulsManager`; bodies are user content (route through `SecurePromptBuilder`).
- `budget/` — Cost & Budget Governance (Phase C): meter real usage (`CostMeter`) and enforce a `Budget` (`BudgetGuard`) per turn/session/tenant, soft (degrade) / hard (abort); opt-in `settings.budget_enabled`. Per-call pricing in `providers/cost.py`.
- `a2a/` — A2A (Agent2Agent) interop (Phase I): expose prismal as an A2A agent (Agent Card + `A2AServerHandler` JSON-RPC/SSE) and consume remote agents (`A2AClient`/`A2AAgentNode`/`A2AToolProvider`); opt-in `settings.a2a_enabled`, `[a2a]` extra. All remote content L1-sanitized + audited.

## Critical rules

1. **Never** concatenate user input into prompts — use `SecurePromptBuilder`. This applies to STT transcripts, OCR text, image captions, **and Kokoro soul (`SOUL.md`) bodies** as well — they are user-controlled content.
2. **Never** bypass `GuardrailsEngine` / `ActionInterceptor`; they are the gateway.
3. **Always** use `get_async_compiled_graph()` in async contexts (the sync variant uses a non-async SQLite saver).
4. **Never** add provider-specific imports outside `prismal/providers/`. This includes `whisper`, `pyttsx3`, `elevenlabs`, `open_clip_torch`, etc. for the multimodal layer.
5. **Always** call `ActionInterceptor.check()` before tool calls that write files or execute code; call `ActionInterceptor.check_media_op()` before media read/write.
6. **Always** validate incoming media with `MediaValidator.validate()` before passing it to a multimodal agent — `AuditLogger.log_media()` records hash + modality, never content.
7. **Always** run FFmpeg via `SandboxExecutor` in `VideoAgent` and `VideoLoader` — never in-process.
8. **Never** add `__init__.py` to `prismal/` — it must remain a PEP 420 namespace package. (The repo-local `lightagent/__init__.py` shim is a deliberate, temporary migration exception and is not shipped.)
9. **Never** import `prismal.mcp` / `prismal.skills` from `prismal/agents/**` (Fase Y inversion; enforced by `test_no_mcp_skills_imports.py`). The only exemptions are `agents/extension/providers.py` (the adapters) and `agents/skill_manager.py` (the skills-administration agent). Tools reach agents only through the injected `ToolProviderPort` — in tests, inject `FakeToolProvider` instead of patching registry internals.

## Testing notes

- `pytest.ini_options` sets `filterwarnings = ["error", …]`, so new `DeprecationWarning`s from our own code will fail tests. Add specific ignores only for third-party warnings.
- `tests/conftest.py` is minimal; most fixtures live in `tests/integration/conftest.py` and per-tier `conftest.py` files.
- Integration tests under `tests/integration/` expect running services (sandbox backends, databases). They are tagged `@pytest.mark.integration`.
- Ruff's per-file ignores relax rules for `tests/**` and `prismal/skills/{available,custom}/**` — assume the strict rules everywhere else.
- **Graph-snapshot baselines (wave 2026-10):** each phase's "flags off ⇒ byte-for-byte unchanged" snapshot / disabled-contract test baselines against **the last released minor at merge time**, not a version number hard-coded in the spec (phases are built on overlapping branches; release order stays TKE → GW → DEC → AMR → PRT+HRS → MEM).

## Wave 2026-10 conventions (`specs/wave-2026-10-implementation-plan.md`)

- **Shared hot files** — every phase adds: one contiguous `# --- <PHASE> ---` settings block plus one `_validate_<phase>` validator in `core/config.py`; one exception subtree in `core/exceptions.py`; one `_register_<phase>_metrics()` helper in `monitoring/otel.py`. Rebase parallel branches on `develop` before each merge.
- **LLM construction** — `ProviderRegistry.get_llm` signature, wrapper stack (`GatewayChatLiteLLM` innermost, `CacheAwareChatModel` outer, AMR only picks the model string, `with_fallbacks` outermost) and `compute_cost_usd` precedence are fixed by `docs/adr/ADR-WAVE-001-llm-construction-contract.md`. Extend the shared `tests/unit/providers/test_registry_contract.py`; never replace it.
- **Verify before build** — TKE0, GW0 and PRT0 run up front; DEC0, AMR0 and MEM0 run just-in-time at the start of their milestone (volatile vendor facts). Record outcomes where each spec says (`VERIFY.md`, ADR, or `docs/<feature>.md#verified-facts`).
