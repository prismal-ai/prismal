# Prismal — Specs Roadmap

| Field | Value |
|---|---|
| **Last updated** | 2026-06-07 |
| **Package version** | 3.1.1 |
| **Author** | Ernesto Crespo |
| **Latest spec** | `config-source-injection/` (Phase W) — 2026-06-07 |

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

Deferred to follow-up phases (noted in their specs): Skynet S+ (heterogeneous
specialist swarms, token-budget *enforcement* — the `skynet_token_budget`
setting and `SkynetBudgetExceeded` already ship —, remote workers via A2A).

## 📋 Ready to implement (full SDD: PLAN + SPEC + ARCHITECTURE + TASKS)

| Spec | Phase | What it adds | Depends on |
|---|---|---|---|
| [`vector-store-port/`](./vector-store-port/) | Z | `VectorStorePort` (Protocol) + `VectorStoreFactory` selectable via `settings.vector_store_backend`, with adapters for Chroma (default, zero breakage), LanceDB, sqlite-vec, Qdrant, pgvector. Covers RAG + memory; consumers only change their type hint | — (additive; mirrors Fase Y) |
| [`composition-root/`](./composition-root/) | — | `build_runtime(settings, *, org_id=None) -> RuntimeContext`: single composition point injecting all ports (tool provider Y, vector store Z, embeddings, checkpointer, audit) with per-tenant resolution. The contract `prismal-server` / `prismal-dashboard` build on | Y ✅, **Z** (vector-store-port) |
| [`config-source-injection/`](./config-source-injection/) | W | `ConfigSourcePort` hexagonal inversion of configuration: the core stops reading `.env`/`os.environ` and consumes an injected source (`EnvConfigSource` default, `Mapping`/`Chained`/`Fake`, Vault/AWS host sources). `Settings` keeps its schema; `env_file` and import-time `env_compat` mutation removed; the ~6 direct `os.getenv` reads relocated onto `Settings`. Lets `prismal-server`/`dashboard`/secrets managers own config; threads per-tenant sources through the composition root. Additive, opt-in, byte-for-byte backward compatible | — (additive; mirrors Fase Y) |
| [`a2a-interop/`](./a2a-interop/) | I | Bidirectional A2A (Agent2Agent) interoperability: JSON-RPC over HTTP(S)+SSE, Agent Card at `/.well-known/agent-card.json`, discovery/delegation with external agents (Google ADK, MS Agent Framework, …) | Benefits from agent-identity (DID) |

## 🌱 Seed PRDs (PLAN only — ARCHITECTURE/SPEC/TASKS must be written first)

| Spec | What it adds | Depends on |
|---|---|---|
| [`cost-budget-governance/`](./cost-budget-governance/) | Per-run/session/tenant budgets, real-time cost/token metering, soft/hard circuit-breakers. Critical for the expensive patterns (debate, ToT, LATS, MoA, **Skynet swarms**) | — (Skynet's `skynet_token_budget` is a ready integration point) |
| [`agent-eval-harness/`](./agent-eval-harness/) | System-level evaluation (the "scaffold gap"): trajectories, tool usage, RAG fidelity, adversarial robustness, cross-version regression | — |
| [`agent-identity-governance/`](./agent-identity-governance/) | Per-agent identity (W3C DID), scoped per-agent credentials, OAuth-on-behalf delegation, auditable access policies | Foundation for A2A (I) and multi-tenant (R) |

## Suggested order

1. **`vector-store-port` (Z)** — unblocks `composition-root`; additive and
   low-risk (same playbook as the already-shipped Fase Y).
2. **`composition-root`** — composes Y + Z into one runtime facade; unblocks
   the `prismal-server` / `prismal-dashboard` ecosystem.
2b. **`config-source-injection` (W)** — inverts configuration itself so the
   core stops reading `.env`; additive and low-risk (same Fase Y playbook).
   Independent of Z, but strengthens `composition-root` (per-tenant config
   sources). Can land in parallel with Z.
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
