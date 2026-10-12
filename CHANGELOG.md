# Changelog — prismal

All notable changes to the `prismal` package are documented here.
The project was published as `lightagent-agents` through v2.x; entries prior to
v3.0.0 refer to that name and the `lightagent.*` import namespace.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- NTS.1 batch 1: I/O contracts on `coder`, `researcher`, `data_analyst`,
  `planner` and `critic` (`<Node>Input` / `<Node>Output`, checked only with
  `node_typesafety_enabled`; adopt in `warn` first). `planner` lists must be
  strings; `critic.iteration_count >= 0` in and out.
- NTS.1 batch 2: I/O contracts on `rag_agent` (typed `metadata.rag_agent`),
  `parallel_researcher`, `parallel_researcher_worker` (exactly one
  `parallel_results` entry per worker — the channel is `operator.add`),
  `research_aggregator`, `codeact` and `cua`.
- `@prismal_node(propagate_errors=True)` re-raises a node's own exception
  unchanged. Built-in nodes use it, so adopting a contract never changes their
  error semantics.
- Shared tenant quota counters: with `budget_scope="tenant"` the guard checks
  the tenant's total over a UTC calendar window (`budget_tenant_window`:
  `month` | `day`). Totals are kept in a SQLite file shared by every worker
  (`budget_tenant_store_path`; atomic upserts, WAL) or in process memory.
  New `prismal.budget.tenant_quota` (`TenantCostMeter`,
  `SqliteTenantQuotaStore`, `TenantQuotaStorePort`). Store failures fall back
  to local counting.
- Recalled memory in agent prompts (Phase MEM follow-up): the `researcher`
  node appends one tail `<turn_context>` message built by
  `memory_block_for_prompt` (`prismal.memory.v2.agent_prompt.memory_turn_message`),
  never touching the cached prefix. Only with memory v2 in `enforce` mode and
  the cache-stable layout; `RuntimeContext.bind_org()` now also binds
  `ctx.memory` (`use_memory_service` / `set_memory_service` for other hosts).
  Fail-closed: no recall without a user identity, untrusted records dropped,
  and a run bound to `None` never falls back to the process-wide service.
- `prismal.events.subagent.emit_subagent()`: Skynet workers and MoA proposers
  now emit the AG-UI `subagent` custom payload, so live runs streamed with
  `custom` show `SUBAGENT_STARTED` / `SUBAGENT_FINISHED` / `SUBAGENT_ERROR`
  (ids, names and error kinds only; no-op when `custom` is not streamed).
- `CronExecutor.add_callable(job_id, func, *, cron, timezone=None,
  max_instances=1, coalesce=True)`: the public way to schedule a coroutine
  function on a crontab (scheduler timezone by default; nothing persisted).
  Memory v2 `register_consolidation_job` now uses it instead of reaching into
  `executor._scheduler`; an AST guard forbids private scheduler access outside
  `prismal/scheduler/`.
- `scripts/gen_settings_docs.py` generates `env.example` and the new
  `docs/settings-reference.md` from `Settings` / `MaintenanceSettings` (486 + 6
  fields, grouped by the config.py sections; secrets never show a default).
  `tests/unit/core/test_settings_docs_sync.py` fails when either file drifts.
- Descriptions for the 11 `Settings` fields that had none (gateway base URLs,
  reconcile/poll intervals, DEC calibration/margin/sampling knobs).
- Docstrings for every public symbol in `events/`, `a2a/`, `routing/`,
  `providers/` and `decisions/` (199 added; no behaviour change).

### Documentation
- New guides: `docs/agents.md`, `rag.md`, `patterns.md`, `subgraphs.md`,
  `providers.md`, `mcp.md`, `skills.md`, `sandbox.md`, `scheduler.md`,
  `events.md`, `data-utilities.md`, plus the `docs/README.md` index.
- README / CLAUDE.md counts corrected against the code: 30 supervisor routes
  (15 core + 15 opt-in; `meta_learner`, `skill_creator`, `network_supervisor`,
  `spawner` are helpers, not routes), 11 RAG engines, 15 subgraph packages;
  package tree, roadmap statuses (Phase Y and LH implemented, wave 2026-10),
  release procedure (automated by `release.yml`), sandbox isolation scope,
  `events/` description, STT backend, state reducers and graph caching.
- `assets/prismal_architecture.svg` and `docs/prismal-ai-architecture.excalidraw`
  redrawn for v3.19.0 (wave 2026-10 layers, all ports, 30 routes).
- `examples/README.md` and `examples.md` cover every example (incl. the eight
  wave 2026-10 ones); `CONTRIBUTING.md`, `CLASIFICACION_AGENTES.md` and
  `docs/competitive-analysis.md` refreshed; `PLAN_MIGRACION.md` moved to
  `docs/history/`; fixed the broken CHANGELOG link and the `prismal-ai[...]`
  extra name in the vector-store docs.

### Fixed
- `@prismal_node` broke HITL: `GraphInterrupt` (from `interrupt()`) is an
  `Exception` subclass, so `error_mapping` swallowed or wrapped it and `retry`
  could re-run it. It now always bubbles up and is never retried. A
  `ParentCommand` is still mapped, because passing it through would skip the
  output-side controls.
- `python -m prismal.eval run --live-api` never exited after writing its
  results. The async graph's SQLite checkpointer (`aiosqlite`, whose worker
  thread is non-daemon) was never closed. The new
  `prismal.agents.graph.aclose_async_compiled_graph()` closes every
  checkpointer `build_checkpointer()` opened and resets the singleton, and the
  eval CLI calls it after a run. Hosts should call it at shutdown.
- Supervisor with reasoning models (qwen3 via Ollama, extended thinking): the
  routing reply arrived as content blocks (`thinking` + `text`) and was
  stringified whole, so every turn logged `supervisor_invalid_routing` and fell
  back to `END`, and direct answers could leak the reasoning. Only `text`
  blocks (and text outside inline `<think>…</think>`) are used now.
- Eval trajectories recorded 0 tokens on live runs: the supervisor's internal
  routing calls and rebuilt answers carry no usage in the update stream.
  `capture_trajectory` now counts every LLM call of the run through a LangChain
  callback (`Trajectory.usage_source`), and the token baseline
  `tests/eval/baselines/tokens.json` is committed (TKE1-08; smoke suite, local
  Ollama `qwen3:14b`). TKE9-04 (A/B with the TKE flags on) is unblocked.

### Changed
- OpenCode defaults follow the 2026-10 docs: `opencode_zen_base_url` =
  `https://opencode.ai/inference/v1`, `opencode_go_base_url` =
  `https://opencode.ai/inference/go/v1` (the old `/zen/...` bases still answer
  but are undocumented). `config/opencode_models.yaml` regenerated from the
  docs' tables: Zen adds `claude-haiku-5-5`, `claude-sonnet-5-5`,
  `mistral-large-4`, `exo-free`, `step-5-preview-free` and drops
  `fledge-alpha-free`; Go adds `claude-haiku-5-5`, `space-bunny`,
  `step-5-preview-free` and drops `space-bunny-free`.

### Security
- OpenCode deny-list (`opencode_denied_models`) now also refuses the free
  tiers whose data may train the upstream model and NVIDIA's "trial use only"
  endpoints: `big-pickle`, `exo-free`, `mimo-*-free`, `ling-*-free`,
  `nemotron-*-free` (VERIFY.md GW0-06, re-verified 2026-10-12).
- `mako` 1.3.12 → 1.4.3 (lock; transitive via `alembic` / `prefect`) — fixes
  CVE-2026-102991 (GHSA-5639-2j2p-m4mx, `TemplateLookup` path traversal).
- `accelerate` 1.13.0 → 1.15.0 and floor `accelerate>=1.15.0` in
  `[local-embeddings]` — fixes CVE-2026-69112 (GHSA-4j2p-28q2-5m79, sharded
  checkpoint path traversal); its pip-audit ignore is removed.
- `chromadb` 1.5.9 (CVE-2026-45829/45830/45831/45833) and `nltk` 3.10.3
  (CVE-2026-81726) re-verified on OSV 2026-10-11: still no upstream fix, both
  are the latest releases. The ignores stay because prismal is not exposed:
  Chroma runs embedded only (no HTTP server/RBAC) and nothing imports `nltk`,
  which arrives only through `llmlingua` in the optional `[compression]` extra.
  Next review: 2027-01. That justification is now enforced by
  `tests/unit/security/test_unpatched_dependency_surface.py` (AST guard: no
  `nltk` import, no Chroma HTTP/cloud client or server settings, the adapter
  persists to a local directory), and Dependabot alerts #49/#50/#51/#101 are
  dismissed as tolerable risk.

## [3.19.0] — 2026-10-11

> Typed decisions on a **local Ollama** backend (DEC delta
> `specs/typed-decision-models/DELTA-2026-10-ollama-backend.md`, PHASE DEC7) and a **one-way
> human-review gate** built from concrete questions. Everything stays opt-in behind
> `decisions_enabled` (default `False`): with it off the supervisor graph is unchanged.
> Guide: `docs/decisions.md` (Ollama backend, Human-review gate, Behaviour changes).

### Added
- `decisions/adapters/ollama.py`: Ollama `/v1/systemone` backend as two sub-tiers, `ollama:fast`
  (default `tev1:4b`) then `ollama:accurate` (default `nimble:9b-q4_K_M`) (OLL-001, CAS-001).
  Lazy per-process preflight (`/api/version` ≥ `decision_ollama_min_version`, `decision`
  capability, served `num_ctx`, local digest; refusals escalate, transport errors retry after
  30 s, successful preflights re-checked every 60 s); `Decision.model = "<name>@<digest12>"`;
  `keep_alive` on every request and an opt-in warm-up (`DecisionRuntime.warmup()`, called by
  `build_runtime`). Limits before sending (options/levels 2–26, ≤ 64 questions, ≤ 64 KiB, tokens
  ≤ 90 % of `num_ctx`; never truncates); HTTP 400 / timeout abstain without retry. Tier routing
  by integration (per-turn ⇒ `fast`; `eval_judge`/`hitl`/`swarm` ⇒ `accurate`; oversize per-turn
  ⇒ `accurate` only under its rolling-p95 limit). One question per request on `fast`, batches of
  ≤ `decision_ollama_max_batch` on `accurate` with auto-disable from a batch-accuracy record.
- Settings: `decision_ollama_base_url`, `decision_ollama_models`, `decision_ollama_keep_alive`,
  `decision_ollama_warmup`, `decision_ollama_allow_remote`, `decision_ollama_accurate_max_p95_ms`,
  `decision_ollama_max_batch`, `decision_ollama_min_version`, `decision_ollama_timeout_ms` (own
  per-sub-tier budget, not charged to `decision_timeout_ms`), `decision_hitl_raise_threshold`,
  `decision_hitl_raise_thresholds`, `decision_hitl_money_limit`; `core.config.is_loopback_url`.
- `DecisionCascade.decide(observe=...)`: receives every raw, non-abstained tier answer.
- Calibration keyed by local digest (`<decision_id>@<name>@<digest12>.json`): a stale digest runs
  uncalibrated (escalates) and counts `prismal.decision_calibration_stale_total`; `enforce`
  startup accepts a valid record for any digest of each configured Ollama model.
- Metrics `prismal.decision_calibration_stale_total`,
  `prismal.decision_ollama_preflight_refused_total{tier,reason}`,
  `prismal.decision_hitl_raised_total{question}`.
- Opt-in live contract test `tests/live/decisions/test_ollama_contract.py` (marker `ollama`,
  `PRISMAL_OLLAMA_CONTRACT=1`).

### Changed
- **DEC-HITL** (`hitl_band`) no longer asks the abstract "approve without a human?" question
  (every Ollama model was persuaded by injection text on it). It asks five one-fact questions and
  derives review as a deterministic OR with a rule tier (injection/taint, EN/ES destructive and
  money keywords, trusted `GatedAction.amount` vs the limit). Models may raise review — stickily,
  across tiers — but never clear it alone: `ollama:fast` / `llm` negatives give at most `confirm`
  (OLL-012/014). New optional `GatedAction.amount`; `HITL_DECISION_ID` is a deprecated alias.
  A host-wide cascade `rule_hook` never answers HITL questions, and a `rule`-tier "no" can
  never clear review (found by the post-commit security review).
- `decision_backend_order` default is now `["rule","ollama","jev","llm"]` (`laya` stays valid).
- A non-loopback Ollama is refused unless `decision_ollama_allow_remote=true`: an explicit
  `decision_ollama_base_url` fails `Settings` load; any other route (LLM URL fallback, tenant
  override) builds no Ollama tier.
- Calibration file names encode `/` and `\` in model names (they can no longer leave the directory).
- CI: `complexity-radon` and `ty` are green (`solve_dual` split; 62 → 0 `ty` diagnostics via real
  type fixes, no suppressions); `specs/*/bench` excluded from ruff.

### Behaviour changes (DEC users only)
- Deployments relying on the old default order must set `decision_backend_order` to keep Laya.
- `hitl` in `enforce` needs calibration records for the five concrete question ids.
- Laya's `laya_preload` warm-up no longer warms Ollama.

## [3.18.0] — 2026-10-07

> **Release note (tag `prismal/v3.18.0`, 2026-10-10):** this tag ships the whole wave
> 2026-10 at once — 3.13.0 (TKE), 3.14.0 (GW), 3.15.0 (DEC), 3.16.0 (AMR),
> 3.17.0 (PRT + HRS) and 3.18.0 (MEM). The intermediate versions were never
> tagged or published on their own; their changes are listed in their own
> sections of `CHANGELOG.md`. Every new layer is opt-in and off by default.

> Phase MEM — **Agentic memory v2**, the sixth and last in-repo milestone of
> wave 2026-10. Typed (episodic / semantic / procedural / preference),
> decision-gated, budgeted and tenant-scoped memory in `prismal/memory/v2/`. It
> runs over the **same** legacy stores, which are wrapped and never edited.
> Writes and recall follow the Jev-Mem controller design. A sleep-time job
> consolidates memory without ever deleting it. Deletion is reachable only from
> a retention rule or an approved human. Opt-in via `memory_v2_enabled` (default
> `False`, `memory_mode="shadow"` first): with it off, the supervisor graph is
> identical (snapshot-tested), `RuntimeContext.memory` is `None` and no
> scheduler job is registered. Guide, migration path and MEM0 verified facts:
> `docs/memory.md`.

### Added
- **Port + service** (`memory/v2/{types,ports,service}.py`): `MemoryPort`
  (`write` / `recall` / `consolidate` / `forget`), `MemoryBackendPort`,
  `MemoryGraphPort`. `MemoryService` serves one tenant, built by
  `build_memory_service(settings, org_id=, ...)` and exposed as
  `RuntimeContext.memory` by `build_runtime(org_id=, memory_backend=,
  memory_graph=)`. Any other org's scope raises `MemoryTenancyError`, which is
  counted and audited hash-first.
- **Backends over the legacy stores** (`memory/v2/adapters/`):
  - `LongTermMemoryBackend`: v2 fields go in a `memory_v2_meta` side table, and
    v2 writes stay visible to the unchanged `LongTermMemory.recall()`.
  - `LangGraphStoreBackend`: tenant namespaces
    `("prismal", org, user|"_", type)`.
  - `InMemoryBackend`.
  - Legacy-shaped facades (destructive methods file a pending forget request)
    and an idempotent, annotate-only `migrate_legacy_records` backfill.
- **Decision-gated writes** (`write_gate.py`, `taint.py`):
  - Pre-filters: PII redaction, length bounds, exact-hash dedupe ⇒ `merged`.
  - Worth (Noul) + type (Choice) in one decision call, then a batched relation
    Choice over `memory_relation_k` neighbours. `UPDATES` supersedes only when
    T6 allows.
  - Gate modes `off` / `decision` / `llm`; shadow mode; hash-first audit.
  - Taint rules T1–T7: tainted content is stored only as an `EPISODIC` episode
    and never supersedes or is promoted.
- **Budgeted recall** (`recall.py`, `render.py`):
  - View routing; multi-hop retrieval ranked by `traversal_score`; optional
    temporal-graph neighbours with `as_of`.
  - A sufficiency Noul and five stop reasons.
  - A hard token cap (property-tested).
  - TOON or compact rendering with `[untrusted]` marks. `memory_block_for_prompt`
    renders a fenced block for the tail turn context, never the cached prefix.
- **Sleep-time consolidation** (`consolidation.py`):
  - Duplicate merge.
  - Newest-wins contradiction resolution with provenance precedence
    (human > trusted > unknown > untrusted).
  - Episodic → semantic summaries via an injected summarizer or `BatchExecutor`.
  - Retention rules applied through `ForgetPolicy`.
  - A watermark, the item cap, and idempotent re-runs.
  - `register_consolidation_job` registers one per-org cron job.
- **Forget** (`forget.py`):
  - `ForgetPolicy` accepts only a retention rule (selectors issued by
    `retention_rules()`, single use) or an approved, single-use human request.
    Any other actor gets `MemoryForgetDenied`.
  - `include_derived` also erases summaries, edges, graph nodes and playbook
    bullets.
  - The agent-facing tool `request_memory_forget` only files pending requests.
- **Playbooks** (`playbooks.py`): ACE generate / reflect / curate with deltas
  only.
  - Rejected: more than 50 % rewrite, more than `memory_playbook_max_deltas`,
    and uncited or tainted sources.
  - Versioned rollback; per-run pinning (stable bytes in the prefix).
- **Temporal graph** (`graph/`): `SqliteMemoryGraph` (stdlib; edges are
  invalidated, never deleted; `as_of`) and `GraphitiMemoryGraph` (new optional
  extra `[graph-memory]` = `graphiti-core>=0.30.2,<0.31`, client always
  injected).
- **DEC bridge** (`decision_bridge.py`): `CascadeDecisionPort` lets the runtime's
  `DecisionCascade` serve the memory controllers (integration `memory`). It
  re-fences and re-scans every state field, so DEC's fail-closed taint rules
  still apply, and turns errors into abstentions.
- **Settings** `# --- MEM ---` (`memory_v2_enabled`, `memory_mode`,
  `memory_v2_backend`, `memory_write_gate`, recall / consolidation / retention /
  graph / playbook knobs) with `_validate_memory`. **Exceptions**
  `AgenticMemoryError` → `MemoryConfigError`, `MemoryTenancyError`,
  `MemoryForgetDenied`, `MemoryBackendUnavailable`, `PlaybookDeltaError`.
  **OTel**: 6 counters, `prismal.memory_recall_tokens`, and spans
  `prismal.memory.{write,recall,consolidate}`.
- **Eval** (`prismal/eval/memory_locomo.py`): a seeded, MIT-licensed
  LoCoMo-style generator (LoCoMo itself is CC BY-NC and is not vendored) and a
  legacy vs v2 report. On 5 × 200 sessions, the deterministic layers alone give
  +20 pts QA accuracy, −89 % memory tokens vs full history and −95 % stored
  records. All PLAN §4 targets are met (`tests/eval/test_memory_locomo_subset.py`).
- Security tests (`tests/security/test_memory_security.py`):
  - Injected "the admin approved deletion" content is inert.
  - Two-tenant isolation across all three backends and the graph.
  - Erasure completeness.
- An AST guard: only `forget.py` may call `.purge(`.
- `docs/memory.md`, `examples/agentic_memory.py`.

### Changed
- `_validate_memory` checks `memory_consolidation_cron` syntactically (five
  crontab fields) instead of importing APScheduler at settings load.
- `build_runtime` gains the keyword-only `memory_backend=`, `memory_graph=` and
  `memory_hooks=` parameters (frozen-signature test updated deliberately).
  `MemoryHooks` carries the host callables (LLM classifier, summarizer, batch
  executor, playbook curator). An `llm` gate without a classifier fails at
  composition. Memory v2 keeps its SQLite files per tenant under
  `memory_v2_data_dir`, never in the shared legacy `memory.db`.

### Notes
- Not yet measured: decision-gated eval numbers with a calibrated DEC backend.
- Not yet wired: agent adoption of `memory_block_for_prompt` (library seam only). *(Wired for the `researcher` in [Unreleased].)*
- The notebook is still to come.
- Graphiti sends anonymous telemetry unless `GRAPHITI_TELEMETRY_ENABLED=false`.

### Changed (wave APIs, before their first publication)
- `react_loop` takes its optional layers as `**options: Unpack[ReactLoopOptions]`
  (a `TypedDict`, type-checked by mypy at every call site). Every existing
  keyword and the `**hardening_react_kwargs(state)` / `tool_gate_react_kwargs`
  splats work unchanged; an unknown keyword still raises `TypeError`. The loop
  body is split into helpers (no behaviour change).
- A2A OAuth flows (`PkceAuthorizationCodeFlow`, `DeviceCodeFlow`,
  `select_oauth_flow`) and `A2AClient` take `policy=EndpointPolicy(...)`
  instead of the four flat host-policy flags, and the flows take
  `binding=OAuthBinding(...)` instead of `vault` / `org_id` / `principal` /
  `http_client` / `clock` / `audit`. `A2AConnectionManager` keeps its signature.
  As before, the card's OAuth endpoints get only the client's `allowlist` /
  `strict`, never its localhost / private-host opt-ins (regression-tested).

### Security
- Dependency refresh (2026-10-10) clearing every pip-audit finding and every
  trivy HIGH/CRITICAL finding that has an upstream fix: `aiohttp` 3.14.4, `anyio` 4.14.2,
  `cryptography` 50.0.0, `pyjwt` 2.15.1, `litellm` 1.96.2 (+ `langchain-litellm`
  0.7.1, `langchain-core` 1.6.9), `urllib3` 2.8.0, `pypdf` 6.20.0,
  `sentence-transformers` 5.6.0, `unstructured` 0.24.0, `pymongo` 4.18.2,
  `langgraph-checkpoint-sqlite`/`-postgres` 3.1.1, `lightning` 2.6.6, plus
  transitive and toolchain packages. Direct-dependency floors raised accordingly.
  GW0-03 (`ChatLiteLLM` hooks) re-verified on `langchain-litellm` 0.7.1.
- Accepted without an upstream fix (justified in `.trivyignore`, mirrored in
  `ci.yml` and `.pre-commit-config.yaml`): `chromadb` CVE-2026-45830/45831/45833
  (Chroma *server* RBAC / code injection; prismal uses Chroma embedded),
  `nltk` CVE-2026-81726 and `accelerate` CVE-2026-69112 (transitive via the
  optional `[compression]` extra; affected APIs unused).

### Fixed
- Release build: `twine` 7.0.0 (+ `packaging` 26.2) in the lock — `twine` 6.2.0
  rejected the Metadata-Version 2.5 that current `hatchling` writes, failing
  `twine check` in `release.yml` before anything was published.
- Release publish: wheel and sdist pin `core-metadata-version = "2.4"`, because the
  pinned `pypa/gh-action-pypi-publish` bundles its own `twine` that also rejects 2.5.
- CI (`release.yml`): the `github-release` job now retries with backoff and
  only trusts a `gh release view` "not found", instead of failing the whole
  release on a transient GitHub 5xx. During the 2026-07-19 GitHub Actions
  incident a 503 on the asset upload made `gh release create` roll back the
  half-made release, so v3.12.0 shipped to PyPI and GHCR without a GitHub
  Release.

## [3.17.0] — 2026-10-06

> Phase PRT + HRS — **Protocol Upgrades 2026** and the **host read surface**,
> the fifth milestone of wave 2026-10. A2A moves to a **v0.3 + v1.0
> dual-stack**, MCP gains the **2026-07-28 stateless** wire, a TTL tool-list
> cache and the **tasks extension** (in-house on `httpx`; the `mcp` SDK stays
> pinned at 1.28.1), and a pure **AG-UI 1.0** event translator lands in
> `prismal/events/agui.py`. A shared 12-key **usage wire** and read-only
> `RuntimeContext` methods give hosts (prismal-server) budget, quota, audit,
> decision and routing views. Every switch defaults off: the graph, the v0.3
> Agent Card and the MCP request sequence equal 3.16.0 (snapshot-tested).
> Wire names are pinned in `docs/adr/ADR-PRT-001-protocol-pins.md`; guide:
> `docs/protocols-2026.md`.

### Added
- **A2A v1.0 dual-stack** (`prismal/a2a/`): `v1/{constants,types}.py` (PascalCase
  methods, `TASK_STATE_*`, `Part` oneof, `supportedInterfaces[]`),
  `negotiation.py` (`A2A-Version` header ⇒ method family ⇒ header-less 0.3;
  unsupported ⇒ -32009), `convert.py` (v0.3 ⇄ v1 ⇄ internal codecs),
  `build_agent_card(..., protocol_version=)`, `a2a_route_paths()`,
  `task_store.py` (`A2ATaskStorePort`, `InMemoryA2ATaskStore`, `ListTasks`
  paging max 100), `oauth.py` (PKCE authorization code, device code, client
  credentials) and `A2AClient(protocol=, tenant=, local_org_id=, ...)` +
  `list_tasks()`. `A2AServerHandler.handle_rpc/stream_rpc(headers=, org_id=)`;
  `headers=None` keeps 3.16.0 behaviour.
- **MCP 2026-07-28** (`prismal/mcp/`): `constants.py`, `protocol.py`
  (`LegacySessionAdapter`, `StatelessHttpAdapter` — `_meta` + `MCP-Protocol-Version`
  / `Mcp-Method` / `Mcp-Name` headers, pagination, multi-round-trip input —,
  `select_adapter` / `negotiate_stateless` with a per-server memo),
  `tool_cache.py` (`McpToolListCache`: `ttlMs`, `cacheScope`, canonical hash,
  identity-preserving refresh, LRU), `tasks.py` (`McpTaskPoller`: backoff,
  timeout ⇒ cancel + `McpTaskTimeout`, run cancellation ⇒ `tasks/cancel`),
  `input_requests.py` (`InputRequestHandler`, `DeclineAllInputHandler`).
  `MCPServerConnection` selects the adapter on connect from
  `mcp_protocol_mode` (`legacy` default | `stateless` | `auto`);
  `McpToolProvider(settings=, tool_cache=, org_id=, principal=)`.
- **AG-UI 1.0 translator** (`prismal/events/agui.py`): 31 event types, Pydantic
  models, `to_sse()`, `AgUiContext`, `start_run` / `to_agui_events` /
  `finish_run` / `error_run` (never raise); interrupts ⇒
  `RUN_FINISHED.outcome`, usage ⇒ `CUSTOM prismal.usage` + native
  `RUN_FINISHED.usage[]`; 12 golden transcripts; `examples/agui_translate.py`.
- **Usage wire.** `RunSummary.to_usage_wire()` (12 keys) and
  `prismal.monitoring.get_run_summary(session_id, turn="last"|"all")`;
  contract fixture `tests/fixtures/protocols/usage_wire.json`.
- **Host read surface (HRS)** (`prismal/composition/host_surface.py`,
  `prismal/monitoring/host_stats.py`): `RuntimeContext.budget_status()`,
  `quota_windows()`, `audit_page()`, `verify_audit_chain()`,
  `decisions_report()`, `routing_report()`, `a2a_routes()` (route ⇔ card
  contract), `bind_org()` (tenant attribution for the stats recorders) — fail-open, frozen view types re-exported from
  `prismal.composition`; in-process 1-minute stats recorders fed by an
  `OTelManager.increment_counter` tap; `build_runtime(config_source=)` builds a
  per-runtime `Settings` without touching the global source or `os.environ`.
- **Settings** (`# --- PRT ---`): `a2a_protocol_version`, `a2a_accept_legacy`,
  `a2a_tasks_list_max_page_size`, `a2a_inbound_auth_schemes`,
  `a2a_oauth_redirect_uri`, `mcp_protocol_mode`, `mcp_stateless_protocol_version`,
  `mcp_tool_cache_*`, `mcp_tasks_enabled`, `mcp_task_poll_*`,
  `mcp_task_timeout_s`, `agui_events_enabled`, `agui_include_*`,
  `host_surface_stats_retention_h`; `_validate_protocol_upgrades`.
- **Exceptions**: `ProtocolUpgradeError`, `A2AVersionError`, `A2AOAuthError`,
  `McpProtocolError`, `McpDiscoveryError`, `McpTaskError`, `McpTaskTimeout`,
  `AgUiTranslationError`.
- **Metrics**: `prismal.a2a_requests_total`, `a2a_oauth_tokens_total`,
  `mcp_protocol_negotiations_total`, `mcp_tool_cache_{hits,misses}_total`,
  `mcp_tool_list_changes_total`, `mcp_tasks_total`, `mcp_input_requests_total`,
  `agui_events_total`, `agui_translation_errors_total`,
  `host_surface_errors_total`, histogram `mcp_task_wait_s`.
- **Interop** (opt-in `interop` marker, `PRISMAL_INTEROP=1`): AG-UI 1.0 schema
  over the golden transcripts, `a2a-tck`, `@modelcontextprotocol/conformance`.

### Security
- A2A: a `SendMessage` reusing a task id owned by another org or principal
  answers TaskNotFound (v1 -32001, v0.3 -32004) and is audited
  (`a2a.inbound.tenant_mismatch`); ownership is reserved under a lock before
  the graph runs and scoped runs use a per-owner graph thread id, so records are
  never overwritten across tenants.
- A2A OAuth: refresh tokens are keyed by token-endpoint origin + agent URL in
  the **local** org's vault partition (never a remote-card name or tenant),
  bound to their `token_url`; endpoints must be `https`, literal private /
  loopback / link-local IPs are rejected, and `a2a_strict` requires the
  outbound allowlist.
- MCP: a task result is accepted only when the client declared the tasks
  extension (else cancelled + `McpTaskError`); stateless mode declines
  sampling / roots; elicitations default to decline + hash audit.

### Changed
- `prismal/mcp/connection.py` opens streamable-HTTP with
  `streamable_http_client(http_client=...)` (the deprecated
  `streamablehttp_client` wrapper is gone; same client configuration).

### Known limits
- No component emits the AG-UI `subagent` custom payload yet, so Skynet / MoA
  runs show `STEP_*` but no `SUBAGENT_*` events.
- OAuth endpoint checks do not resolve DNS: outside `a2a_strict` with no
  allowlist, a hostname resolving to an internal address is not caught.
- The `mcp` 2.x SDK migration and gRPC / HTTP+JSON A2A bindings are out of scope.

## [3.16.0] — 2026-10-06

> Phase AMR — **Adaptive Model Routing**, the fourth phase of wave 2026-10.
> A `RoutingPolicyPort` picks the model **string** per agent call (ADR-WAVE-001
> step 1) — static tier map, budget-aware downgrade, cheapest-first cascade with
> typed adequacy checks — plus an OmniRouter-style batch assigner for fan-outs,
> an intent-keyed plan cache, an exact response cache and a provider
> `BatchExecutor`. Prismal picks *which model*; gateways pick *which provider*.
> Everything is gated by `adaptive_routing_enabled` (default `False`) and starts
> in `shadow`; with it off, `get_llm()` builds the 3.15.0 `ChatLiteLLM`
> arguments and the supervisor graph is unchanged (contract + snapshot tests).
> Guide: `docs/routing.md` (AMR0 verified facts in its `#verified-facts` section).

### Added
- **Types and ports.** `prismal/routing/types.py` (`ModelTier`, `TIER_ORDER` —
  `auto` is terminal —, `TaskFeatures`, `ModelChoice`, `RoutingOutcome`) and
  `routing/ports.py` (`RoutingPolicyPort`, `SuccessPredictorPort`).
- **Policies** (`routing/policies/`): `StaticTierPolicy` + `load_tier_models`
  with the shipped **parity map** `config/routing_tiers.yaml` (all tiers
  `null` ⇒ `default_model`, so 26+ agents keep their 3.15.0 model);
  `BudgetAwarePolicy` (one-tier downgrade on the soft cap, never below the risk
  floor, never on a tainted turn; hard caps untouched); `CascadePolicy` +
  `AdequacyChecker` (start tier, capability filter, escalation on structured
  failure / inadequate answer / tool errors / high-risk abstention, cap 2;
  adequacy is a DEC Noul over a `DecisionStateBuilder` state);
  `CompositePolicy` (`BudgetAware(Cascade(Static))`, inward fallback,
  first-escalation-wins).
- **Per-run selection** (`routing/select.py`, `routing/features.py`):
  deterministic LLM-free features (intent, token estimate, agent risk map,
  language, turn taint), seeding trio mirroring `budget/resolve.py`, same-turn
  stickiness + cross-turn **escalation affinity** (no re-descent), fail-open
  policies (`fallback:<err>`), fail-closed **security floors** (risk floor,
  no `auto` for critical, tainted never below static), `shadow` mode,
  `prismal.routing_savings_usd` vs a frontier counterfactual.
- **Registry.** `ProviderRegistry.get_llm` / `get_llm_with_fallback` gain
  keyword-only `features=` / `state=` (ADR-WAVE-001 §2.1): explicit `model=`
  wins; under a cascade a `gw:` call carries no gateway `models[]` and no
  `settings.fallback_model` (SPEC-AMR-GW-001). The shared registry contract
  test gained the AMR flag-off, parity, shadow and fail-open cases.
- **Agents.** `coder`, `researcher`, `data_analyst`, `file_manager`,
  `cron_manager`, `critic` adopt `llm_route_kwargs()` + `track_outcome()`
  (`routing/outcome.py`; `react_loop` notes failed tool calls); `rag_agent`,
  `planner`, `parallel_researcher`, `codeact` adopt the model choice only. All
  calls are byte-identical with routing off.
- **Batch assignment** (`routing/policies/batch_assignment.py`,
  `routing/predictor.py`): `TableSuccessPredictor` (Beta-Binomial per
  (intent, tier), served-model attribution, output-length table,
  `load_predictor_table`, `export_predictor_table`, cold-start
  `config/routing_predictor/v1.json`); `BatchAssignmentPolicy.assign()` —
  Lagrangian dual with primal recovery, greedy fallback, max-accuracy
  `feasible=False` path, waves, eligibility masks; deterministic, never raises
  (N=200, K=8 p95 ≈ 40 ms). `routing/fanout.py` wires it into **Skynet**
  (`SkynetSupervisor(batch_policy=)`, `order.context["routing_model"]`) and
  **MoA** (proposers assigned, aggregator static).
- **Plan cache and response cache** (`routing/plan_cache.py`,
  `routing/response_cache.py`): canonical intent keys with tenant, tool-scope
  hash and prompt version; memory LRU and SQLite backends; read-only intent
  allowlist, no write/exec tools, untainted input; small-tier adaptation via
  `SecurePromptBuilder` + Kahn DAG re-validation; `LLMCompiler(plan_cache=)`.
- **BatchExecutor** (`routing/batch.py`, `providers/batch_adapters.py`):
  Anthropic Message Batches and OpenRouter `:batch` (raw `httpx`), OpenAI
  Batch (LiteLLM); capped exponential backoff with jitter, deadline, sync
  fallback; tainted / HITL-gated / data-policy-rejected requests never
  uploaded. `compute_cost_usd(..., batch=True)` applies `batch_price_ratio`
  (ADR-WAVE-001 §2.3).
- **Eval.** `python -m prismal.eval run --routing <policy>` and
  `routing-gate --static --routed` (−40 % cost / ≤ 1 pt quality target;
  `prismal/eval/metrics_routing.py`).
- **Settings** (`core/config.py`, `# --- AMR ---`): `adaptive_routing_enabled`,
  `routing_*`, `plan_cache_*`, `response_cache_enabled`, `batch_*`,
  `batch_price_ratio`; `_validate_routing` (refuses to start without a
  `critical` floor when enabled). Exceptions: `RoutingError`,
  `RoutingConfigError`, `BatchAssignmentError`, `PlanCacheError`,
  `BatchExecutorError`. OTel: `prismal.routing_decisions_total`,
  `routing_escalations_total`, `plan_cache_hits_total` / `misses_total`,
  `batch_jobs_total`, `routing_savings_usd`; span `prismal.routing.select`.
- Docs `docs/routing.md`, example `examples/adaptive_routing.py`.

### Security
- An independent review before release found and fixed (each with a regression
  test): floors skipped for same-turn sticky choices; a shared process-wide
  bucket for session-less calls; fan-outs ignoring taint, the YAML floor and
  gateway data policy; `metadata.routing` hints able to lower risk and forged
  affinity accepted; the settings floor default silently overriding a stricter
  YAML floor; OpenRouter batch bodies without privacy defaults; API keys sent
  to provider-returned `results_url` hosts; routing newly activating the
  gateway agent map in shadow; transport errors escalating the tier; cascade
  features colliding across agents; double billing when batch results fail;
  plans cached before execution and shared across users of one org.

### Notes
- AMR0 changed two spec assumptions: LiteLLM 1.86.2 cannot create Anthropic
  batches (raw `httpx` adapter instead) and arXiv 2602.18922 reports APC
  *cache accuracy* 0–12 %, not a semantic-cache hit rate.
- Open: the eval-gate measurement on a real eval set and the notebook.

## [3.15.0] — 2026-10-05

> Phase DEC — **Typed Decision Models**, the third phase of wave 2026-10.
> Closed questions (Choice / Score / Noul) are answered by a cascade of typed
> decision models — a deterministic rule hook, **Laya** (local, Apache-2.0) and
> **Jev** (TypeSafe API, OpenRouter or `laya-serve`) — before falling back to
> today's LLM path. A decision *proposes*; deterministic code *authorizes*.
> Everything is gated by `decisions_enabled` (default `False`) plus one flag
> per integration, and every integration starts in `shadow`. With it off, the
> compiled supervisor graph and node outputs are identical to 3.14.0 (graph
> snapshot test). Guide: `docs/decisions.md` (verified vendor facts in its
> `#verified-facts` section).

### Added
- **Types and port.** `prismal/decisions/`:
  - `types.py`: `QuestionSpec` (+ `schema_hash`), `Decision` (calibrated
    `answer_confidence`; vendor confidence kept only as `spread_confidence`),
    `GatePolicy` (τ low .85 / medium .90 / high .97 / critical never; raise-only overrides).
  - `ports.py`: `DecisionPort` plus `guarded_decide` (never raises: errors,
    timeouts and over-limit input become abstentions).
  - `state.py`: `DecisionStateBuilder` — the decision analogue of
    `SecurePromptBuilder`: sanitized, fenced, labelled untrusted fields; EN/ES
    authority-claim and injection flags taint the state.
  - `validation.py`: registration-time lint for forbidden uses (counting,
    arithmetic, dates; EN/ES) and `FORBIDDEN_DECISION_USES`.
  - `gate.py`: `evaluate_gate` and a derangement option shuffle against
    first-option bias.
- **Cascade** (`cascade.py`): rule → laya → jev → llm, one shared non-LLM
  time budget, calibration application, taint corroboration, per-language
  gating, hash-only `decision.made` audit records, OTel metrics.
- **Backends** (`decisions/adapters/`): Jev-compatible `/systemone` HTTP
  (TypeSafe, OpenRouter, laya-serve incl. `/systemone/batch`; model-mismatch
  abstention; usage metered in `CostMeter` with `cost_source="decision"`),
  in-process Laya (deferred import, `to_thread`, fp16 warning), Jev SDK shim,
  LLM fallback (structured output through `SecurePromptBuilder`), fake. New
  extras `[laya]` and `[jev]`.
- **Calibration** (`calibration.py`): temperature scaling (Laya) / isotonic
  map (Jev), ECE, τ fitting, model-pinned `CalibrationStore`. `enforce`
  without a valid record (or with ECE above `decision_max_ece`) refuses to
  start. A Spanish slice must pass on its own before `es` traffic enforces.
- **Shadow mode** (`shadow.py`): `ShadowRunner`, `AgreementReport`,
  `promotion_ready`, drift auto-demotion to shadow.
- **Integrations**:
  - Wired in-graph: supervisor routing after `match_intent()` (DEC-ROUTE),
    CRAG per-chunk relevance (DEC-RAG), and an escalate-only guardrail
    pre-screen through `security/decision_bridge.py` (DEC-SAFE).
  - Library functions: the eval-judge cascade (DEC-EVAL; never for
    math/code/logic rubrics; 5 % LLM audit), HITL bands (irreversible or
    critical actions always go to a human), tool proposal, a compaction
    relevance scorer, and a Skynet text scorer.
- **Composition**: `build_runtime(..., decision_vault=)` → `RuntimeContext.decisions`
  (tenant keys `jev_api_key@<org_id>`); `use_decision_runtime()` binds it per
  request in `context` mode.
- **CLI**: `python -m prismal.decisions calibrate` (labeled JSONL → calibration
  file) and `bench` (live, opt-in latency / cost).
- Settings block `# --- DEC ---` + `_validate_decisions`; `Decision*`
  exceptions; `_register_dec_metrics()`; example `examples/typed_decisions.py`.

### Security
- EN/ES injection corpus (seeded with Octomind's "pre-approved" attack)
  asserts zero forbidden transitions against a fully manipulated backend. Also
  covered: option-order shuffle, forbidden-use AST and behaviour tests, and a
  secret-leak test.
- An independent review found these, all fixed with regression tests:
  - The terminal LLM tier ignored taint.
  - HITL could `auto`-approve on an LLM verdict.
  - Nested fence markers could escape the fence.
  - `context` mode silently built an unaudited global runtime.
  - Shadow recording was quadratic.
  - Non-finite probabilities or calibration values were accepted.
  - Unsafe MCP tool names were accepted.

## [3.14.0] — 2026-10-05

> Phase GW — **LLM Gateway Providers**, the second phase of wave 2026-10.
> Agents can route through OpenRouter, OpenCode Zen/Go, OmniRoute or any
> OpenAI-compatible gateway. LiteLLM stays the only transport. Everything is
> gated by `llm_gateways_enabled` (default `False`) plus one `*_enabled` flag
> per gateway. With it off, the compiled supervisor graph and the kwargs of
> every `ProviderRegistry.get_llm()` call are identical to 3.13.0 (graph
> snapshot test plus the shared registry contract test
> `tests/unit/providers/test_registry_contract.py`, created here as ADR-WAVE-001
> requires). Guide: `docs/gateways.md`. Verified vendor facts:
> `specs/llm-gateway-providers/VERIFY.md`.

### Added
- **Gateway port and router.** `prismal/providers/gateways/`:
  - `base.py` / `policy.py`: value objects, `GatewayPort`, `gw:` reference grammar.
  - `openai_compatible.py`: the generic adapter, shared by the others.
  - `router.py`: `GatewayRouter`, `build_gateway_router`, and
    `get/set/use_gateway_router` (ContextVar first, then the process global).
- **`GatewayChatLiteLLM`** (`chat_model.py`). The innermost model in the
  ADR-WAVE-001 stack. It captures served model, generation id, billed cost and
  cost mode into `response_metadata["prismal_gateway"]`, for sync, async and
  streaming calls. It maps HTTP 402 to a non-retryable `GatewayPaymentRequired`,
  runs the quota preflight before any network call, and hides the key in
  `repr`.
- **Registry.** `ProviderRegistry(gateway_router=)` and
  `get_llm(..., session_id=)`. **No double fallback**: a gateway that owns
  model fallback returns the primary unwrapped with `max_retries=0`, and a
  same-gateway fallback never retries 402 or policy errors.
- **OpenRouter** (`openrouter.py`):
  - Attribution headers.
  - Privacy defaults `provider.data_collection="deny"` and `provider.zdr=true`.
  - `require_parameters` when tools are bound.
  - A hashed sticky `session_id`.
  - Gateway-owned `models[]` fallback.
  - Variant allow-list and a gated `openrouter/auto` (`auto-router` plugin).
  - A plugin allow-list.
  - `/api/v1/key` quotas and `/api/v1/generation` stats.
- **OpenCode Zen/Go** (`opencode.py`, `config/opencode_models.yaml`):
  - A per-model wire-protocol map (`openai/`, `anthropic/`, `openai/responses/`).
  - `User-Agent` and a hashed `x-opencode-session`.
  - The Go coding-agent ToS guard and a retention deny-list.
  - Go meters as `subscription`, with a downgrade target.
  - `refresh_protocols()` from `/v1/models`.
- **QuotaLedger** (`quota.py`). Go 5 h / weekly / monthly windows and the
  OpenRouter `:free` daily request window, scoped per `(org, gateway)`, with a
  fake-clock-testable store.
- **OmniRoute** (`omniroute.py`). A fail-closed startup gate (mandatory key,
  upstream attestation, version floor), a combo deny-list, and a warning for a
  public plain-HTTP base URL. `resolve()` refuses until the gate passes.
- **Cost.**
  - `compute_cost_usd(..., cost_mode=)`: `"subscription"` returns
    `cost_usd=0` and `CostEstimate.equivalent_usd`.
  - `CostMeter` reads gateway metadata and tags OTel with `gateway` /
    `served_model`. It records ledger usage and schedules reconciliation.
  - `Usage` gains `equivalent_usd`, `gateway` and `served_model`;
    `RunSummary` gains the same three.
  - `budget/usage.py::extract_gateway_meta`.
  - `BudgetGuard.degradation()` returns `reduce` with
    `reason="gateway_quota:<window>"` after a quota downgrade.
- **Reconciliation and quotas.** `CostReconciler` (bounded, alert-only drift
  histogram) and `GatewayQuotaPoller` (`gateway_quota_remaining` gauge).
- **Composition.**
  - `build_runtime(..., gateway_vault=)` composes `RuntimeContext.gateways`.
  - Tenant keys are `<auth_ref>@<org_id>`, with no global fallback unless
    `llm_gateway_tenant_key_fallback`.
  - Startup gates run before first use; a failing or crashing gate disables the
    gateway.
  - Reconciler and poller lifecycles are managed.
- **Settings**: the GW block in `core/config.py` with `_validate_llm_gateways`.
  **Exceptions**: the `GatewayError` tree. **Metrics**: the six
  `prismal.gateway_*` metrics (`OTelManager.set_gauge` is new).
- Examples: `examples/gateways_{openrouter,opencode,omniroute}.py` (offline).
  Opt-in live smoke tests: `tests/live/test_gateways_live.py` (`live_api`).

### Changed
These facts changed at GW0 verification (`VERIFY.md`):
- **OmniRoute floor.** CVE-2026-88062 affects OmniRoute ≤ 3.8.50 with no named
  patch, so the default `omniroute_min_version` is `3.8.51`, not 3.8.49.
  OmniRoute documents no version endpoint, so `omniroute_declared_version` is
  new.
- **OpenCode deny-list.** DeepSeek ZDR on OpenCode ended on 2026-08-31, so
  `deepseek-v4-flash*` joins the default `opencode_denied_models`.

### Known gaps
- The prisma-notebooks notebook (GW6-07) lives in another repository and is
  not part of this change.
- `config/opencode_models.yaml` is illustrative. Refresh it, or edit it, before
  production use.

## [3.13.0] — 2026-10-05

> Phase TKE — **Token Efficiency**, the first phase of wave 2026-10
> (`specs/wave-2026-10-implementation-plan.md`). It measures first and then
> cuts tokens without changing agent behaviour. Everything is gated by
> `token_efficiency_enabled` (default `False`), and every sub-flag takes
> effect only with that master flag on. With it off, the compiled supervisor
> graph, `get_tools_for_agent()`, `SecurePromptBuilder.build()`, the
> `ChatLiteLLM` construction and the metered `Usage` numbers are identical to
> v3.12.0 (snapshot + disabled-contract tests). LLM construction follows
> `docs/adr/ADR-WAVE-001-llm-construction-contract.md`. Provider facts were
> verified in `specs/token-efficiency/VERIFY.md`.

### Added
- **Cache-aware accounting (TKE1).**
  - `TokenCounts`/`Usage` gain `cache_read_tokens`, `cache_write_tokens`,
    `reasoning_tokens` and `cost_source`, plus `cache_hit_ratio` and
    `uncached_prompt_tokens`.
  - `extract_token_usage()` reads the LangChain token details and raw
    provider shapes.
  - `compute_cost_usd(..., cache_read_tokens=, cache_write_tokens=,
    reported_cost_usd=, response=)` uses gateway → LiteLLM (cache-aware) →
    table → none precedence.
  - `CostMeter.record_response(..., reported_cost_usd=)` adds kind-split
    `prismal.llm_input_tokens_total` and `prismal.llm_reasoning_tokens_total`
    counters and a `prismal.llm_cache_hit_ratio` histogram.
  - `RunSummary` gains cache fields.
  - `eval/metrics_tokens.py`: `TokenReport`, baseline save/load and
    `check_token_regression`. The token report appears in every scorecard.
  - New CLI options: `python -m prismal.eval token-gate` and
    `run --write-token-baseline`.
- **Prompt caching (TKE2).** `providers/prompt_cache.py` provides
  `detect_cache_family`, `CachePolicy` (`config/prompt_cache.yaml`),
  `apply_cache_policy` and `CacheAwareChatModel`.
  - Anthropic: `cache_control` on the last tool, the system prompt and the
    last stable message.
  - OpenAI: `prompt_cache_key`.
  - Tools are bound sorted by name and serialized with `stable_dumps`.
  - `ProviderRegistry.get_llm(..., agent=)` and
    `get_llm_with_fallback(..., agent=)` gain the `agent` argument.
  - `utils/stable_json.py`.
- **Cache-stable prompt layout (TKE2).** `SecurePromptBuilder(layout=...)` adds
  `PromptLayout.CACHE_STABLE`, `TurnContext`, an HMAC `prefix_canary()`,
  `detect_leak()` and `canaries`. `GuardrailsEngine.validate_output(canary=)`
  now accepts several canaries. Leak-detection recall on the red-team corpus
  is the same for both layouts. The supervisor moves recalled memory to the
  tail turn context.
- **Cache-safe tool exposure (TKE3, delta to LH2).**
  - `agents/tool_gate.py` and `CompositeToolProvider(tool_gating_mode="mask")`
    with `allowed_tool_names()`.
  - The allow-set is enforced in `react_loop(tool_allow_set=, tool_phase=)` and
    `ActionInterceptor.set_tool_allow_set()`.
  - `tool_gate_react_kwargs()` fails closed.
  - `build_default_tool_provider(settings)` wires the LH phase map and the
    gating mode when `tool_gating_enabled` is on.
  - `agents/tool_search.py`: BM25 `ToolSearchIndex`, `search_tools`,
    append-only `DeferredToolSet`, `plan_deferred_tools`, and native
    `defer_loading` for the Anthropic family.
  - `react_loop(deferred_tools=, rebind_llm=)`.
- **Observation masking + artifact store (TKE4, delta to LH1).**
  - `agents/artifacts.py`: in-memory and SQLite `ArtifactStorePort` scoped to
    tenant and run, with TTL and a `read_artifact` tool.
  - `CompactionStrategy.MASK_OBSERVATIONS`: stubs keep the same id and
    tool_call_id, and the payload is stored before masking.
  - Optional `relevance_fn`, and high/low-water hysteresis.
- **Tool-output budgets + TOON (TKE5).** `agents/tool_output_budget.py`
  produces a head+tail excerpt plus an artifact ref. It is applied in
  `react_loop` (replacing the 4 000-char cap when enabled) and in the new
  `tool_output_budget_middleware`, which runs just outside the hardening
  middleware. `utils/toon.py`, `utils/token_estimate.py`.
- **Effort per role (TKE6).** `llm_effort_by_agent`, `llm_max_tokens_by_agent`
  and the `recommended` profile. `reasoning_effort` is sent only to
  reasoning-capable models.
- **Handoff envelopes (TKE7).** `agents/handoff.py`: `HandoffEnvelope`,
  `build_envelope`, `maybe_envelope` and a per-run artifact store
  (`get_run_artifact_store` / `clear_run_artifact_store`). Wired into Skynet
  (`WorkerResult.envelope`; reducer and evaluator read summaries), MoA
  (`MoAResult.proposer_envelopes`), Kokoro (judge reads summaries), BRP,
  `parallel_research` and the spawner (`get_envelope`). For BRP and the spawner
  the envelope is attached but nothing reads it yet, so they save no tokens.
- **RAG context budget (TKE8).**
  - `rag/context_budget.py`, `providers/rerank.py` (cross-encoder) and
    `providers/compression.py` (LLMLingua-2), with new extras `[rerank]` and
    `[compression]`.
  - Wired into `RAGEngine`, CRAG, HyDE and Hybrid.
- Settings: the TKE block in `core/config.py` with `_validate_token_efficiency`.
  `context_compaction_strategy` now accepts `mask_observations`.
- Exceptions: `TokenEfficiencyError` hierarchy (`ArtifactNotFound`,
  `MaskedToolCallError`, …).
- Metrics: 15 counters and 2 histograms (`SPEC-TKE-OTEL-001`).
- Docs and examples: `docs/token-efficiency.md`, `examples/token_efficiency.py`,
  ADR-WAVE-001.

### Fixed
- `tests/unit/agents/conftest.py` no longer replaces a real, installed
  `langchain_litellm` with a `MagicMock`. The stub leaked into later test
  modules depending on run order.

### Known gaps
- The committed token baseline (TKE1-08) and the eval A/B run (TKE9-04) are
  pending. Trajectories capture 0 tokens for internal LLM calls, so a live run
  and/or meter-based capture is needed before the CI token gate can block.
- TKE10 (code mode for MCP chains, optional P3) is deferred.

## [3.12.0] — 2026-07-12

> Fase S+ — **Skynet S+ (heterogeneous / metered / remote swarms)**. Follow-up
> to Phase S that turns each worker into a **specialist** (own model / persona /
> tools via a file-driven `RoleRegistry`), makes the swarm **truthfully metered**
> (worker tokens counted into a single shared `CostMeter`, so `skynet_token_budget`
> bounds the *whole* swarm), and lets a role be a **remote A2A agent** (the whole
> sub-order runs elsewhere). Composes the shipped Phase C (`budget/`) and Phase I
> (`a2a/`) primitives — no new LangGraph capability, no change to the Phase-S
> subgraph topology. All additive and opt-in (`skynet_specialists_enabled`,
> `skynet_remote_workers_enabled`); with every S+ flag off the compiled graph and
> the skynet subgraph are byte-for-byte unchanged (snapshot-tested).

### Added
- `prismal/agents/skynet/roles.py` — `SpecialistRole` + `DEFAULT_ROLE` +
  `RoleRegistry` (`from_yaml` load mirroring the Phase-H tool-policy loader;
  `resolve` never raises, falling back to `DEFAULT_ROLE`). Config example:
  `config/skynet_roles.example.yaml`.
- `prismal/agents/skynet/remote.py` — `make_remote_send_fn(...)`: delegates one
  `SwarmOrder` over A2A via the Phase-I `A2AConnectionManager` (allowlist +
  strict deny-all + pool); remote output is L1-sanitized before it returns and
  audited hash-first (`a2a.outbound`).
- `WorkerResult.usage` / `.role` / `.remote` and `SwarmResult.usage` (additive,
  defaulted — Phase-S round-trips preserved).
- Role-aware `SkynetSupervisor.plan()` (offers `known_roles()` to the planner,
  validates tags), injected shared `meter=`; role-aware / metered / remote
  `SwarmWorker` (`role_registry=`, `meter=`, `send_fn=`, `budget_guard_fn=`);
  metered default reducer (`reduce_results(meter=)`); builder threads ONE
  `CostMeter` into supervisor + worker + reducer + output.
- Settings: `skynet_specialists_enabled`, `skynet_roles_path`,
  `skynet_remote_workers_enabled`, `skynet_remote_allowlist`; `_validate_skynet`
  warns `skynet.remote_needs_a2a` when remote is enabled without `a2a_enabled`.
- Exception `SkynetRoleError` (role-registry load time only).
- Docs: `docs/skynet.md` S+ sections. Example: `examples/skynet_specialist_swarm.py`.

### Changed
- `skynet_token_budget` is now **truthful whole-swarm** accounting (worker tokens
  counted); `enforce_token_budget()` shape is unchanged. `output_node` became a
  `make_output_node(meter=)` factory (topology unchanged).

## [3.11.0] — 2026-07-11

> Fase BRP — **Blind Review Pipeline**. A new opt-in subgraph: a spec agent and
> an implementer produce an artifact that two independent, **blind** reviewers
> (no visibility into `state["messages"]`, only spec + artifact) assess before a
> deterministic synthesis and a bounded correction loop, optionally HITL-gated.
> Each of the four roles gets its own LLM (`ProviderRegistry`) and tool scope
> (`ToolProviderPort`). Gated by `blind_review_pipeline_enabled` (default
> `False`) — with the flag off the compiled supervisor graph is byte-for-byte
> unchanged (snapshot-tested).

### Added
- `prismal/agents/subgraphs/blind_review_pipeline/` — `make_spec_agent_node`,
  `make_implementer_agent_node`, `make_reviewer_node` + `BlindnessGuard`,
  `synthesize_verdicts` / `make_synthesis_node`,
  `build_blind_review_pipeline_subgraph` / `register_blind_review_pipeline`.
  Reuses `CodeIssue`/`CodeReviewReport`, `score_gate`, and the `dev_pipeline`
  HITL trio unmodified.
- Blindness enforced three ways: the narrow `(spec, artifact)` input contract,
  a CI-blocking AST guard (`reviewer_node.py` never reads `state["messages"]`),
  and the runtime `BlindnessGuard`.
- Settings: `blind_review_pipeline_enabled`, per-role `blind_review_*_model` /
  `blind_review_*_capabilities`, `blind_review_approval_threshold`,
  `blind_review_max_iterations` (validated by `_validate_blind_review`).
- Exceptions: `BlindReviewPipelineError`, `BlindReviewConfigError`,
  `BlindReviewBlindnessViolationError`.
- Supervisor/intent integration (gated): `match_intent()` returns
  `blind_review_pipeline` for review-panel intents; `effective_valid_routes` /
  `build_system_prompt` / `get_async_compiled_graph` honour the flag.
- Docs: `docs/blind-review-pipeline.md`; example:
  `examples/blind_review_pipeline.py`.

### Fixed
- The implementer node now increments `iteration_count`, so the `score_gate`
  `max_iterations` force-pass actually bounds the correction loop.

### Notes
- Reviewers run **sequentially** (not the spec's two-way fan-out): both write
  the no-reducer `metadata` channel, which raises `InvalidUpdateError` on a
  concurrent superstep. Independence/blindness is unaffected — it is a property
  of the input contract, not execution concurrency; only reviewer latency is
  traded away.

## [3.10.2] — 2026-07-08

> Public-surface patch: no engine logic changed and `build_agent_card`'s
> behaviour is unchanged. A host that serves the A2A Agent Card at
> `/.well-known/agent-card.json` needs the two inputs of
> `build_agent_card(settings, registry, ...)` — a `Settings` instance and a
> capability registry. Re-exporting them from `prismal.a2a` lets the host build
> the card without reaching into `prismal.core` / `prismal.agents` internals.

### Added
- `prismal.a2a` now re-exports `get_settings` (from `prismal.core.config`) and
  `DEFAULT_CAPABILITY_MAP` (from `prismal.agents.tool_registry`), so
  `from prismal.a2a import build_agent_card, get_settings, DEFAULT_CAPABILITY_MAP`
  is enough to construct the Agent Card. Purely additive — existing exports and
  behaviour are unchanged.

**Consumer note.** After bumping the pin to `prismal-ai>=3.10.2,<4`,
`prismal-server` can build the card from these public symbols and
`GET /.well-known/agent-card.json` stops returning `500` and serves the real
card.

## [3.10.1] — 2026-07-07

> Packaging-only patch: no engine logic changed, and the four public entry
> points (`build_runtime`, `get_async_compiled_graph`, the A2A
> `A2AServerHandler` / `build_agent_card`, and identity) are byte-for-byte
> unchanged.

### Fixed
- **Fresh-resolve packaging metadata for downstream hosts.** The core
  dependency floor `unstructured>=0.16.10` was loose enough that a *fresh*
  resolution of the published `prismal-ai` sdist (a consumer that does not
  inherit the engine's `uv.lock`) could select an old `unstructured` whose
  dependency tree drags an ancient `numba` (`0.53.1`, Python <3.10 only) —
  which has no Python 3.13 wheel and cannot build on 3.13. The floor is raised
  to `unstructured>=0.21.5` (the version the engine's own `uv.lock` already
  pins), so a clean py3.13 resolve now selects the working transitive trio
  (`unstructured 0.21.5`, `numba 0.61.2`, `llvmlite 0.44.0`). `numba` and
  `llvmlite` are transitive-only (never imported by `prismal`), so they are
  intentionally not declared as direct dependencies.

### Notes
- Downstream hosts (e.g. `prismal-server`) that added a temporary
  `[tool.uv] constraint-dependencies` workaround for `numba` / `llvmlite` /
  `unstructured` can drop it once they depend on `prismal-ai>=3.10.1`.

## [3.10.0] — 2026-07-07

> Spec:
> [`specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md`](./specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md).
> Closes the one deferred task of Phase IDN (**ID6-02**): `PermissionManager`
> TTL grants can now be keyed to an identity DID. Additive and
> backward-compatible — omit `identity` and grants stay global, exactly as
> before.

### Added
- `PermissionManager.grant/check/revoke` gain a keyword-only
  `identity: str | None = None` (the caller DID). A global grant (`None`,
  stored as `NULL`) is usable by any identity; a DID-scoped grant satisfies a
  check only when the caller's `identity` matches, so two identities no longer
  share a `(permission_type, resource)` grant.
- `PermissionManager.list_grants(identity=None)` — global/admin view (every
  active grant) or an identity view (global + that DID's grants).
  `list_permissions()` is now a thin alias for `list_grants()`.
- `ActionInterceptor(..., identity=...)` keyword-only param threads the resolved
  `AgentIdentity.did` into the pre-tool TTL `check`; constructed without it, the
  identity-less `check(perm, "*")` call is byte-for-byte unchanged.

### Notes
- The `permissions.identity` column is additive and nullable (`NULL` = global) —
  no destructive migration. `PolicyEngine.allow()` remains the authoritative
  identity-aware decision; these grants are a narrower TTL allowlist beneath it.

## [3.9.0] — 2026-07-06

> Spec: [`specs/observability-integration/`](./specs/observability-integration/).
> Adds an **opt-in** `ObservabilityPort` — a stable, backend-agnostic contract
> over one run's telemetry (spans, cost/latency, tool-call history, node-visit
> sequence) — plus the concrete LangSmith/Langfuse **parity** closes (consistent
> run/trace naming, a score/feedback hook, and evaluation-dataset export). The
> default adapter is a thin wrapper over the *existing* `OTelManager`/
> `LangfuseManager` emission, so it ships useful before any dashboard exists.
> Additive and **opt-in**: with `observability_enabled=False` (default)
> `RuntimeContext.observability` is `None`, the compiled supervisor graph is
> byte-for-byte unchanged (snapshot-tested), and no existing OTel/Langfuse call
> site changes. Framework-side only — a literal observability UI belongs to the
> planned `prismal-dashboard` repo, which consumes this port.

### Added

- `monitoring/observability_types.py` — frozen value objects `RunSummary`,
  `SpanRecord`, `ToolCallRecord`, `ScoreAnnotation`, and the `DatasetFormat`
  enum. `RunSummary.usage` reuses `budget.types.Usage` (and its `__add__`) for
  the cost/latency portion — parallel to, but independent of, `eval.Trajectory`
  (DD-OBS-001, no import coupling either way).
- `agents/extension/ports.py` — `ObservabilityPort` `Protocol` (`@runtime_checkable`,
  10th port in the family): `record_node` / `record_score` (sync, **never raise**
  on the hot path) + `get_run_summary` / `export_dataset` (best-effort, never
  raise). Re-exported from `agents/extension/__init__.py`.
- `monitoring/observability.py` — `DefaultObservabilityProvider` (glue over the
  OTel/Langfuse singletons with a bounded in-memory ring buffer per run;
  `run_buffer_size` spans/tool-calls, `max_runs` runs with LRU eviction),
  `FakeObservabilityProvider` (deterministic, I/O-free), and the naming
  single-source-of-truth `run_name_for` / `trace_tags_for` (DD-OBS-004).
- `monitoring/observability_resolve.py` — per-run registry
  (`seed_observability_run` / `get_observability_provider` /
  `clear_observability_run`), idempotent per `(session_id, turn)`; the live
  provider stays **out of checkpointed state** (DD-OBS-002, mirrors
  `budget/resolve.py`).
- `record_score` end-to-end: stores a local `ScoreAnnotation` **and** forwards to
  `LangfuseManager.score_trace` keyed by the canonical `run_id`. `export_dataset`
  emits LangSmith (snake_case) and Langfuse (camelCase) evaluation-dataset shapes.
- `composition/runtime.py` — `RuntimeContext.observability: ObservabilityPort | None`
  plus a `build_runtime()` opt-in composition step gated on `observability_enabled`
  (mirrors `identity_enabled` / `a2a_enabled`; `RuntimeCompositionError("observability", …)`
  on failure). `build_test_runtime(observability=…)` fake-injection parameter.
- Settings `observability_enabled` (default `False`), `observability_run_buffer_size`
  (`200`), `observability_max_runs` (`500`), `observability_score_source_default`
  (`"system"`), `observability_dataset_export_format` (`"langsmith"`), validated by
  `_validate_observability`. Exceptions `ObservabilityError` / `ObservabilityConfigError`
  / `RunNotFoundError`. OTel counters `prismal.observability_runs_total{result}`,
  `prismal.observability_scores_total{name}`, `prismal.observability_dataset_exports_total{fmt}`.
- Docs: [`docs/observability-integration.md`](./docs/observability-integration.md)
  (explicit framework/host split + eval-harness LLM-judge → `record_score`
  pattern). Example: [`examples/observability_integration.py`](./examples/observability_integration.py).

## [3.8.0] — 2026-07-05

> Spec: [`specs/node-io-typesafety/`](./specs/node-io-typesafety/). Adds an
> **opt-in, per-node** Pydantic I/O contract layer at the `@prismal_node`
> boundary. `AgentState` stays a bare `TypedDict`; declared `input_model`/
> `output_model` are narrow boundary projections validated at node entry/exit.
> Additive and **opt-in**: with `node_typesafety_enabled=False` (default) the
> compiled supervisor graph is byte-for-byte unchanged (snapshot-tested).

### Added

- `agents/extension/node_schema.py` — `NodeIOMode`, `NodeIODirection`,
  `NodeIOValidationResult` (frozen), and pure, never-raising
  `validate_node_input()` / `validate_node_output()` helpers (narrow-projection
  semantics; extra keys ignored; field-name-only error messages, never values).
- `@prismal_node(input_model=, output_model=)` and matching `NodeMetadata`
  fields (both default `None`, so every existing call site is unaffected);
  `PrismalStateGraphBuilder.add_node(input_model=, output_model=)` forwarded on
  auto-wrap only.
- `node_io_validation_middleware` — new **innermost** entry of
  `DEFAULT_MIDDLEWARE_STACK` (one layer inside `hardening_middleware`); a pure
  passthrough when disabled. Modes `off | warn | enforce`: `warn` logs + counts
  and passes through; `enforce` raises `NodeValidationError`, mapped by the
  existing `error_mapping_middleware` with zero changes to it.
- Settings `node_typesafety_enabled` (default `False`) + `node_typesafety_mode`
  (default `warn`) with a `_validate_node_typesafety` validator rejecting an
  unknown mode; the `NodeValidationError` stub gains `direction`/`schema_errors`.
- OTel counters `prismal.node_io_validated_total` and
  `prismal.node_io_validation_failures_total` (labelled by `node`, `direction`).
- Pilot annotations on `file_manager`, `cron_manager`, `skill_manager` with an
  `AgentState`-field-name drift guard; `docs/node-typesafety.md`;
  `examples/node_typesafety.py`.

## [3.7.0] — 2026-07-04

> Spec: [`specs/loop-hardening/`](./specs/loop-hardening/). Closes two gaps
> in the agentic-loop mechanics: no context/message-window compaction, and
> no dynamic tool provisioning by task phase. Additive and **opt-in**:
> `context_compaction_enabled` and `tool_gating_enabled` (both default
> `False`) ⇒ compiled graph and `get_tools_for_agent()` output byte-for-byte
> unchanged (snapshot + contract tested). Implemented test-first (TDD).

### Added — Loop Hardening (Phase LH)

- **`agents/context_compaction.py`** — `ContextCompactor`: trims/summarizes
  `state["messages"]` via `RemoveMessage` (never in-place mutation) once a
  message-count (or Budget-token) threshold is exceeded, keeping the most
  recent `context_compaction_keep_recent` messages verbatim. `truncate`
  (default, no LLM call) or `summarize` (opt-in, Budget-metered, falls open
  to truncate on summarizer failure). Per-run seeding trio
  (`maybe_seed_context_compaction_run`/`get_context_compactor`/
  `clear_context_compaction_run`) mirrors `budget/resolve.py`; wired into
  `supervisor_node` next to `maybe_seed_budget_run`/`maybe_seed_hardening_run`.
  A second, optional `react_loop(..., context_compactor=...)` hook compacts a
  single node's local tool-loop accumulator (position-based, via the new
  `compact_list()`) — `context_compaction_react_kwargs()` mirrors
  `hardening_react_kwargs` for opting individual nodes in.
- **`agents/loop_phase.py::resolve_phase`** — deterministic, LLM-free task
  phase (`planning`/`executing`/`finishing`/`None`) from an explicit
  `state["metadata"]["loop"]["phase"]` hint or `task_plan`/`pending_tasks`/
  `completed_tasks`.
- **`agents/extension/ports.py`** *(extended)* — `ToolProviderPort.get_tools()`
  gains an optional `phase` keyword (non-breaking Protocol widening).
- **`agents/extension/providers.py`** *(extended)* —
  `CompositeToolProvider(phase_capability_map=...)` intersects a phase's
  capability override with the caller's capabilities before delegating to
  live sub-providers (never a superset); falls open to a phase-less call on
  `TypeError` from a non-conforming sub-provider. New
  `load_phase_capability_map()` + `config/tool_gating_phases.yaml`.
- **`agents/tool_registry.py`** *(extended)* — `get_tools_for_agent()` /
  `get_tools_for_agent_ctx()` / `_observed_get_tools()` thread an optional
  `phase`, with the fail-open shim centralized at the single
  `_observed_get_tools` choke point.
- **Settings** (`core/config.py`) — `context_compaction_enabled`,
  `context_compaction_strategy`, `context_compaction_max_messages`,
  `context_compaction_token_threshold`, `context_compaction_keep_recent`,
  `context_compaction_summarizer_model`,
  `context_compaction_min_interval_messages`, `tool_gating_enabled`,
  `tool_gating_phase_map_path`.
- **Exceptions** — `LoopHardeningError`, `ContextCompactionError`,
  `ToolGatingConfigError`.
- **Observability** — `prismal.context_compactions_total{strategy}`,
  `prismal.context_compaction_messages_dropped_total`,
  `prismal.context_compaction_summarize_errors_total`,
  `prismal.tool_gate_narrowed_total{agent}`,
  `prismal.tool_gate_phase_resolved_total{agent,phase}`.
- **Artifacts** — `docs/loop-hardening.md`; `examples/loop_hardening.py`.

---

## [3.6.0] — 2026-07-04

> Spec: [`specs/guardrails-modernization/`](./specs/guardrails-modernization/).
> Closes two concrete gaps in the 5-layer security stack: Layer 3 (NeMo
> Guardrails) shipped no config, and output enforcement had no schema-first,
> retry-capable framework. Additive and **opt-in**: `nemo_classifier_enabled`
> and `structured_output_guard_enabled` (both default `False`) ⇒ compiled
> graph byte-for-byte unchanged (snapshot-tested). Implemented test-first (TDD).

### Added — Guardrails Modernization (Phase GRD)

- **`config/nemo_rails/`** — `config.yml` + `main.co` (dialog/topical Colang
  flows for the 5(+1) sentinel categories already asserted by
  `test_nemo_rails.py`) + `safety_classifier.co`. `NemoRailsLayer` no longer a
  silent no-op: `available=True` once `nemo_guardrails_enabled=True`.
- **`security/nemo_actions.py`** — `content_safety_reasoning()`, a
  reasoning-capable safety-classifier NeMo custom action gated by
  `nemo_classifier_enabled`; fails open (`"safe"`) on timeout/error, audited +
  counted. Its own `nemo_classifier_timeout_seconds` budget is fully
  independent of the existing 450ms dialog-rail timeout.
- **`security/nemo_rails.py`** *(extended)* — resolves NeMo's main LLM via
  `providers/registry.py::ProviderRegistry` instead of `LLMRails`'s own
  config-driven (and previously hardcoded-provider) resolution; conditionally
  registers the classifier action and activates its Colang flows when enabled.
  Public API unchanged.
- **`security/structured_output_guard.py`** — `StructuredOutputGuard`,
  `StructuredOutputVerdict`: schema-first validation via `guardrails-ai`'s
  `Guard.for_pydantic(schema).validate()`, with a bounded, Budget-metered
  automatic re-ask loop driven entirely by Prismal (never `guardrails-ai`'s own
  `llm_api` mechanism — zero provider-isolation compromise). Composes with,
  never replaces, `OutputValidator`. Opt-in Guardrails Hub validators
  (`detect_pii`, `provenance_llm`, `toxic_language`) per-call, gated by
  `structured_output_guard_hub_validators_enabled`; an uninstalled/unknown
  validator degrades gracefully. New `[guardrails-ai]` extra; absent ⇒
  `MissingDependencyError` at construction, never mid-call.
- **Settings** (`core/config.py`) — `nemo_classifier_enabled`,
  `nemo_classifier_model`, `nemo_classifier_categories`,
  `nemo_classifier_threshold`, `nemo_classifier_timeout_seconds`,
  `structured_output_guard_enabled`, `structured_output_guard_max_reasks`,
  `structured_output_guard_hub_validators_enabled` (all default off/2/False).
- **Exceptions** — `GuardrailsModernizationError`, `NemoClassifierError`,
  `NemoClassifierConfigError`, `StructuredOutputGuardError`,
  `StructuredOutputReaskExhausted`; reuses the existing `MissingDependencyError`
  for the missing-extra case.
- **Observability** — `prismal.nemo_classifier_checks_total{category,result}`,
  `prismal.nemo_classifier_latency_seconds`,
  `prismal.structured_output_reask_total{outcome}`,
  `prismal.structured_output_hub_validator_blocks_total{validator}`.
- **Artifacts** — `docs/security/guardrails-modernization.md`;
  `examples/guardrails_modernization.py`; `[guardrails-ai]` extra in
  `pyproject.toml`.

---

## [3.5.0] — 2026-06-17

> Spec: [`specs/a2a-interop/`](./specs/a2a-interop/). Bidirectional Agent2Agent
> (A2A) interoperability — the last pending roadmap phase. Additive and
> **opt-in**: `a2a_enabled` (default `False`) ⇒ compiled graph byte-for-byte
> unchanged (the supervisor graph is untouched). Implemented test-first (TDD);
> 94% branch coverage on `prismal/a2a`.

### Added — A2A interoperability (Phase I)

- **`prismal.a2a`** subpackage (`[a2a]` extra; HTTP/SSE imports deferred):
  - `types.py` — A2A v0.3.x Pydantic models (`AgentCard`, `AgentSkill`,
    `A2ATask`, `A2AMessage`, `A2AArtifact`, `A2APart`, `A2AAuth`); camelCase wire
    aliases, snake_case attributes; `A2AAuth` secrets are `SecretStr`.
  - `card.py` — `build_agent_card(settings, registry, *, org_id, did)` derives the
    Agent Card from the capability registry + `a2a_published_skills` allowlist;
    tenant-scopes the URL; embeds the identity DID (`did:web` from the Phase IDN
    settings or an explicit override); adds media output modes when
    `multimodal_enabled`; cached per (settings fingerprint, org). Served at
    `/.well-known/agent-card.json` by the host.
  - `server.py` — `A2AServerHandler` (inbound): JSON-RPC `message/send` /
    `tasks/get` / `tasks/cancel` (`handle_rpc`) + SSE streaming (`stream_rpc`);
    maps a task → sanitized graph invocation (`thread_id = task_id`) → A2A
    artifacts; `AuthContext` gate (strict mode requires auth); audits
    `a2a.inbound`.
  - `client.py` — `A2AClient` (discover card, `send_task` over JSON-RPC+SSE,
    `cancel`, bearer/OAuth2-client-credentials auth with token caching),
    `A2AConnectionManager` (fnmatch allowlist + deny-all-in-strict + client pool;
    mirror of `mcp/connection.py`), and `A2AAgentNode.as_node()` — wraps a remote
    agent as a `@prismal_node` graph node (the A2A analogue of
    `LangChainRunnableAdapter`); a remote failure yields a graceful
    `metadata.a2a.error` update instead of aborting the graph.
  - `provider.py` — `A2AToolProvider` conforms to the Phase Y `ToolProviderPort`:
    surfaces remote skills as `a2a__{agent}__{skill}` tools, composable in a
    `CompositeToolProvider`; sync `get_tools` never raises; async `prepare()`
    discovers URL-only agents.
- **Security** — every remote artifact/message passes `InputSanitizer` before it
  touches `AgentState`; outbound delegation honours the allowlist (deny-all in
  strict); inbound text is sanitized before the graph; all in/out tasks audited
  without content.
- **Settings** (`core/config.py`) — `a2a_enabled`, `a2a_inbound_enabled`,
  `a2a_outbound_enabled`, `a2a_base_url`, `a2a_published_skills`,
  `a2a_outbound_allowlist`, `a2a_strict` (all default off/empty/strict).
- **Exceptions** — `A2AError`, `A2AAgentUnavailable`.
- **Composition root** (`composition/runtime.py`) — `build_runtime(..., graph=,
  a2a_agents=)` composes `A2AToolProvider` into the tool provider (outbound) and
  exposes an `A2AServerHandler` on `RuntimeContext.a2a_handler` (inbound) when
  `a2a_enabled`; per-`org_id` Agent Card.
- **Artifacts** — `docs/a2a.md`; `examples/a2a_server.py`,
  `examples/a2a_remote_node.py`; `[a2a]` extra in `pyproject.toml`.

---

## [3.4.0] — 2026-06-16

> Spec: [`specs/agent-identity-governance/`](./specs/agent-identity-governance/).
> Per-agent identity + access policy; foundation for A2A (I) and multi-tenant (R).
> Additive and **opt-in**: `identity_enabled` (default `False`) ⇒ compiled graph
> byte-for-byte unchanged (snapshot-tested). Implemented test-first (TDD); 100%
> coverage on `prismal/identity`.

### Added — identity & access governance (Phase IDN)

- **`prismal.identity`** hexagonal package: `types.py` (`AgentIdentity`, `DID`,
  `Scope`, `Credential`, `OnBehalfToken`, `PolicyDecision`), `did.py` (`did:key`
  local + `did:web` for A2A; issue/resolve/verify/`did_document`; base58btc +
  Ed25519 inline, only `cryptography`/`httpx`), `provider.py` (`LocalIdentityProvider`/
  `OidcIdentityProvider`/`FakeIdentityProvider`), `vault.py` (`EnvVault` via
  `ConfigSourcePort`/`FileVault` encrypted-at-rest/`FakeVault`; secrets resolved at
  the boundary, never in state/logs), `delegation.py` (OAuth on-behalf-of:
  `mint`/`propagate` narrow-only/`revoke`/`validate`), `policy.py` (identity-aware
  `PolicyEngine.allow(identity, action, resource)` that **delegates** `(agent, tool,
  args)` to the Phase H `ToolPolicyEngine`).
- **Ports** (`agents/extension/ports.py`) — `IdentityPort`, `CredentialVaultPort`,
  `PolicyPort` Protocols, re-exported from `agents/extension`.
- **Settings** (`core/config.py`) — `identity_enabled`, `identity_mode`,
  `identity_provider`, `identity_did_method`, `identity_did_web_domain`,
  `identity_vault`, `identity_policy_path`, `identity_on_behalf_enabled`,
  `identity_on_behalf_ttl_s`, `oidc_issuer`/`oidc_client_id`; `_validate_identity`.
- **Exceptions** — `IdentityError` hierarchy (`DidVerificationError`, `ScopeError`,
  `PolicyDenied`, `CredentialResolutionError`, `IdentityConfigError`,
  `DelegationError`).
- **Observability** — counters `prismal.identity_issued_total`,
  `prismal.policy_decisions_total`, `prismal.credential_resolved_total`,
  `prismal.did_verify_total`.
- **Artifacts/docs** — `config/identity_policies.yaml`,
  `agent-card-did.example.json`; `docs/identity.md`; `examples/agent_identity.py`.

### Changed — identity & access governance (Phase IDN)

- `ActionInterceptor` gains `check_identity_policy(...)` — consults `PolicyEngine`
  (least-privilege scopes + identity rules) when `identity_enabled`; a `DENY` is
  audited with the identity DID (never a secret) and raised as `PolicyDenied`;
  `REQUIRE_HITL` routes through `hitl_gate()`. Additive — the existing `check()`
  path is unchanged.
- `composition/runtime.py` — `build_identity_ports(settings)` + `build_runtime`
  composes the identity provider + vault + policy per `org_id` onto the
  `RuntimeContext` (`identity_provider`/`credential_vault`/`policy_engine`, `None`
  when disabled); `FileVault` made lazy (no key-file I/O on construction).

### Deferred — identity & access governance (Phase IDN)

- `PermissionManager` grants keyed by DID (ID6-02) — needs an Alembic migration
  for the existing `permissions` table; the `PolicyEngine` + scopes already
  provide identity-aware authorization. Tracked as a follow-up.

## [3.3.0] — 2026-06-15

> Spec: [`specs/agent-eval-harness/`](./specs/agent-eval-harness/). System-level
> evaluation (the "scaffold gap"); the red-team suite is the executable proof for
> Phase H controls. Additive; **no agent-runtime change**. Fakes by default,
> `live_api` opt-in.

### Added — evaluation harness (Phase V)

- **`prismal.eval`** package (sibling of the runtime; imports only the public
  graph entry + ports, AST-guarded): `types.py` (`EvalCase`/`EvalSet`/`Assertion`/
  `Trajectory`/`CaseResult`/`Scorecard` + `EvalSet.from_yaml`), `runner.py`
  (`EvalRunner` over `get_async_compiled_graph().astream` with `build_test_runtime`
  fakes + per-case seed; never raises), `trajectory.py` (capture from the public
  event stream + cost/tokens/security-signals), `assertions.py`
  (exact/semantic/tool-usage/llm-judge/groundedness/security + `dispatch_assertions`),
  `judges.py` (LLM-as-judge via `providers/`, `SecurePromptBuilder`-isolated),
  `regression.py` (baseline diff + tolerance gate), `redteam/` (adversarial corpus
  loader + `assert_security` containment), `report.py` (JSON/Markdown/Langfuse),
  `__main__.py` CLI (`run`/`redteam`/`gate`).
- **Adversarial corpus** — `tests/eval/redteam/corpus.yaml` (direct + indirect
  injection, tool-abuse, exfiltration, jailbreak, system-prompt leak); each case
  asserts containment against L1–L5 (+ Phase H controls when present).
- **Public surface** — `prismal.langgraph` re-exports `create_initial_state` so the
  harness builds graph input via the public state factory, not an `agents.*` internal.
- **Settings** (`core/config.py`) — `eval_default_mode`, `eval_judge_model`,
  `eval_regression_tolerance`, `eval_seed`, `eval_langfuse_export` (+ `_validate_eval`).
- **Exceptions** — `EvalError`, `EvalSetError`, `RegressionGateFailed`.
- **CI** — `eval` + `redteam` pytest markers; AST guard
  (`tests/unit/eval/test_no_internal_imports.py`). Docs: `docs/eval.md`; example:
  `examples/agent_eval.py`.

---

## [3.2.0] — 2026-06-14

> Spec: [`specs/runtime-hardening/`](./specs/runtime-hardening/) ·
> Research: `docs/security/hardening-and-harness-engineering.md`. Closes residual
> OWASP **LLM01/05/06/10** gaps. Opt-in: `hardening_enabled` (default `False`).

#### Added — runtime hardening (Phase H)

- **`prismal.security.taint`** — `Provenance`, `TaintTag`, `TaintRegistry`
  (`mark_untrusted`/`is_untrusted`); content from tools/RAG/web/STT/OCR/captions/
  souls is tagged at its loader (hashes only — serializable, no secrets).
- **`prismal.security.indirect_injection`** — `IndirectInjectionDetector`: scores
  untrusted content through the existing `GuardrailsEngine` + an indirect-injection
  heuristic pack before re-injection; optional LLM classifier wired via
  `providers/` (metered by Budget). Closes **indirect prompt injection**.
- **`prismal.security.output_validator`** — `OutputValidator`
  (`validate_tool_args` schema check + `validate_freeform` path/command/html
  escaping; paths delegate to `filesystem_guard`). Closes **Improper Output
  Handling**.
- **`prismal.security.tool_policy`** — identity-agnostic `ToolPolicyEngine`
  (`(agent, tool, args)` → allow/deny/require-HITL + per-run rate limits), YAML
  loader (`config/tool_policies.yaml`; example shipped). Closes **Excessive
  Agency** at the tool boundary.
- **`prismal.security.runaway`** — `RunawayGuard` (explicit step cap + stagnation
  detection); shares the per-run registry with the Budget guard. Closes
  **Unbounded Consumption** within budget.
- **Settings** (`core/config.py`) — `hardening_enabled`, `hardening_mode`
  (`off|warn|enforce`), `taint_tracking_enabled`, `hardening_injection_threshold`,
  `hardening_injection_classifier`, `output_validation_enabled`,
  `tool_policy_path`, `hardening_tool_policy_default`, `hardening_runaway_max_steps`,
  `hardening_runaway_stagnation_window`, `hardening_pii_output`.
- **Exceptions** (`core/exceptions.py`) — `HardeningError` hierarchy
  (`IndirectInjectionBlocked`, `OutputValidationError`, `ToolPolicyDenied`,
  `RunawayStopped`, `HardeningConfigError`).
- **Observability** (`monitoring/otel.py`) — counters
  `prismal.guardrail_blocks_total`, `prismal.injection_detected_total`,
  `prismal.output_rejected_total`, `prismal.tool_policy_denied_total`,
  `prismal.runaway_stops_total`.
- **Docs & example** — `docs/security/runtime-hardening.md`;
  `examples/runtime_hardening.py`.

#### Changed — runtime hardening (Phase H)

- `react_loop` checks untrusted tool/RAG results before re-injection and ticks
  the `RunawayGuard` next to the Budget check (stop → graceful partial).
- `ActionInterceptor.check()` consults `ToolPolicyEngine` via its
  `_tool_call_checker` seam; `REQUIRE_HITL` routes through `hitl_gate()`.
- `pii_sanitizer` gains `redact_output` so PII redaction also covers agent output.
- `@prismal_node` middleware chain extends with taint-in / output-validator / pii
  stages, all gated on `hardening_enabled`.

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
(`lightagent-agents` → `prismal`). See `docs/history/PLAN_MIGRACION.md`.

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
Their history lives in the `lightagent` monorepo's own CHANGELOG (not part of this repository).
