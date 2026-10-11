# Prismal — Specs Roadmap

| Field | Value |
|---|---|
| **Last updated** | 2026-10-11 |
| **Package version** | 3.19.0 |
| **Author** | Ernesto Crespo |
| **Latest spec** | `token-efficiency/` (TKE) · `llm-gateway-providers/` (GW) · `typed-decision-models/` (DEC) · `adaptive-model-routing/` (AMR) · `protocol-upgrades-2026/` (PRT) · `agentic-memory/` (MEM) — 2026-10-04 |
| **Latest shipped** | `agentic-memory/` (Phase MEM) — v3.18.0, 2026-10-07; PRT + HRS v3.17.0; AMR v3.16.0; DEC v3.15.0; GW v3.14.0; TKE v3.13.0 — wave 2026-10 released together under tag `prismal/v3.18.0` |

Living index of every SDD under `specs/`: what has shipped, what is pending,
and in which order the pending work fits together. Statuses are verified
against the codebase, not just each spec's own `Status` field (spec `Status`
markers were reconciled with the codebase on 2026-06-07).

Status legend: ✅ `IMPLEMENTED` · 📋 `READY` (full SDD, not implemented).

---

## ✅ Implemented

| Spec | Phase | Status | Delivered |
|---|---|---|---|
| [`advanced-architectures/`](./advanced-architectures/) | A–E | ✅ | 7 RAG engines (HyDE, Fusion, Hybrid, Self-RAG, Hierarchical, Multi-Vector, Adaptive), 7 agent patterns (ToT, Debate, Constitutional, LATS, LLM-Compiler, MoA, Swarm), 5 subgraph pipelines (customer_service, document_generation, data_etl, code_review, debate_consensus) |
| [`multimodal-agents/`](./multimodal-agents/) | F | ✅ | Vision/Audio/Video agents, modality router, multimodal fusion, `multimodal_pipeline` subgraph, multimodal RAG, `MediaValidator`. Opt-in: `multimodal_enabled` |
| [`extension-surface/`](./extension-surface/) | X | ✅ | `prismal.langgraph` re-export, `@prismal_node`, `PrismalStateGraphBuilder`, plugin discovery (entry points), `LangChainRunnableAdapter`, formal `Protocol` ports. Docs: `docs/extension.md` |
| [`tool-provider-injection/`](./tool-provider-injection/) | Y | ✅ | `ToolProviderPort` hexagonal inversion: `McpToolProvider`/`SkillToolProvider`/`CompositeToolProvider`/`FakeToolProvider`; `agents/**` no longer imports `prismal.mcp`/`prismal.skills` (AST-guarded). Docs: `docs/tool-providers.md` |
| [`kokoro-deliberation/`](./kokoro-deliberation/) | K | ✅ | Souls tier (`prismal/souls/`), `SoulAgent`, bounded `deliberate()`, `KokoroJudgeAgent` (gated single action), `kokoro` subgraph + supervisor route. Opt-in: `kokoro_enabled`. Docs: `docs/kokoro.md` · **v3.1.0** |
| [`skynet-swarm/`](./skynet-swarm/) | S | ✅ | `SkynetSupervisor` (dynamic/fixed swarm sizing, cap + deferred overflow), `SwarmWorker` (ToolProviderPort + gated actions), `reduce_results`, `skynet` subgraph (Send fan-out + bounded re-plan loop) + supervisor route. Opt-in: `skynet_enabled`. Docs: `docs/skynet.md` · **v3.1.0** |
| [`dependency-security-remediation/`](./dependency-security-remediation/) | — | ✅ | 18 Dependabot alerts triaged and remediated (2026-06); decision matrix + `remediation-tracker.csv`. **Merged to `main` (PR #10)** — Dependabot now reports 0 open alerts |
| [`vector-store-port/`](./vector-store-port/) | Z | ✅ | `VectorStorePort` (Protocol) + `VectorStoreFactory` selectable via `settings.vector_store_backend` (default `chroma`); adapters in `rag/stores/` (chroma moved + shim, lancedb, sqlite_vec, qdrant, pgvector) with deferred imports + normalized score `[0,1]`; RAG + memory retyped; `FakeVectorStore`; extras `[lancedb]`/`[sqlite-vec]`/`[qdrant]`/`[pgvector]`. Docs: `docs/vector-stores.md` |
| [`composition-root/`](./composition-root/) | R | ✅ | `build_runtime(settings, *, org_id=None)` composition facade in `prismal/composition/` — assembles all ports (tool provider Y, vector store Z, embeddings, checkpointer, audit) into a `RuntimeContext` with coordinated `aclose()`; `global`/`context` modes (`settings.runtime_mode`); per-`org_id` collection isolation (`collection_for`); `VectorStoreProvider`/`VectorStoreProviderPort`; `build_test_runtime` fakes; pure config loaders. Additive/opt-in. Docs: `docs/composition-root.md` · **v3.1.3** |
| [`config-source-injection/`](./config-source-injection/) | W | ✅ | `ConfigSourcePort` hexagonal inversion of configuration: the core stops reading `.env`/`os.environ` and consumes an injected source. `EnvConfigSource` (default, byte-for-byte parity) + `MappingConfigSource`/`ChainedConfigSource`/`FakeConfigSource`; `set_config_source`/`build_settings`/`reload_settings`; `env_file` dropped from `Settings`; import-time `env_compat` mutation removed (legacy `LIGHTAGENT_` mirror folded into `EnvConfigSource`); `tavily_api_key`/`config_source_strict` fields + `ConfigSourceError`; raw `os.getenv` reads relocated (tavily, mcp `resolve_secret`); composition-root `apply_org_overrides(*, source=)`; AST guard. Additive/opt-in. Docs: `docs/configuration.md` · **v3.1.4** |
| [`cost-budget-governance/`](./cost-budget-governance/) | C | ✅ | `prismal/budget/` enforcement layer (opt-in `budget_enabled`): `Budget`/`Usage`/`BudgetStatus` value objects (`0`=unlimited), `CostMeter` (auto token+cost extraction; `providers/cost.py` litellm-native + pricing-table fallback; OTel + `CostTracker` bridge), `BudgetGuard` (soft-cap degrade / hard-cap abort) + `make_budget_guard_fn`; `react_loop` metering + graceful partial; `budget_guard_fn` in the 5 expensive patterns (debate/ToT/LATS/MoA/reflection); per-turn seeding via an in-process registry (no live objects in checkpointed state); **unifies the dormant `skynet_token_budget`** under `BudgetExceeded`. Docs: `docs/budget.md` · **v3.1.5** |
| [`runtime-hardening/`](./runtime-hardening/) | H | ✅ | `prismal/security/` hardening layer (opt-in `hardening_enabled`, `off\|warn\|enforce`): taint tracking (`taint.py` + per-run registry), `IndirectInjectionDetector` (reuses `GuardrailsEngine` + heuristic pack; optional LLM classifier in `providers/`), `OutputValidator` (tool-arg schema + path/command/html), identity-agnostic `ToolPolicyEngine` (allow/deny/HITL/rate-limit, `config/tool_policies.yaml`), `RunawayGuard` (step cap + stagnation), PII-on-output; wired into `react_loop` + the `@prismal_node` middleware + supervisor seeding; 5 security OTel counters; graph byte-for-byte unchanged when off. Docs: `docs/security/runtime-hardening.md` · **v3.2.0** |
| [`agent-eval-harness/`](./agent-eval-harness/) | V | ✅ | `prismal/eval/` system-level evaluation (sibling of the runtime; imports only the public graph entry + ports, AST-guarded): `EvalRunner` over `astream` + `build_test_runtime` fakes, trajectory capture, assertions (exact/semantic/tool-usage/llm-judge/groundedness/security), LLM-as-judge, regression gate, `redteam/` containment suite (`tests/eval/redteam/corpus.yaml`), JSON/MD/Langfuse report, `python -m prismal.eval` CLI. Fakes by default, `live_api` opt-in. Docs: `docs/eval.md` · **v3.3.0** |
| [`agent-identity-governance/`](./agent-identity-governance/) | IDN | ✅ | `prismal/identity/` hexagonal package: `AgentIdentity` + W3C DID (`did:key` offline + `did:web` for A2A), scoped `CredentialVault` (`EnvVault` via `ConfigSourcePort` / encrypted `FileVault` / `FakeVault`), OAuth on-behalf-of delegation (narrow-only `propagate`), identity-aware `PolicyEngine` that **delegates** `(agent, tool, args)` to the Phase H `ToolPolicyEngine`; wired at the `ActionInterceptor` seam + composed per `org_id` in `build_runtime`. Opt-in: `identity_enabled` (graph snapshot-tested). ID6-02 (PermissionManager-DID) deferred. Docs: `docs/identity.md` · **v3.4.0** |
| [`a2a-interop/`](./a2a-interop/) | I | ✅ | `prismal/a2a/` (`[a2a]` extra): A2A v0.3.x types, `build_agent_card` (registry + allowlist + `did:web` + per-org cache), inbound `A2AServerHandler` (JSON-RPC `message/send`/`tasks/get`/`tasks/cancel` + SSE, sanitized graph mapping, `AuthContext` strict gate), outbound `A2AClient`/`A2AConnectionManager` (allowlist + pool + bearer/OAuth2 auth) + `A2AAgentNode.as_node` (`@prismal_node`), `A2AToolProvider` (conforms to `ToolProviderPort`). Remote content L1-sanitized + audited; `build_runtime(graph=, a2a_agents=)` composes it; `RuntimeContext.a2a_handler`. Opt-in: `a2a_enabled`. Docs: `docs/a2a.md` · **v3.5.0** |
| [`blind-review-pipeline/`](./blind-review-pipeline/) | BRP | ✅ | `prismal/agents/subgraphs/blind_review_pipeline/` opt-in subgraph (`blind_review_pipeline_enabled`): spec → implement → two **blind** reviewers (no `state["messages"]` access — input contract + AST guard + runtime `BlindnessGuard`) → deterministic `synthesize_verdicts` → bounded correction loop + reused HITL trio. Per-role model/tools via `ProviderRegistry`/`ToolProviderPort`; supervisor route + intent gated on the flag (graph snapshot-tested). Docs: `docs/blind-review-pipeline.md` · **v3.11.0** |
| [`guardrails-modernization/`](./guardrails-modernization/) | GRD | ✅ | `config/nemo_rails/` (finally ships the config `NemoRailsLayer` always expected) + `security/nemo_actions.py::content_safety_reasoning` (reasoning-capable safety-classifier NeMo custom action, settings-driven main-LLM resolution via `providers/`, independent timeout budget, fail-open). `security/structured_output_guard.py::StructuredOutputGuard` (bounded, Budget-metered re-ask over `guardrails-ai`'s `Guard.validate()` — no LLM call inside guardrails-ai itself; opt-in Hub validators; composes with `OutputValidator`). Opt-in: `nemo_classifier_enabled`, `structured_output_guard_enabled`; `[guardrails-ai]` extra. Docs: `docs/security/guardrails-modernization.md` · **v3.6.0** |

| [`loop-hardening/`](./loop-hardening/) | LH | ✅ | `ContextCompactor` (truncate / summarize via `RemoveMessage`) + `resolve_phase()` and phase-based tool gating in `CompositeToolProvider`. Opt-in: `context_compaction_enabled`, `tool_gating_enabled`. Amended by TKE3/TKE4 (mask mode, observation masking). Docs: `docs/loop-hardening.md` · **v3.7.0** |
| [`node-io-typesafety/`](./node-io-typesafety/) | NTS | ✅ | Opt-in per-node Pydantic `input_model`/`output_model` at the `@prismal_node` boundary (`off\|warn\|enforce`); 3 pilot nodes; `NodeIOValidatorPort` deferred (DD-NTS-004). Docs: `docs/node-typesafety.md` · **v3.8.0** |
| [`observability-integration/`](./observability-integration/) | OBS | ✅ | Opt-in `ObservabilityPort` (queryable `RunSummary`, LangSmith/Langfuse parity, dataset export). Extended by TKE1 (cache fields) and HRS (host read surface). Docs: `docs/observability-integration.md` · **v3.9.0** |
| [`agent-identity-governance/` ID8 addendum](./agent-identity-governance/) | IDN | ✅ | `PermissionManager` grants keyed by DID (closes ID6-02) · **v3.10.0** (3.10.1/3.10.2: A2A card re-exports) |
| [`skynet-swarm-plus/`](./skynet-swarm-plus/) | S+ | ✅ | Heterogeneous specialist swarms (`RoleRegistry`), truthfully metered workers (shared `CostMeter`), remote A2A workers. Opt-in. Docs: `docs/skynet.md` · **v3.12.0** |
| [`token-efficiency/`](./token-efficiency/) | TKE | ✅ | Measure-first token layer, opt-in (`token_efficiency_enabled`): cache-aware accounting + `TokenReport`/`token-gate`, `CacheAwareChatModel` prompt caching + cache-stable prompt layout (HMAC prefix canary, red-team leak parity), mask-mode tool gating + BM25 deferred tools (delta to LH2), observation masking + `ArtifactStore` + hysteresis (delta to LH1), tool-output budgets + TOON, effort per role, handoff envelopes, RAG context budget (`[rerank]`/`[compression]`). TKE10 deferred; baseline capture pending (TKE1-08). Docs: `docs/token-efficiency.md` · **v3.13.0** |
| [`llm-gateway-providers/`](./llm-gateway-providers/) | GW | ✅ | `providers/gateways/`: `GatewayPort` + router (ContextVar/global), `GatewayChatLiteLLM` (served model / billed cost capture, 402 mapping, quota preflight), OpenRouter (ZDR + `data_collection:deny` defaults, sticky `session_id`, gateway-owned fallback, `/key` + `/generation`), OpenCode Zen/Go (protocol map, Go ToS guard, `QuotaLedger`), OmniRoute (fail-closed startup gate, floor 3.8.51), generic OpenAI-compatible; no double fallback; `subscription` cost mode; reconciler + quota gauge; per-tenant vault keys in `build_runtime`. Opt-in `llm_gateways_enabled`. Docs: `docs/gateways.md` · **v3.14.0** |
| [`typed-decision-models/`](./typed-decision-models/) | DEC | ✅ | `decisions/`: `DecisionPort` + cascade rule → Laya (local) → Jev (TypeSafe / OpenRouter / laya-serve) → LLM; `DecisionStateBuilder` (fenced untrusted state, EN/ES authority-claim taint), forbidden-use lint, τ per risk tier + derangement shuffle, model-pinned calibration (temperature / isotonic, ES slice), shadow → enforce with drift demotion, hash-only audit; wired into supervisor routing, CRAG grading and an escalate-only guardrail pre-screen; HITL/tool/eval/ctx/swarm as library functions. Opt-in `decisions_enabled`. Docs: `docs/decisions.md` · **v3.15.0** |
| [`adaptive-model-routing/`](./adaptive-model-routing/) | AMR | ✅ | `routing/`: `RoutingPolicyPort` + static parity tier map, budget-aware soft-cap downgrade, cascade with DEC adequacy check + cache affinity, OmniRouter-style batch assignment (Lagrangian dual + greedy, Skynet/MoA), Beta-Binomial predictor, intent-keyed plan cache (`LLMCompiler`), response cache, `BatchExecutor` (Anthropic / OpenAI / OpenRouter `:batch`); fail-closed risk floors; `get_llm(features=, state=)`; eval `routing-gate`. Opt-in `adaptive_routing_enabled` (shadow first). Docs: `docs/routing.md` · **v3.16.0** |
| [`protocol-upgrades-2026/`](./protocol-upgrades-2026/) | PRT | ✅ | A2A v0.3 + v1.0 dual-stack (`a2a/v1/`, header/method negotiation, codecs, per-version Agent Card, task store + `ListTasks`, PKCE/device-code OAuth, cross-tenant task guard), MCP 2026-07-28 stateless adapter (`legacy`/`stateless`/`auto`, in-house on `httpx`, `mcp` 1.28.1 kept) + `ttlMs` tool-list cache + tasks extension + input requests, AG-UI 1.0 translator (`events/agui.py`), 12-key usage wire; `interop` conformance marker. Wire pins: ADR-PRT-001. Docs: `docs/protocols-2026.md` · **v3.17.0** |
| [`composition-root/` HRS delta](./composition-root/DELTA-2026-10-host-read-surface.md) | HRS | ✅ | `RuntimeContext` read methods (`budget_status`, `quota_windows`, `audit_page`, `verify_audit_chain`, `decisions_report`, `routing_report`, `a2a_routes`), frozen view types, in-process stats recorders, `build_runtime(config_source=)`. Docs: `docs/composition-root.md` §8 · **v3.17.0** |
| [`agentic-memory/`](./agentic-memory/) | MEM | ✅ | `memory/v2/`: `MemoryPort` over the legacy stores (wrapped, never edited) with one-tenant `MemoryService`, decision-gated typed writes + taint T1–T7, budgeted recall (sufficiency/stop, prompt tail block), sleep-time consolidation (supersedes, never deletes), ACE playbooks, SQLite temporal graph + optional Graphiti (`[graph-memory]`), rule/HITL-only forget (AST-guarded `purge`), `CascadeDecisionPort`, synthetic LoCoMo-style eval. Opt-in `memory_v2_enabled` (shadow first). Docs: `docs/memory.md` · **v3.18.0** |

## 📋 Ready to implement (full SDD: PLAN + SPEC + ARCHITECTURE + TASKS)

Sourced from the 2026-10-04 gap analysis
(`docs/gap-analysis-gateways-tokens-decisions-2026-10.md`); all six (TKE, GW, DEC, AMR, PRT + HRS, MEM) are now implemented; no full in-repo SDD is pending. Each spec opens with a
**Phase 0 "verify before build"** for facts marked **[verify]** (post-mid-2026, single-source or vendor-published).

_None — every in-repo SDD is implemented (see the table above). Open follow-ups are tracked in each spec's `TASKS.md`._

Satellite repos received matching specs on the same date: `prismal-server/specs/host-v0.2-usage-gateways-agui/` (RH2, 0.2.0),
`prismal-sdk/specs/client-v0.2-usage-agui/` (CS2, 0.2.0), `prismal-dashboard/specs/usage-and-gateway-console/` (DSH2, 0.2.0)
and `DELTA-2026-10-usage-agui.md` in the webchat / tui / chatbot bootstrap specs.

### Target package versions (strict SemVer — minor per phase)

Current: **`3.19.0`** (DEC Ollama delta; MEM `3.18.0`; PRT + HRS `3.17.0`; AMR `3.16.0`; DEC `3.15.0`; GW `3.14.0`; TKE `3.13.0`; all phases through S+ shipped; LH `3.7.0`, NTS `3.8.0`, OBS `3.9.0`, ID8 `3.10.0`, BRP `3.11.0`, S+ `3.12.0`).

| Order | Phase | Spec | Target version |
|---|---|---|---|
| ✅ 1 | TKE | [`token-efficiency/`](./token-efficiency/) | **`3.13.0`** (implemented 2026-10-05) |
| ✅ 2 | GW | [`llm-gateway-providers/`](./llm-gateway-providers/) | **`3.14.0`** (implemented 2026-10-05) |
| ✅ 3 | DEC | [`typed-decision-models/`](./typed-decision-models/) | **`3.15.0`** (implemented 2026-10-05) |
| ✅ 4 | AMR | [`adaptive-model-routing/`](./adaptive-model-routing/) | **`3.16.0`** (implemented 2026-10-06) |
| ✅ 5 | PRT + HRS | [`protocol-upgrades-2026/`](./protocol-upgrades-2026/) + host read surface delta | **`3.17.0`** (implemented 2026-10-06) |
| ✅ 6 | MEM | [`agentic-memory/`](./agentic-memory/) | **`3.18.0`** (implemented 2026-10-07) |

> A2A v0.3 removal (PRT) is the first planned breaking change → `4.0.0`, not before all hosts speak v1.0.

> Versioning note: the earlier additive phases Z/R/W/C were released as **patches**
> (`3.1.2`–`3.1.5`) to avoid bumping the minor too quickly. Going forward, feature
> phases follow **strict SemVer**: new functionality ⇒ a **minor** bump (a breaking
> change would be a major, but all pending phases are opt-in and snapshot-safe, so
> none is expected). Each spec's metadata table records its `Target package version`.

> **Out-of-repo host (not tracked here):** the `prismal-server` reference host
> (REST/WS/SSE surface, `/a2a` mount, auth) is a **separate repository** and is
> deliberately **out of this roadmap's scope** — nothing about it can be
> implemented inside `prismal-ai/prismal`. Its scoping seed lives with the
> sibling `prismal-server` repo, not here.

## Suggested order

1. ~~**`vector-store-port` (Z)**~~ — ✅ **shipped**: `VectorStorePort` +
   `VectorStoreFactory`, adapters in `rag/stores/`, default Chroma. Unblocked
   `composition-root`.
2. ~~**`composition-root` (R)**~~ — ✅ **shipped** (v3.1.3): `build_runtime` composes
   Y + Z + embeddings/checkpoint/audit into one `RuntimeContext`; `global`/`context`
   modes; per-`org_id` collection isolation. The `prismal-server` /
   `prismal-dashboard` ecosystem can now build on a single composition contract.
2b. ~~**`config-source-injection` (W)**~~ — ✅ **shipped** (2026-06-10):
   `ConfigSourcePort` inverts configuration so the core stops reading `.env`;
   `EnvConfigSource` default keeps byte-for-byte parity; `set_config_source` /
   `build_settings` (per-tenant); import-time `env_compat` mutation removed; AST
   guard forbids new config `os.getenv` in the core. Strengthens
   `composition-root` (per-tenant config sources via `apply_org_overrides(*, source=)`).
3. ~~**`cost-budget-governance` (C)**~~ — ✅ **shipped**: `prismal/budget/`
   meters real usage per run and enforces soft/hard caps in `react_loop` and the
   expensive patterns; unifies the dormant `skynet_token_budget`. Opt-in via
   `budget_enabled`.
4. ~~**`runtime-hardening` (H)**~~ — ✅ **shipped** (v3.2.0): security hardening
   derived from the 2026 OWASP LLM/Agentic Top 10 research
   (`docs/security/hardening-and-harness-engineering.md`): indirect-injection
   containment, output validation, tool policy, runaway guard, taint tracking,
   PII-on-output. Additive/opt-in (`hardening_enabled`); extends `security/` and
   reuses the budget per-run registry. Docs: `docs/security/runtime-hardening.md`.
5. ~~**`agent-eval-harness` (V)**~~ — ✅ **shipped** (v3.3.0, 2026-06-15):
   `prismal/eval/` runs eval-sets against the real graph with `build_test_runtime`
   fakes, captures trajectories, scores (exact/semantic/llm-judge/tool-usage/
   groundedness), gates regressions, and runs the red-team containment suite — the
   **executable proof** for Phase H controls. Fakes in CI; `live_api` opt-in.
6. ~~**`agent-identity-governance` (IDN)**~~ — ✅ **shipped** (v3.4.0,
   2026-06-16): `prismal/identity/` (DID, scoped credential vault, on-behalf-of
   delegation, identity-aware `PolicyEngine` that delegates to Phase H's
   `ToolPolicyEngine`); wired at the `ActionInterceptor` seam + composed per
   `org_id`. Opt-in (`identity_enabled`), graph snapshot-tested. ID6-02
   (PermissionManager-DID) deferred (needs an Alembic migration).
7. ~~**`a2a-interop` (I)**~~ — ✅ **shipped** (v3.5.0, 2026-06-17): `prismal/a2a/`
   bidirectional A2A interop — types, `build_agent_card`, inbound
   `A2AServerHandler` (JSON-RPC + SSE), outbound `A2AClient`/`A2AAgentNode`/
   `A2AConnectionManager` + `A2AToolProvider` (conforms to `ToolProviderPort`);
   **consumes** the Phase IDN DID; everything remote is L1-sanitized + audited;
   `build_runtime(graph=, a2a_agents=)` composes it. Opt-in (`a2a_enabled`),
   graph unchanged. Enables Skynet S+ remote workers. This was the last
   pending phase of the *original* roadmap — the 2026-07-04 gap analysis then
   opened four new ones (below).
8. ~~**`guardrails-modernization` (GRD)`**~~ — ✅ **shipped** (v3.6.0,
   2026-07-04): `config/nemo_rails/` finally ships (L3 was a code-complete
   no-op — no config directory existed); `security/nemo_actions.py` adds a
   reasoning-capable safety-classifier rail with its own timeout budget and
   settings-driven main-LLM resolution via `providers/`;
   `security/structured_output_guard.py::StructuredOutputGuard` adds bounded,
   Budget-metered re-ask over `guardrails-ai` (schema-check only, no LLM call
   inside the SDK — zero provider-isolation compromise) plus opt-in Hub
   validators, composing with `OutputValidator`. Opt-in
   (`nemo_classifier_enabled`, `structured_output_guard_enabled`), graph
   unchanged. Next: `loop-hardening` (LH), `node-io-typesafety` (NTS),
   `observability-integration` (OBS) — all spec-ready, none started.

9. **Wave 2026-10** (from `docs/gap-analysis-gateways-tokens-decisions-2026-10.md`):
   TKE1 accounting first (baseline in the eval harness) → TKE2–TKE5 (caching, cache-safe tools,
   observation masking, tool budgets) → GW (OpenRouter → OpenCode Zen/Go → OmniRoute) → DEC in shadow
   mode → AMR ∥ PRT + HRS → `prismal-server` 0.2 → `prismal-sdk` 0.2 → MEM → front-ends
   (dashboard bootstrap + console → webchat → tui → chatbot).
   Consolidated cross-spec plan (milestones M0–M7, Track P parallel work, gaps G1–G8):
   [`wave-2026-10-implementation-plan.md`](./wave-2026-10-implementation-plan.md). **M0 (spec
   reconciliation) done 2026-10-05**: `docs/adr/ADR-WAVE-001-llm-construction-contract.md`, TKE phase
   order fixed, AMR master flag added. Docs hygiene checked: GitHub Release `prismal/v3.12.0` exists
   (2026-07-20); LH/NTS/OBS (3.7–3.8) never got their own tags because they shipped together in
   `prismal/v3.9.0`, so no retro-tags are needed.

## Maintenance notes

- When a phase ships: set `Status` → `IMPLEMENTED` in its four docs, mark its
  TASKS rows `DONE`, and update this file. (Stale `DRAFT` markers in the A–E,
  F, X, Y specs were fixed on 2026-06-07 — all implemented specs are now
  consistent.)
- New specs follow the same SDD layout: `PLAN.md` + `SPEC.md` +
  `ARCHITECTURE.md` + `TASKS.md` (see `kokoro-deliberation/` or
  `skynet-swarm/` as reference implementations of the full cycle).
