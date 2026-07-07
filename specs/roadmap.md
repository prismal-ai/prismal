# Prismal — Specs Roadmap

| Field | Value |
|---|---|
| **Last updated** | 2026-07-04 |
| **Package version** | 3.6.0 |
| **Author** | Ernesto Crespo |
| **Latest spec** | `guardrails-modernization/` (Phase GRD) |
| **Latest shipped** | `guardrails-modernization/` (Phase GRD) — 2026-07-04 |

Living index of every SDD under `specs/`: what has shipped, what is pending,
and in which order the pending work fits together. Statuses are verified
against the codebase, not just each spec's own `Status` field (spec `Status`
markers were reconciled with the codebase on 2026-06-07).

Status legend: ✅ `IMPLEMENTED` · 📋 `READY` (full SDD, not implemented) ·
🌱 `SEED` (PRD only; ARCHITECTURE/SPEC/TASKS missing).

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
| [`guardrails-modernization/`](./guardrails-modernization/) | GRD | ✅ | `config/nemo_rails/` (finally ships the config `NemoRailsLayer` always expected) + `security/nemo_actions.py::content_safety_reasoning` (reasoning-capable safety-classifier NeMo custom action, settings-driven main-LLM resolution via `providers/`, independent timeout budget, fail-open). `security/structured_output_guard.py::StructuredOutputGuard` (bounded, Budget-metered re-ask over `guardrails-ai`'s `Guard.validate()` — no LLM call inside guardrails-ai itself; opt-in Hub validators; composes with `OutputValidator`). Opt-in: `nemo_classifier_enabled`, `structured_output_guard_enabled`; `[guardrails-ai]` extra. Docs: `docs/security/guardrails-modernization.md` · **v3.6.0** |

Deferred to follow-up phases (noted in their specs): Skynet S+ (heterogeneous
specialist swarms; metering *worker* token usage into the shared swarm budget —
Phase C already enforces `skynet_token_budget` at the supervisor's planner/
evaluator boundary via the unified budget engine; remote workers via A2A).

## 📋 Ready to implement (full SDD: PLAN + SPEC + ARCHITECTURE + TASKS)

Sourced from the 2026-07-04 gap analysis
(`docs/gap-analysis-loops-harness-guardrails-2026-07.md`); none started yet.

| Spec | Phase | What it adds | Depends on |
|---|---|---|---|
| [`loop-hardening/`](./loop-hardening/) | LH | Context compaction (`AgentState.messages` grows unbounded today) + dynamic tool gating by task phase | — |
| [`node-io-typesafety/`](./node-io-typesafety/) | NTS | Pydantic I/O validation contracts per graph node (`AgentState` is still a bare `TypedDict`) | — |
| [`observability-integration/`](./observability-integration/) | OBS | `ObservabilityPort` + LangSmith/Langfuse parity, dataset export, eval-harness integration | Phase V (eval harness) |
| [`reference-host-bootstrap/`](./reference-host-bootstrap/) | — | PLAN-only seed for the *external* `prismal-server` host repo (REST/WS/SSE, `/a2a` mount, auth) — out of this repo's scope | Phase Y, I |

### Target package versions (strict SemVer — minor per phase)

Current: **`3.6.0`**. Each phase was new, additive, opt-in functionality → a **SemVer minor** bump (H `3.2.0`, V `3.3.0`, IDN `3.4.0`, I `3.5.0`, **GRD `3.6.0` all shipped**):

| Order | Phase | Spec | Target version |
|---|---|---|---|
| ✅ | H | [`runtime-hardening/`](./runtime-hardening/) | **`3.2.0`** (shipped) |
| ✅ | V | [`agent-eval-harness/`](./agent-eval-harness/) | **`3.3.0`** (shipped) |
| ✅ | IDN | [`agent-identity-governance/`](./agent-identity-governance/) | **`3.4.0`** (shipped) |
| ✅ | I | [`a2a-interop/`](./a2a-interop/) | **`3.5.0`** (shipped) |
| ✅ | GRD | [`guardrails-modernization/`](./guardrails-modernization/) | **`3.6.0`** (shipped) |
| 📋 | LH | [`loop-hardening/`](./loop-hardening/) | `3.7.0` (spec ready) |
| 📋 | NTS | [`node-io-typesafety/`](./node-io-typesafety/) | `3.8.0` (spec ready) |
| 📋 | OBS | [`observability-integration/`](./observability-integration/) | `3.9.0` (spec ready) |

> Versioning note: the earlier additive phases Z/R/W/C were released as **patches**
> (`3.1.2`–`3.1.5`) to avoid bumping the minor too quickly. Going forward, feature
> phases follow **strict SemVer**: new functionality ⇒ a **minor** bump (a breaking
> change would be a major, but all pending phases are opt-in and snapshot-safe, so
> none is expected). Each spec's metadata table records its `Target package version`.

## 🌱 Seed PRDs (PLAN only — ARCHITECTURE/SPEC/TASKS must be written first)

| Spec | What it adds | Depends on |
|---|---|---|
| [`reference-host-bootstrap/`](./reference-host-bootstrap/) | Scopes the missing `prismal-server` host repo (REST/WS/SSE surface, `/a2a` mount, auth). Out-of-repo — nothing here can be implemented inside `prismal-ai/prismal` itself. | Phase Y, I |

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

## Maintenance notes

- When a phase ships: set `Status` → `IMPLEMENTED` in its four docs, mark its
  TASKS rows `DONE`, and update this file. (Stale `DRAFT` markers in the A–E,
  F, X, Y specs were fixed on 2026-06-07 — all implemented specs are now
  consistent.)
- New specs follow the same SDD layout: `PLAN.md` + `SPEC.md` +
  `ARCHITECTURE.md` + `TASKS.md` (see `kokoro-deliberation/` or
  `skynet-swarm/` as reference implementations of the full cycle).
