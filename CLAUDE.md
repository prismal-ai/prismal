# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Package context

`prismal` is the **agent framework layer** extracted from the larger `lightagent` monorepo. It is a standalone, publishable PyPI package containing everything needed to build and run AI agents — no web server, dashboard, or CLI. It was published as `lightagent-agents` through v2.x and **rebranded to `prismal` in v3.0.0** (distribution name plus the `lightagent.*` → `prismal.*` import namespace; see `propuesta.md` / `PLAN_MIGRACION.md`). The sibling app package (still named `lightagent`) historically depended on this one and shared the import namespace — see the namespace note below.

Current version: **3.1.4** (single source of truth: `pyproject.toml`; history in `CHANGELOG.md`; release tag format `prismal/vMAJOR.MINOR.PATCH`). Pushing a release tag triggers both `release.yml` (PyPI + GitHub Release with notes extracted from the CHANGELOG section) and `docker-publish.yml` (container image to GHCR `ghcr.io/prismal-ai/prismal`, tagged `X.Y.Z` + `latest`; manual dispatch publishes `dev`).

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
uv run pytest tests/unit/security/test_sanitizer.py::TestSanitizer::test_strip_controls  # single test
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

The core is a LangGraph `StateGraph[AgentState]` assembled in `prismal/agents/graph.py`. A central `supervisor_node` routes each turn to one of 26 specialist agent nodes (`coder`, `researcher`, `rag_agent`, `data_analyst`, `planner`, `critic`, `codeact_agent`, `cua_agent`, `file_manager`, `skill_manager`, `cron_manager`, `parallel_research`, `meta_learner`, `skill_creator`, `domain_supervisor`, `network_supervisor`, …), which each return control to the supervisor; the supervisor routes to `END` when done.

- **Entry points**: `get_compiled_graph()` (sync) and `get_async_compiled_graph()` (async, LRU-cached). Async contexts must use the async variant — it wires `AsyncSqliteSaver`.
- **State**: `AgentState` is a `TypedDict`. Only `messages` has a custom reducer (`add_messages`); all other fields use plain merge semantics.
- **Routing wrapper**: `_supervisor_router` in `graph.py` exists purely so LangGraph's `get_type_hints()` can resolve `AgentState` — do not remove it.
- **Checkpointing**: `build_checkpointer()` supports SQLite (default) and PostgreSQL (via `[postgres]` extra).
- **Tool cap**: `agents/tool_registry.py` enforces a global `_MAX_TOTAL_TOOLS = 120` (Phase 44).
- **Intent routing**: `agents/intent_router.py::match_intent()` is deterministic regex-based, ahead of LLM supervision.

### Subgraphs and patterns

`agents/subgraphs/` holds composed multi-node pipelines wrapped as reusable subgraphs: `dev_pipeline` (PO → Architect → Developer → Tests → QA → Reviewer), `ml_pipeline` (Ingester → EDA → Features → Trainer → Evaluator → Exporter), `financial` (Collector → Technical → Fundamental → Risk → Report), plus `analysis_orchestrator`, `engineering_orchestrator`, `research_orchestrator`. They are built by `SubgraphFactory` and registered in `SubgraphRegistry`.

**Advanced architectures (Fase A/B/C — `specs/advanced-architectures/`)** adds 7 RAG engines, 7 agent patterns, and 5 subgraph pipelines. All follow the same factory-injection pattern: business logic accepts callables (`generate_fn`, `evaluate_fn`, `reward_fn`, `plan_fn`, `tool_executor`, `linter_fn`, …) so tests run without LLM backends. Defaults wire `ProviderRegistry().get_llm()` lazily.

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

Each subgraph exports both `build_<name>_subgraph()` (returns `SubgraphDefinition`) and `register_<name>()` (idempotent registry install), mirroring the existing `register_ml_pipeline`.

`agents/patterns/` provides composable primitives: `reflection_loop()` (generate → critique → refine) and `make_parallel_dispatcher()` (fan-out via LangGraph `Send()`).

`agents/subgraphs/gates.py::hitl_gate()` uses `interrupt()` for Human-in-the-Loop pauses.

### Multimodal layer (Fase F — `specs/multimodal-agents/`, implemented, opt-in)

A multimodal capability layer covers audio, image, and video on top of the existing text agents. It is **opt-in**: gated by `settings.multimodal_enabled` (default `False`). Without the toggle, the 26 text agents behave identically to today. When `multimodal_enabled=True`, `get_async_compiled_graph()` wires a single `multimodal_pipeline` supervisor route (the subgraph fans out to vision/audio/video internally and emits the answer); `effective_valid_routes`/`build_system_prompt` gate on the flag, and `intent_router.match_intent()` returns `multimodal_pipeline` for media intents. Incoming attachments reach the pipeline via `agents/multimodal/ingestion.py::ingest_media()`, which spills bytes to a content-addressed file and records a path-based descriptor under `state["metadata"]["mm"]["media"]` (never raw bytes in checkpointed state).

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

- `core/config_source.py` — `ConfigSourcePort` (`@runtime_checkable` Protocol, sync `load() -> Mapping[str, str|SecretStr]`, must not raise) + sources: `EnvConfigSource` (the **only** core reader of `os.environ`/`.env`; folds in the legacy `LIGHTAGENT_` mirror **into the returned mapping**, no global mutation; honours unprefixed provider keys), `MappingConfigSource`, `ChainedConfigSource` (first-wins, sub-error skipped), `FakeConfigSource`. Global registry `set_config_source()`/`get_config_source()` (invalidates the `get_settings` cache).
- `core/config.py` — `env_file` dropped from `Settings.model_config`; `settings_customise_sources()` adapts the injected port via a `_ConfigSourceSettingsSource(EnvSettingsSource)` subclass (preserves prefix/`AliasChoices`/JSON-list decoding); init kwargs still win. `build_settings(source=None)` (pure, per-tenant; uses a `ContextVar` for isolation), `get_settings()` delegates behind `@lru_cache`, `reload_settings()`. New fields `tavily_api_key`, `config_source_strict`.
- `core/env_compat.py` — the legacy `LIGHTAGENT_` mirror moved into `EnvConfigSource`; `apply_legacy_env_aliases()` is a deprecated **no-op** shim and is **no longer called at import** (`core/__init__.py` import-time side effect removed → importing `prismal.core` mutates zero `os.environ`).
- `core/exceptions.py` — `ConfigSourceError(source, cause)` (raised by `build_settings` when `config_source_strict` and no source).
- **Relocated reads (W4):** `agents/tools.py` `TAVILY_API_KEY` → `settings.tavily_api_key`; `mcp/connection.py` `token_env` → `resolve_secret(name)` (prefers injected source, falls back to `os.environ`). The single LiteLLM `os.environ.setdefault` write-bridge in `providers/registry.py` stays, fed only from injected `Settings`.
- `composition/config_sources.py` — `apply_org_overrides(settings, org_id, overrides, *, source=None)` threads a per-tenant source via `build_settings(source)` (no global mutation).
- **AST guard** `tests/unit/core/test_no_env_reads.py` forbids new literal config `os.getenv`/`os.environ` reads in `prismal/**` (exempt: `core/config_source.py`, `providers/registry.py`, `skills/` plugins, `mcp/servers/` standalone subprocesses). Test `.env` isolation is now handled by an autouse fixture in `tests/conftest.py` that injects `EnvConfigSource(dotenv_path=None)` + clears LLM-provider env keys (Phase W replaced the old `model_config["env_file"]=None` patch, which is now a no-op).

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

- `core/` — Pydantic Settings config (`get_settings()`), logging (`get_logger()`), exceptions, SQLAlchemy database, user model.
- `memory/` — short-term conversation history + long-term PII-sanitized store (SQLite + ChromaDB; optional MongoDB via `[mongodb]`).
- `mcp/` — Model Context Protocol client, adapter, connection manager.
- `rag/` — RAG engine, CRAG pipeline, interchangeable vector store (`VectorStorePort` + `rag/stores/` adapters, default Chroma; Fase Z), document loaders, embeddings, federated search.
- `scheduler/` — APScheduler-based `CronExecutor`, `DateTimeService` (single time-of-truth, timezone-aware), Prefect flows.
- `sandbox/` — `SandboxExecutor` with docker/podman/nsjail/bwrap/firejail backends (Phase 43); AST denylist in `codeact_agent.py`.
- `monitoring/` — Langfuse traces, OpenTelemetry spans, structlog.
- `data/` — DuckDB + Polars utilities.
- `agents/visualization.py` — `to_mermaid()`/`to_mermaid_png()`/`visualize()`/`save_graph_image()` for any graph-based architecture (compiled graph, `SubgraphDefinition`, `PrismalStateGraphBuilder`); re-exported from `prismal.langgraph`. `SubgraphDefinition` gains `.to_mermaid()`/`.visualize()`/`.save_image()`; `agents.graph.visualize_supervisor_graph()` renders the main graph. `subgraphs/factory.py::assemble_state_graph()` is the shared sync topology builder. Non-graph architectures (patterns, modal agents) raise `TypeError`.
- `skills/` — `available/` (source, committed) · `active/` (runtime-enabled, gitignored) · `custom/` (AI-generated, gitignored).
- `souls/` — Kokoro personas (Fase K): `available/{spirit,mind,heart}/SOUL.md` (committed). Loaded via `SoulsManager`; bodies are user content (route through `SecurePromptBuilder`).

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
