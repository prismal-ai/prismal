# Changelog — prismal

All notable changes to the `prismal` package are documented here.
The project was published as `lightagent-agents` through v2.x; entries prior to
v3.0.0 refer to that name and the `lightagent.*` import namespace.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [3.1.0] — 2026-06-07

Two new opt-in agent layers — the **Kokoro deliberation agents** (Fase K) and
the **Skynet swarm supervisor** (Fase S) — plus the dependency-security
remediation. Both layers are gated by their own settings flags (default
`False`); with the flags off the compiled supervisor graph is byte-for-byte
unchanged.

### Added — Skynet swarm supervisor (Fase S, opt-in)

A swarm map-reduce layer over agents (`specs/skynet-swarm/`, user guide in
`docs/skynet.md`). Gated by `settings.skynet_enabled` (default `False`) — with
the flag off the compiled supervisor graph is byte-for-byte unchanged
(snapshot-tested).

- **Value objects** (`agents/skynet/types.py`) — frozen `SwarmOrder`,
  `SwarmPlan` (with `.size` and the `deferred` overflow set), `WorkerResult`,
  `SwarmResult`.
- **`SkynetSupervisor`** (`agents/skynet/supervisor.py`) — owns swarm sizing
  and the control loop: `plan()` decomposes a goal into N sub-orders (dynamic
  by default; fixed-K via `skynet_swarm_size`), hard-caps N at
  `min(skynet_max_swarm, parallel_max_workers)` and **defers** the overflow
  (never drops it); re-plans unmet orders deterministically (attempt+1, no
  LLM call). `evaluate()` returns `(complete, answer)`. Goal and worker
  outputs reach the model only through `SecurePromptBuilder`; audit is
  hash-first (`skynet_plan` / `skynet_evaluate`).
- **`SwarmWorker`** (`agents/skynet/worker.py`) — executes one order: tools
  resolved per `order.role` through the injected `ToolProviderPort`
  (`agent_name="skynet_worker"`); every requested tool action passes the
  `ActionInterceptor` gateway (denial → noted, never fatal); any backend
  failure is captured as `WorkerResult(success=False)` — one worker's failure
  never aborts the swarm.
- **`reduce_results()`** (`agents/skynet/reduce.py`) — `synthesis` (default) |
  `concat` | `first_success`; failures are excluded from the reduction but
  retained for re-planning; a failing synthesis degrades to the deterministic
  concat.
- **`skynet` subgraph** (`agents/subgraphs/skynet/`) — `plan → Send fan-out →
  worker ⇉ reduce → evaluate → output` with a bounded re-plan loop
  (`skynet_max_rounds`), exported as `build_skynet_subgraph()` + idempotent
  `register_skynet()`. Fan-out reuses `make_parallel_dispatcher` over the new
  top-level `AgentState.skynet_orders` field; concurrent worker results merge
  through the Phase 34 `parallel_results` `operator.add` channel (tagged);
  durable state under `state["metadata"]["skynet"]`; per-stage OTel spans
  (`skynet.plan` / `skynet.worker` / `skynet.reduce` / `skynet.evaluate`).
- **Supervisor integration (opt-in)** — `intent_router.match_intent()` returns
  `skynet` for swarm/parallel intents (EN/ES); `get_async_compiled_graph()`
  wires the route, and `effective_valid_routes` / `build_system_prompt` gate
  on `skynet_enabled`; `DEFAULT_CAPABILITY_MAP["skynet"/"skynet_worker"]`
  resolve tools through the injected `ToolProviderPort`.
- **Settings** — `skynet_enabled`, `skynet_swarm_size` (0 = dynamic),
  `skynet_max_swarm` (clamped to `parallel_max_workers`; fixed size > cap →
  `SkynetConfigError`), `skynet_max_rounds`, `skynet_reduce_strategy`,
  `skynet_worker_model`, `skynet_planner_model`, `skynet_token_budget`.
- **Exceptions** — `SkynetError` hierarchy (`SkynetPlanError`,
  `SwarmWorkerError`, `SkynetConfigError`, `SkynetBudgetExceeded`).
- **Example** — `examples/skynet_swarm.py` runs the full swarm with injected
  fakes (no LLM/network); architecture guard tests enforce no module-level
  provider imports and no `prismal.mcp`/`prismal.skills` anywhere in the
  Skynet modules.

### Added — Kokoro deliberation agents (Fase K, opt-in)

A persona-driven deliberation-and-decision layer (`specs/kokoro-deliberation/`,
user guide in `docs/kokoro.md`). Gated by `settings.kokoro_enabled` (default
`False`) — with the flag off the compiled supervisor graph is byte-for-byte
unchanged (snapshot-tested).

- **Souls tier** (`prismal/souls/`) — Markdown-authored personas mirroring the
  `skills/` layout (`available/` committed, `active/` allow-list, `custom/`
  gitignored). `SoulMetadata` + `Soul` value objects, `parse_soul_md()` /
  `load_soul()` (schema validation, `soul_max_body_chars` cap, path
  confinement), and `SoulsManager` (`list_souls` / `load` / `load_triad` —
  exactly three souls or `KokoroConfigError`). Three defaults shipped:
  `spirit` (魂 *tamashii* — values), `mind` (知 *chi* — logic), `heart`
  (情 *jō* — empathy).
- **`SoulAgent`** (`agents/kokoro/soul_agent.py`) — persona-conditioned
  position generator. The soul body (and every user-controlled field) reaches
  the model only through `SecurePromptBuilder`; backend callable-injected
  (`generate_fn`), default lazily wires `ProviderRegistry().get_llm()` with
  per-soul `metadata.model` override.
- **`deliberate()`** (`agents/kokoro/deliberation.py`) — bounded
  agreement-seeking rounds reusing the `debate` primitives (`DebatePosition`,
  `pairwise_jaccard`): round 1 concurrent independent positions, later rounds
  cross-revision (each soul sees only the others); early-stop at
  `kokoro_agreement_threshold`, hard cap `kokoro_max_rounds`; revision
  failures degrade to the soul's previous position.
- **`KokoroJudgeAgent`** (`agents/kokoro/judge.py`) — renders a `Verdict`
  (decision, rationale, one `lens_summaries` entry per soul, retained
  dissent); `act()` executes at most one `KokoroAction` gated by
  `kokoro_execute_actions` **and** the `ActionInterceptor` gateway (denial →
  `blocked_reason`, no exception); `AuditLogger` records verdict + action
  hash-first (`kokoro_verdict` / `kokoro_action` events).
- **`kokoro` subgraph** (`agents/subgraphs/kokoro/`) —
  `load_souls → deliberate → judge → act → output`, exported as
  `build_kokoro_subgraph()` + idempotent `register_kokoro()`; all runtime
  state under `state["metadata"]["kokoro"]`; per-stage OTel spans
  (`kokoro.load_souls` / `kokoro.deliberate` / `kokoro.judge` / `kokoro.act`).
- **Supervisor integration (opt-in)** — `intent_router.match_intent()` returns
  `kokoro` for deliberation intents (EN/ES); `get_async_compiled_graph()`
  wires the route, and `effective_valid_routes` / `build_system_prompt` gate
  on `kokoro_enabled`; `DEFAULT_CAPABILITY_MAP["kokoro"]` resolves judge tools
  through the injected `ToolProviderPort`.
- **Settings** — `kokoro_enabled`, `souls_dir`, `kokoro_souls` (exactly 3),
  `kokoro_max_rounds`, `kokoro_agreement_threshold` (∈ [0,1]),
  `kokoro_execute_actions`, `kokoro_judge_model`, `soul_max_body_chars`.
- **Exceptions** — `KokoroError` hierarchy (`SoulValidationError`,
  `SoulNotFoundError`, `KokoroConfigError`, `DeliberationError`, `JudgeError`).
- **Example** — `examples/kokoro_deliberation.py` runs the full pipeline with
  injected fakes (no LLM/network); architecture guard tests enforce no
  module-level provider imports and no `prismal.mcp`/`prismal.skills` anywhere
  in the Kokoro modules.

### Security — Dependabot remediation (18 alerts, 2026-06)

Full triage and remediation of the 2026-06-05 Dependabot report
(3 Critical, 8 High, 6 Moderate, 1 Low). Decision matrix, exposure
analysis (library vs server surface) and per-alert closure criteria in
`specs/dependency-security-remediation/`.

- **Already fixed by the current lock (12 alerts)** — litellm 1.86.2
  (CVE-2026-42208, CVE-2026-40217 + proxy-surface cluster), urllib3 2.7.0
  (CVE-2026-21441, GHSA-qccp-gfcp-xxvc), langsmith 0.8.7 /
  langchain-classic 1.0.7 (CVE-2026-45134), idna 3.17 (CVE-2026-45409),
  starlette 1.2.0 (CVE-2026-48710), pymdown-extensions 10.21.3
  (CVE-2026-46338). Closed by pushing the lock to the scanned branch.
- **Upgraded** — `aiohttp >= 3.14.0` (CVE-2026-34993 RCE via
  `CookieJar.load()` pickle, CVE-2026-47265 cross-origin cookie leak);
  `prefect >= 3.6.28` (CVE-2026-7724 SSRF DNS-rebinding TOCTOU in
  `validate_restricted_url`; lock resolves 3.7.4); transitives
  pip 26.1.2 (PYSEC-2026-196) and pyjwt 2.13.0 (PYSEC-2026-175/177/178/179).
- **Mitigated (no upstream fix)** — transformers CVE-2026-1839 neutralized
  by `torch >= 2.6` constraint (PyTorch `safe_globals()`; prismal never
  uses `Trainer`); chromadb CVE-2026-45829 (embedded-only usage, no HTTP
  server); ecdsa CVE-2024-23342 (won't-fix; python-jose → PyJWT migration
  registered as debt). All documented in `.trivyignore` with re-evaluation
  triggers.
- **Supply chain** — trivy-action compromise (GHSA-69fq-xp46-6x23) closed:
  no CI run in the compromise window (workflows exist since 2026-05-22);
  trivy binary download now sha256-verified; **all GitHub Actions pinned
  to immutable commit SHAs**.
- **Hygiene** — `.trivyignore`, pre-commit `pip-audit` hook and
  `ci.yml` `PIP_AUDIT_IGNORES` reduced to an exact 3-way mirror of the
  4 no-fix CVEs only (18 obsolete ignores removed).

### Added — Graph visualization

A reusable way to visualize any graph-based architecture (the compiled
supervisor graph, every `SubgraphDefinition`, and `PrismalStateGraphBuilder`
output).

- **`prismal/agents/visualization.py`** — `to_mermaid(obj)` (offline Mermaid
  source), `to_mermaid_png(obj)`, `save_graph_image(obj, path)`, and
  `visualize(obj)` (inline PNG in a notebook via IPython with a graceful
  fallback to Mermaid text). Non-graph architectures (reasoning patterns, modal
  agents) raise a clear `TypeError`.
- **`SubgraphDefinition.to_mermaid()` / `.visualize()` / `.save_image(path)`** —
  one-line visualization for any subgraph.
- **`prismal.langgraph`** re-exports `to_mermaid` / `to_mermaid_png` /
  `visualize` / `save_graph_image`; **`agents.graph.visualize_supervisor_graph()`**
  renders the main graph.
- Internal: `subgraphs/factory.py` exposes `assemble_state_graph(definition)`
  (sync, checkpointer-less topology assembly) shared by `build` and the viz
  helpers. Runnable demo: `examples/visualize_graphs.py`.

### Added — Multimodal Agents (Fase F)

Opt-in vision/audio/video layer on top of the text agents, gated by
`settings.multimodal_enabled` (default `False`). See `specs/multimodal-agents/`.

- **Providers** (`prismal/providers/`) — `get_stt()` (Whisper API + local
  faster-whisper), `get_tts()` (pyttsx3 → openai → elevenlabs cascade),
  `get_vision_llm()`, `get_multimodal_llm()`, `get_cross_modal_embeddings()`
  (CLIP). All SDK access stays isolated here.
- **Modal agents** (`prismal/agents/multimodal/`) — `VisionAgent`,
  `AudioAgent`, `VideoAgent`, `classify_modality()` +
  `make_modality_router_node()`, and `MultimodalFusion`
  (`concat`/`moderator`/`moa`). Callable-injected and degrade gracefully.
- **Subgraph** (`agents/subgraphs/multimodal_pipeline/`) —
  `build_multimodal_subgraph()` + idempotent `register_multimodal_pipeline()`
  (router → [vision|audio|video|text] → fusion → output_formatter).
- **Multimodal RAG** (`rag/multimodal.py`) — `MultimodalRAGEngine` +
  `MultimodalRetrievedChunk` with modality-filtered search; `rag/loaders/`
  package adds `ImageLoader` / `AudioLoader` / `VideoLoader` (backward-compatible
  with the former `rag/loaders.py`).
- **Security** — `MediaValidator` (magic bytes, size/duration limits) +
  `InputSanitizer.sanitize_media()` (EXIF strip) + `ActionInterceptor`
  `.check_media_op()`; `AuditLogger.log_media()` (hash + modality, never content).
- **Settings** — `multimodal_enabled`, `vision/audio/video_enabled`, model
  strings, media size/duration limits, `max_frames_per_video`,
  `video_sample_fps`, `tts_max_chars`, `vision_ocr_enabled`.
- **Exceptions** — `MultimodalError`, `STTError`, `TTSError`,
  `VisionAgentError`, `AudioAgentError`, `VideoAgentError`,
  `ModalityRouterError`, `MultimodalFusionError`, `MultimodalRAGError`,
  `MediaValidationError`, `MissingDependencyError`.
- **Packaging** — extras `[multimodal]`, `[multimodal-local]`,
  `[multimodal-premium]`, `[multimodal-embed]`.
- **Supervisor wiring** — when `multimodal_enabled=True`,
  `get_async_compiled_graph()` wires a single `multimodal_pipeline` supervisor
  route (`effective_valid_routes`/`build_system_prompt` gate on the flag;
  `match_intent` returns `multimodal_pipeline` for media intents) +
  `DEFAULT_CAPABILITY_MAP` entries + documented MCP capability servers.
  Byte-for-byte unchanged when the flag is off.
- **Media ingestion** — `agents/multimodal/ingestion.py::ingest_media()` /
  `cleanup_session_media()`: the entry-layer boundary that validates,
  EXIF-strips, spills bytes to a content-addressed file, audits the hash, and
  records a path-based descriptor in `state["metadata"]["mm"]["media"]` (no raw
  bytes in checkpointed state); `settings.media_workspace`.

### Added — Extension Surface (Fase X)

Public, opt-in extension API so users and third-party plugins can build on
LangGraph without forking the repo. See `docs/extension.md` and
`specs/extension-surface/`.

- **`prismal.langgraph`** — official re-export of `StateGraph`, `START`, `END`,
  `Send`, `interrupt`, `add_messages`, `CompiledStateGraph`, plus `AgentState`,
  `SubgraphDefinition`, `SubgraphRegistry`, and a dynamic `VERSION`.
- **`@prismal_node`** decorator (`prismal.agents.extension`) wrapping any
  `async (state) -> state_update` with a middleware chain (security → OTel →
  logging → retry → timeout → audit → error mapping), plus
  `list_registered_nodes()` / `get_node_metadata()` and auto-registration of
  capabilities in `DEFAULT_CAPABILITY_MAP`.
- **`PrismalStateGraphBuilder`** — fluent builder over `StateGraph[AgentState]`
  with node auto-wrapping; `compile()` → `SubgraphDefinition`, `compile_raw()`
  → `CompiledStateGraph`.
- **Plugin discovery** — `discover_plugins()` over four entry-point groups
  (`prismal.subgraphs|nodes|tools|rag_engines`) with allowlist/denylist and
  per-plugin failure isolation; `list_plugins()` / `get_plugin_info()`; CLI
  `python -m prismal.plugins {list,info,doctor,enable,disable}`; new
  `RAGEngineRegistry`.
- **`LangChainRunnableAdapter`** — wrap any `Runnable` / `AgentExecutor` as a
  prismal node with auto input/output mapping.
- **Hexagonal ports** — `CheckpointPort`, `AuditPort`, `EmbeddingsPort`,
  `ToolPort` (`@runtime_checkable` Protocols) + `conforms_to()`.
- **Settings** — `plugins_autodiscover`, `plugins_allowlist`,
  `plugins_denylist`, `plugins_groups_enabled`, `extension_default_security`,
  `extension_default_audit`, `extension_default_timeout_s`.
- **Exceptions** — `ExtensionError`, `NodeExecutionError`, `NodeTimeoutError`,
  `NodeValidationError`, `PluginLoadError`, `PluginConflictError`,
  `AdapterError`, `LangChainAdapterError`.
- **`SubgraphRegistry.register_sync()`** for synchronous (startup/plugin)
  registration; **`AuditLogger.log_event()` / `log_node()` / `log_media()`**.
- Runnable examples under `examples/extension/` and an installable
  `examples/plugin_template/`.

---

## [3.0.2] — 2026-06-06

PyPI distribution rename, an automated release pipeline, and a full
English-language pass over the documentation.

### Changed
- **Distribution renamed** `prismal` → `prismal-ai`. PyPI rejected `prismal`
  as too similar to an existing project, so the published distribution is now
  `prismal-ai` (`pip install prismal-ai`). The **import namespace is
  unchanged** — code keeps using `from prismal. …` — and the wheel still
  targets `packages = ["prismal"]`.
- Self-referential extras (`ml-dl`, `all`) updated from `prismal[…]` to
  `prismal-ai[…]`; `uv.lock` regenerated for the new project name.
- `README.md` install instructions, badges, and title updated to the
  `prismal-ai` distribution name.

### Added
- **Automated PyPI release workflow** (`.github/workflows/release.yml`):
  builds and publishes on push to `main` or a `prismal/v*` tag, using an API
  token from the `PYPI_API_TOKEN` GitHub secret (`TEST_PYPI_API_TOKEN` for the
  manual TestPyPI dry run). `skip-existing` makes unchanged-version runs a
  no-op.

### Documentation
- **Full English translation** of all documentation: `README.md`, every file
  under `docs/`, and every spec under `specs/` (PLAN/SPEC/ARCHITECTURE/TASKS
  across all phases) are now entirely in English. "Fase" → "Phase" throughout.

---

## [3.0.0] — 2026-05-22

Rebrand of the framework from **LightAgent** to **Prismal**
(`lightagent-agents` → `prismal`). See `propuesta.md` and `PLAN_MIGRACION.md`.

### Changed
- **Distribution renamed** `lightagent-agents` → `prismal`; project URLs now
  point to `github.com/prismal-ai/prismal` and `prismal.dev`.
- **Import namespace renamed** `lightagent` → `prismal` (PEP 420 namespace
  package). All imports become `from prismal. …`.
- Observability rebranded: logger names and OpenTelemetry span attributes
  `lightagent.*` → `prismal.*`; OTEL `service_name` default `lightagent` →
  `prismal`.

### Added
- Transitional **import shim** `lightagent` that redirects `lightagent.*` →
  `prismal.*` (not shipped in the wheel; to be removed in a later release).
- **Environment-variable fallback** (`prismal/core/env_compat.py`): legacy
  `LIGHTAGENT_*` variables are mirrored onto `PRISMAL_*` on import with a
  one-time `DeprecationWarning`, so existing deployments keep working.

### Breaking
- **Import paths**: `from lightagent. …` no longer resolve once the shim is
  removed; migrate to `from prismal. …`.
- **Exception base class** `LightAgentError` → `PrismalError` (and subclasses).
- **Environment prefix** `LIGHTAGENT_` → `PRISMAL_` (legacy names still work via
  the deprecated fallback above).
- **Persisted data identities**: memory collection `lightagent_memory` →
  `prismal_memory`; default DB paths `data/db/lightagent.db` →
  `data/db/prismal.db`. Existing persisted memory/databases must be migrated or
  reconfigured to the previous values.
- Renaming the `lightagent` import namespace breaks the sibling app package that
  shares the PEP 420 namespace; it must be coordinated/rebranded in tandem.

### Deprecated
- The `lightagent-agents` distribution (a thin shim depending on `prismal`) and
  the `LIGHTAGENT_*` environment prefix.

---

## [2.0.0] — 2026-04-18

### Added
- **Package extraction** — `lightagent-agents` extracted from the `lightagent` monolith as a standalone, publishable Python package (PEP 420 namespace package under `lightagent.*`).
- **26 agent nodes** for LangGraph state machine: `coder`, `researcher`, `codeact_agent`, `cua_agent`, `data_analyst`, `file_manager`, `skill_manager`, `rag_agent`, `planner`, `critic`, `cron_manager`, `parallel_research`, `meta_learner`, `skill_creator`, `domain_supervisor`, `network_supervisor`, and more.
- **Reflection Loop Framework** (Phase 33) — `reflection_loop()` composable pattern in `agents/patterns/reflection.py`.
- **Map-Reduce / Fan-out** (Phase 34) — `make_parallel_dispatcher()` with `Send()` in `agents/patterns/parallel.py`.
- **Human-in-the-Loop** (Phase 35) — `hitl_gate()` factory with `interrupt()` in `agents/subgraphs/gates.py`.
- **Configurable Checkpointing** (Phase 36) — `build_checkpointer()` supporting SQLite and PostgreSQL.
- **Long-Term Memory Store** (Phase 37) — `LongTermMemoryStore` with PII sanitization.
- **CodeAct Agent** (Phase 38) — direct Python code generation with AST denylist hardening (Phase 42).
- **SandboxExecutor** (Phase 43) — process isolation backends: docker, podman, nsjail, bwrap, firejail.
- **Deterministic Intent Router** (Phase 44) — `match_intent()` in `agents/intent_router.py` with global `_MAX_TOTAL_TOOLS = 120` cap.
- **ML/DL Pipeline** (Phase 26) — `Ingester → EDA → Features → Trainer → Evaluator → Exporter` subgraph.
- **Financial Analysis Pipeline** (Phase 27 + 39) — `Collector → Technical → Fundamental → Risk → Report` with quality gates.
- **Computer Use Agent** (Phase 41) — VLM + browser automation with HITL.
- **Hierarchical Multi-Agent Architecture** (Phase 40) — 3-level hierarchy with domain sub-orchestrators.
- **DateTime & Timezone-Aware Cron** (Phase 28) — `DateTimeService` as single time source of truth.
- **NeMo Guardrails** (Phase 20) — NVIDIA NeMo L3 guardrails integration.
- **RBAC & Multi-User** (Phase 19) — `require_role()` dependency, JWT auth, bcrypt passwords.
- **Autonomous v2.0** (Phase 21) — A2A protocol, MetaLearner, voice loop.
- **Native Cron Engine** (Phase 23) — APScheduler with `CronExecutor`, hot-sync via tools.
- **Dynamic Subgraphs** (Phase 24) — `SubgraphFactory.build()` + `SubgraphRegistry`.
- **Package Security & Maintenance** (Phase 30) — OSV API scanner, `uv pip check`, audit reports.
- **API Path Hardening** (Phase 31) — path confinement guards, `resolve().is_relative_to()`.
- **Prompt Quality Framework** (Phase 32) — 7-component system prompts for all 26 agents.

### Changed
- Converted `lightagent/` root to PEP 420 implicit namespace package (removed `__init__.py`).
- All agent, core, provider, memory, MCP, security, scheduler, monitoring, RAG, skills, data, and sandbox modules now live exclusively in this package.

### Notes
- The `lightagent` app package (API, dashboard, channels, CLI) depends on this package via `lightagent-agents>=2.0.0`.
- Install: `pip install lightagent-agents` or `uv pip install lightagent-agents`.
- For all optional extras: `pip install "lightagent-agents[all]"`.

---

## Previous versions

Versions prior to 2.0.0 were part of the `lightagent` monolithic package.
See the main [CHANGELOG.md](../../CHANGELOG.md) in the repository root for history.
