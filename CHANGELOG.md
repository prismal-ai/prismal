# Changelog — prismal

All notable changes to the `prismal` package are documented here.
The project was published as `lightagent-agents` through v2.x; entries prior to
v3.0.0 refer to that name and the `lightagent.*` import namespace.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [3.1.5] — 2026-06-13

Cost & Budget Governance (**Phase C** — `specs/cost-budget-governance/`).
The **enforcement** layer atop `prismal/monitoring/` (observation): meter real
per-run usage, compare it to a `Budget`, and cut off (soft = degrade, hard =
abort) when exceeded. **Additive and opt-in**: gated by `settings.budget_enabled`
(default `False`); with the flag off there is zero extra state and the compiled
supervisor graph is byte-for-byte unchanged (snapshot-tested). Docs:
`docs/budget.md`; example: `examples/budget_governance.py`.

### Added — cost & budget governance (Phase C)

- **`prismal.budget`** package — `types.py` (`Budget`, `BudgetScope`
  `turn|session|tenant`, `TokenCounts`, `Usage` summable with `+`,
  `BudgetStatus`, `Degradation`; `0` on any dimension = unlimited, mirroring the
  `skynet_token_budget` convention), `usage.py::extract_token_usage()` (LangChain
  `usage_metadata` → OpenAI-style `response_metadata['token_usage']` → zeros;
  never raises), `meter.py::CostMeter` (in-memory O(1) `Usage` accumulator, OTel
  counters tagged `agent`/`pattern`/`model`/`tenant`, optional `CostTracker`
  FinOps bridge), `guard.py::BudgetGuard` (`check()` pure verdict, `enforce()`
  hash-first audit + `BudgetExceeded` on hard cap, `degradation()` advice,
  `make_budget_guard_fn()` adapter), `resolve.py` (`resolve_budget()` +
  `seed_budget_run`/`maybe_seed_budget_run`/`get_budget_guard`/`clear_budget_run`
  with a session-keyed in-process registry — live engines never reach the
  checkpoint serializer; only a serializable marker lands in
  `state["metadata"]["budget"]`).
- **`prismal.providers.cost`** — `CostEstimate` + `compute_cost_usd()`: prices a
  call from LiteLLM's native model map first, then the `settings.budget_pricing`
  fallback table, else a zero-cost `"none"` estimate. The only new module
  importing `litellm` (Critical Rule #4); never raises.
- **Settings** (`core/config.py`) — `budget_enabled`, `budget_max_tokens`,
  `budget_max_cost_usd`, `budget_max_calls`, `budget_max_wall_clock_s`,
  `budget_scope`, `budget_soft_ratio`, `budget_hard_cap`, `budget_pricing`,
  `budget_alert_usd`; `_validate_budget` rejects an unknown `budget_scope` at
  load time.
- **Exceptions** (`core/exceptions.py`) — `BudgetExceeded`; re-parented
  `SkynetBudgetExceeded(BudgetExceeded, SkynetError)`.
- **Observability** (`monitoring/otel.py`) — counters
  `prismal.budget_tokens_total`, `prismal.budget_cost_usd_total`,
  `prismal.budget_cutoffs_total` + histogram `prismal.cost_per_call_usd`.
- **Docs & example** — `docs/budget.md` user guide; runnable
  `examples/budget_governance.py`.

### Changed — cost & budget governance (Phase C)

- `react_loop(..., budget_guard=None)` now meters usage after each LLM call and
  checks the budget before the next (hard cap → graceful partial answer + break);
  the per-run guard is wired from `state["metadata"]["budget"]` at the node seam.
- The five expensive patterns honour an injected `budget_guard_fn`:
  `debate_round`, `tree_of_thoughts`, `LATSAgent.search`,
  `MixtureOfAgents.generate`, `reflection_loop`.
- Skynet's supervisor/worker build `Budget(max_tokens=skynet_token_budget)` over a
  shared `CostMeter` and raise `SkynetBudgetExceeded` on breach — **unifying the
  previously dormant `skynet_token_budget`** with this engine.

### Fixed

- `prismal/budget/resolve.py` — moved `collections.abc.Mapping` into the
  `TYPE_CHECKING` block (ruff `TC003`), keeping the lint gate green under the
  pinned pre-commit / CI ruff.

---

## [3.1.4] — 2026-06-12

Config source injection (**Fase W** — `specs/config-source-injection/`).
Configuration is inverted into a hexagonal port (mirror of Fase Y/Z): the core
stops *reading* `.env`/`os.environ` and instead *consumes* an injected
`ConfigSourcePort` that *supplies* raw values; `Settings` keeps its schema and
only validates. **Additive and opt-in**: with no source injected the default
`EnvConfigSource` reproduces today's behaviour byte-for-byte, so the ~151
`get_settings()` call sites are untouched. Docs: `docs/configuration.md`;
examples: `examples/config_source_{env,custom}.py`.

### Added — config source injection (Fase W)

- **`prismal.core.config_source`** module — `ConfigSourcePort`
  (`@runtime_checkable` Protocol, sync `load() -> Mapping[str, str | SecretStr]`,
  must not raise) plus sources: `EnvConfigSource` (the only core reader of
  `os.environ`/`.env`; folds the legacy `LIGHTAGENT_` mirror into its returned
  mapping with no global mutation; honours unprefixed provider keys),
  `MappingConfigSource`, `ChainedConfigSource` (first-wins, sub-error skipped),
  and `FakeConfigSource`. Global registry `set_config_source()` /
  `get_config_source()` (invalidates the `get_settings` cache).
- **`build_settings(source=None)`** (`core/config.py`) — pure, per-tenant
  constructor (`ContextVar`-isolated); `get_settings()` delegates behind
  `@lru_cache`; `reload_settings()`. New fields `tavily_api_key`,
  `config_source_strict`.
- **`ConfigSourceError(source, cause)`** (`core/exceptions.py`) — raised by
  `build_settings` when `config_source_strict` and no source is available.
- **`apply_org_overrides(settings, org_id, overrides, *, source=None)`**
  (`composition/config_sources.py`) — threads a per-tenant source via
  `build_settings(source)` with no global mutation (Fase R consumer).
- **AST guard** `tests/unit/core/test_no_env_reads.py` — forbids new direct
  config `os.getenv`/`os.environ` reads in `prismal/**` (exempt:
  `EnvConfigSource`, the LiteLLM write-bridge).

### Changed — config source injection (Fase W)

- `Settings` drops `env_file` from `model_config`; `settings_customise_sources()`
  adapts the injected port via a `_ConfigSourceSettingsSource(EnvSettingsSource)`
  subclass (preserves prefix / `AliasChoices` / JSON-list decoding); init kwargs
  still win.
- Relocated raw config reads: `agents/tools.py` `TAVILY_API_KEY` →
  `settings.tavily_api_key`; `mcp/connection.py` `token_env` →
  `resolve_secret(name)` (injected source first, `os.environ` fallback).
- `core/env_compat.py`: the legacy `LIGHTAGENT_` mirror moved into
  `EnvConfigSource`; `apply_legacy_env_aliases()` is now a deprecated no-op and
  is no longer called at import — importing `prismal.core` mutates zero
  `os.environ`.

### Fixed

- `reload_settings()` no longer raises `AttributeError` when `get_settings` is
  monkeypatched with a plain function (clears the cache only when present);
  fixes 7 unrelated tests that errored at teardown under the autouse `.env`
  isolation fixture.

### Security

- Bump `pypdf` to `>=6.12.0` (installed 6.13.2) — fixes **CVE-2026-48155** and
  **CVE-2026-48156** (crafted-PDF DoS via layout-mode extraction / `/W [0 0 0]`
  cross-reference streams).
- Triage **CVE-2025-3000** (`torch` `torch.jit.script` memory corruption,
  local-only, no upstream fix) — added to the mirrored ignore list
  (`.trivyignore` + `.pre-commit-config.yaml` + `ci.yml`) with justification and
  re-evaluation trigger.

---

## [3.1.3] — 2026-06-09

Runtime composition root (**Fase R** — `specs/composition-root/`). A single
composition *facade* — `build_runtime()` — assembles every core port from
`settings` plus an optional tenant (`org_id`): tool provider (Fase Y), vector
store (Fase Z), embeddings, checkpointer, and audit. The host (`prismal-server`)
calls it once in its lifespan and gets back a `RuntimeContext` grouping the ports
with a coordinated teardown. **Additive and opt-in**: code using
`set_tool_provider` / `VectorStoreFactory` directly is unaffected. Guiding
principle — *orchestrate, do not reimplement*: it reuses the Fase Y/Z builders and
the existing factories. Docs: `docs/composition-root.md`; example:
`examples/composition_root.py`.

### Added — composition root (Fase R)

- **`prismal.composition`** package — re-exports the composition root
  (`composition/runtime.py`) and the config loaders (`composition/config_sources.py`).
- **`build_runtime(settings=None, *, org_id=None, overrides=None, mode=None,
  collection_base="default", mcp_config_path=None)`** (`composition/runtime.py`)
  — composes the five ports in one call, reusing `build_default_tool_provider`
  (Y), `VectorStoreFactory` (Z), `EmbeddingsFactory`, `build_checkpointer`, and
  `AuditLogger`. On any sub-port failure it tears down what was already created
  and raises `RuntimeCompositionError`.
- **`RuntimeContext`** — groups `tool_provider`, `vector_store_provider`,
  `embeddings`, `checkpointer`, `audit`, `config`, `org_id`; idempotent
  `aclose()` (disconnects MCP, closes the checkpointer, releases built stores)
  and async-context-manager support.
- **`RuntimeConfig`** — frozen, resolved view (backend, collection name, mode,
  org); sensitive fields stay referenced from `settings`, never copied in clear.
- **`VectorStoreProvider`** + **`VectorStoreProviderPort`**
  (`agents/extension/ports.py`) — factory-backed, tenant-scoped vector-store
  source (`get_store(collection_name=None)` applies `collection_for(base, org_id)`).
- **Two modes** via **`settings.runtime_mode`** (`core/config.py`): `global`
  injects the tool-provider singleton; `context` keeps every port in the
  `RuntimeContext`. Backward-compat: derived from `tool_provider_mode` when unset.
- **Tenant resolution** — `collection_for(base, org_id)` (`base_<org_id>`)
  applied identically to RAG and memory; parallel tenants stay isolated.
- **Config loaders** (`composition/config_sources.py`) — `load_mcp_config`,
  `resolve_skills_source`, `resolve_vector_store`, `apply_org_overrides`,
  `collection_for` (pure, side-effect-free; the dashboard reads the same sources).
- **`build_test_runtime(...)`** — deterministic `RuntimeContext` with fakes
  (`FakeToolProvider`, `FakeVectorStore`, in-module embeddings/checkpointer/audit
  doubles); no I/O, `aclose()` is a no-op.
- **`RuntimeCompositionError`** (`core/exceptions.py`) — carries the failing
  port name.
- Observability: `composition.runtime_built` / `composition.runtime_teardown`
  logs.

### Notes — composition root (Fase R)

- Fase Z ships a *factory*, not a process singleton or graph-bound provider, so
  the vector store is always carried in the `RuntimeContext` via
  `VectorStoreProvider`; there is no `set_vector_store_provider` global to inject
  in global mode (deviation from the SPEC text, which assumed a Z singleton).
  Consumers resolve tenant stores with `ctx.vector_store_provider.get_store(...)`.
- The logic lives in `composition/runtime.py` (the package `__init__.py` is a thin
  re-export) so it is covered by the suite — the repo omits `*/__init__.py` from
  coverage.

---

## [3.1.2] — 2026-06-08

Interchangeable vector store (**Fase Z** — `specs/vector-store-port/`). Vector
search is inverted as a hexagonal port: RAG patterns and the memory layer depend
on `VectorStorePort` and never construct a backend — `VectorStoreFactory` selects
the adapter from `settings.vector_store_backend`. **Chroma stays the default**, so
existing deployments are byte-for-byte unchanged; the alternatives are opt-in via
extras. Additive and backward-compatible.

### Added — vector store port (Fase Z)

- **`VectorStorePort`** (`agents/extension/ports.py`) — `@runtime_checkable`
  Protocol (`collection_name`, `add_documents`, `similarity_search`,
  `delete_by_source`, `delete_collection`), re-exported from
  `prismal.agents.extension`. Score contract (SPEC-VS-002): `similarity_search`
  returns `(Document, score)` with `score ∈ [0, 1]`, higher = more relevant.
- **Adapters** (`rag/stores/`) — `chroma.py` (default; **moved** from
  `rag/vector_store.py`, which stays a re-export shim), `lancedb.py`,
  `sqlite_vec.py` (both embedded, no server), `qdrant.py` (embedded/server),
  `pgvector.py` (server). Backend SDK imports are **deferred**; a missing extra
  raises `VectorStoreBackendUnavailable`. Score normalization lives in
  `rag/stores/_normalize.py`.
- **`VectorStoreFactory`** + **`FakeVectorStore`** (`rag/vector_store_factory.py`)
  — backend selection (mirror of `EmbeddingsFactory`) and a deterministic,
  I/O-free test double.
- **Settings** (`core/config.py`) — `vector_store_backend` (default `chroma`),
  `vector_store_path` (embedded), `vector_store_url` + `vector_store_api_key` /
  `vector_store_user` / `vector_store_password` (server). `chroma_path` is kept
  as a backward-compatible alias via `Settings.resolve_vector_store_path()`.
- **Exceptions** (`core/exceptions.py`) — `VectorStoreError` (generalizes
  `ChromaStoreError`, which now subclasses it) and `VectorStoreBackendUnavailable`.
- **Extras** (`pyproject.toml`) — `[lancedb]`, `[sqlite-vec]`, `[qdrant]`,
  `[pgvector]`; base install gains no mandatory dependency.
- **Docs & example** — `docs/vector-stores.md`, `examples/vector_store_lancedb.py`.

### Changed

- RAG consumers (`engine`, `hyde`, `fusion`, `self_rag`, `hybrid`, `hierarchical`,
  `multi_vector`, `multimodal`, `crag`) and the memory layer (`long_term`,
  `mongodb_store`) type against `VectorStorePort` and build their default store
  through `VectorStoreFactory` — no logic change.

---

## [3.1.1] — 2026-06-07

Patch release: container image and release automation. No changes to the
Python package's runtime behaviour.

### Added

- **Dockerfile** — multi-stage container image (build wheel + venv on
  `python:3.13-slim`, copy only the venv into a slim runtime; base install
  only — derive an image and `pip install "prismal-ai[all]"` for extras).
- **`.github/workflows/docker-publish.yml`** — builds and publishes the image
  to GHCR (`ghcr.io/prismal-ai/prismal`) on `prismal/v*` tags
  (`X.Y.Z` + `latest`) and on manual dispatch (`dev` when no version is
  given), using plain `docker` and the built-in `GITHUB_TOKEN`.

### Changed

- **`.github/workflows/release.yml`** — extracts the version's section from
  `CHANGELOG.md` as the GitHub Release notes when a `prismal/v*` tag is
  pushed.

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
