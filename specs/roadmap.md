# Prismal — Specs Roadmap

| Field | Value |
|---|---|
| **Last updated** | 2026-06-10 |
| **Package version** | 3.1.3 |
| **Author** | Ernesto Crespo |
| **Latest spec** | `config-source-injection/` (Phase W) — 2026-06-07 |
| **Latest shipped** | `config-source-injection/` (Phase W) — 2026-06-10 |

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
| [`config-source-injection/`](./config-source-injection/) | W | ✅ | `ConfigSourcePort` hexagonal inversion of configuration: the core stops reading `.env`/`os.environ` and consumes an injected source. `EnvConfigSource` (default, byte-for-byte parity) + `MappingConfigSource`/`ChainedConfigSource`/`FakeConfigSource`; `set_config_source`/`build_settings`/`reload_settings`; `env_file` dropped from `Settings`; import-time `env_compat` mutation removed (legacy `LIGHTAGENT_` mirror folded into `EnvConfigSource`); `tavily_api_key`/`config_source_strict` fields + `ConfigSourceError`; raw `os.getenv` reads relocated (tavily, mcp `resolve_secret`); composition-root `apply_org_overrides(*, source=)`; AST guard. Additive/opt-in. Docs: `docs/configuration.md` |

Deferred to follow-up phases (noted in their specs): Skynet S+ (heterogeneous
specialist swarms, token-budget *enforcement* — the `skynet_token_budget`
setting and `SkynetBudgetExceeded` already ship —, remote workers via A2A).

## 📋 Ready to implement (full SDD: PLAN + SPEC + ARCHITECTURE + TASKS)

| Spec | Phase | What it adds | Depends on |
|---|---|---|---|
| [`a2a-interop/`](./a2a-interop/) | I | Bidirectional A2A (Agent2Agent) interoperability: JSON-RPC over HTTP(S)+SSE, Agent Card at `/.well-known/agent-card.json`, discovery/delegation with external agents (Google ADK, MS Agent Framework, …) | Benefits from agent-identity (DID) |

## 🌱 Seed PRDs (PLAN only — ARCHITECTURE/SPEC/TASKS must be written first)

| Spec | What it adds | Depends on |
|---|---|---|
| [`cost-budget-governance/`](./cost-budget-governance/) | Per-run/session/tenant budgets, real-time cost/token metering, soft/hard circuit-breakers. Critical for the expensive patterns (debate, ToT, LATS, MoA, **Skynet swarms**) | — (Skynet's `skynet_token_budget` is a ready integration point) |
| [`agent-eval-harness/`](./agent-eval-harness/) | System-level evaluation (the "scaffold gap"): trajectories, tool usage, RAG fidelity, adversarial robustness, cross-version regression | — |
| [`agent-identity-governance/`](./agent-identity-governance/) | Per-agent identity (W3C DID), scoped per-agent credentials, OAuth-on-behalf delegation, auditable access policies | Foundation for A2A (I) and multi-tenant (R) |

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
3. **`cost-budget-governance`** — first seed to mature into a full SDD: the
   expensive patterns (and now Skynet swarms) run uncapped on spend today.
4. **`agent-identity-governance`** → **`a2a-interop` (I)** — identity first,
   then the interop layer that consumes it (and enables Skynet S+ remote
   workers).
5. **`agent-eval-harness`** — valuable at any point; most leverage once the
   surface above stabilizes.

## Maintenance notes

- When a phase ships: set `Status` → `IMPLEMENTED` in its four docs, mark its
  TASKS rows `DONE`, and update this file. (Stale `DRAFT` markers in the A–E,
  F, X, Y specs were fixed on 2026-06-07 — all implemented specs are now
  consistent.)
- New specs follow the same SDD layout: `PLAN.md` + `SPEC.md` +
  `ARCHITECTURE.md` + `TASKS.md` (see `kokoro-deliberation/` or
  `skynet-swarm/` as reference implementations of the full cycle).
