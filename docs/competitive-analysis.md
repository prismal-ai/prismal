# Prismal — Competitive Analysis and Production Roadmap

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Date** | 2026-06-06 |
| **Status** | `DRAFT` (historical framing; prismal status lines updated 2026-10-12 for v3.19.0) |
| **Basis** | Structure of the `prismal` repo (v3.0.0, ~52k LOC, 191 test files) + specs in `specs/` + Obsidian notes `Documentacion/Prismal` |
| **Scope** | Positioning versus 2026 agent frameworks, weaknesses, and a prioritized feature roadmap |

> Caveat: this analysis is based on the repo structure, the specs, and the notes — not on running the suite or auditing each capability end-to-end. The specs describe the *intended* design; actual maturity may be lower.

> **Updated 2026-10-12 for v3.19.0:** only the statements about *prismal's own* features and gaps were brought up to date; the competitor descriptions and sources are unchanged from 2026-06-06 and were not re-verified. Since the 2026-07-04 update below, prismal shipped: Guardrails modernization (v3.6.0), Loop Hardening — context compaction + phase-based tool gating (v3.7.0), opt-in per-node I/O type-safety (v3.8.0), the `ObservabilityPort` with LangSmith/Langfuse parity (v3.9.0), DID-keyed `PermissionManager` grants (v3.10.0), the Blind Review Pipeline (v3.11.0), Skynet S+ specialist swarms (v3.12.0), and the wave 2026-10: Token Efficiency — cache-aware accounting, prompt caching, tool-output budgets (v3.13.0), LLM gateways — OpenRouter, OpenCode Zen/Go, OmniRoute (v3.14.0), Typed Decision Models (v3.15.0; local Ollama backend and one-way human-review gate in v3.19.0), Adaptive Model Routing (v3.16.0), Protocol upgrades — A2A v0.3 + v1.0 dual-stack, MCP 2026-07-28 stateless + tasks, AG-UI 1.0, plus a host read surface (v3.17.0), and Agentic memory v2 (v3.18.0). All are opt-in, with the graph byte-for-byte unchanged when off. Counts in §1–§2 now read 30 supervisor routes, 11 RAG engines, 7 patterns + 2 primitives, 15 subgraph packages. Weaknesses 2–9 in §3 are closed or reduced (status noted inline); #1 (no deployable host — `prismal-server` and the rest of the upper layer are still not developed in-repo) and #10 (bus factor) remain open. Current per-spec status: [`specs/roadmap.md`](../specs/roadmap.md); the latest gap analysis is [`gap-analysis-gateways-tokens-decisions-2026-10.md`](./gap-analysis-gateways-tokens-decisions-2026-10.md).

> **2026-07-04 update:** every P0–P2 item in §5 below (Tool Provider, Vector Store Port, Composition Root, Dependency Remediation, A2A, Agent Identity, Eval Harness, Cost & Budget Governance) has since shipped — verified against real code, tests, and `CHANGELOG.md`, not just spec metadata. This document is kept for historical framing only; for the current state and the *next* round of gaps (external, versus 2026 Loops/Harness/Guardrails state of the art rather than internal backlog) see [`gap-analysis-loops-harness-guardrails-2026-07.md`](./gap-analysis-loops-harness-guardrails-2026-07.md), whose priority list (§5) is now tracked as SDD artifacts: [`specs/guardrails-modernization/`](../specs/guardrails-modernization/), [`specs/loop-hardening/`](../specs/loop-hardening/), [`specs/node-io-typesafety/`](../specs/node-io-typesafety/), [`specs/observability-integration/`](../specs/observability-integration/), the out-of-repo `prismal-server` reference host (seed removed from this repo's planning 2026-07-12), [`specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md`](../specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md), and [`specs/dependency-security-remediation/ADDENDUM-refresh-2026-07.md`](../specs/dependency-security-remediation/ADDENDUM-refresh-2026-07.md).

---

## 1. What prismal is and what it resembles

Prismal is a **"LangGraph with batteries included"**: it wraps LangGraph (state-graph engine) and adds, in a single package, what is normally assembled from several libraries — supervisor with 30 routes (15 core + 15 opt-in), 11 RAG engines, 7 reasoning patterns + 2 primitives, 15 subgraph packages, 5-layer security plus runtime hardening, sandbox, MCP, A2A, skills, scheduler, and multimodal, plus opt-in budget, token-efficiency, gateway, typed-decision, adaptive-routing and agentic-memory layers *(v3.19.0; at v3.0.0 this read "26 agents, 7 RAG engines, 9 patterns, 12 subgraphs")*.

2026 comparables by piece:

| Framework | Overlap with prismal | Key difference |
|---|---|---|
| **LangGraph** (LangChain Inc.) | It is the substrate that prismal wraps | LangGraph is the engine; prismal adds the batteries |
| **Microsoft Agent Framework** (GA v1.0) | Graph workflows + enterprise features | **Native A2A + MCP**, OTel, Entra ID, managed runtime |
| **Google ADK** | Multi-agent + **model-agnostic via LiteLLM** (same as prismal) | Native **A2A with Agent Cards** |
| **Claude Agent SDK** | Supervisor + subagents + hooks + MCP + skills | Anthropic-native, managed runtime and memory |
| **CrewAI** | Role-based multi-agent | Roles DSL, low learning curve |
| **AutoGen / AG2** | Conversation/debate patterns | Dialogue between agents |
| **LlamaIndex** | RAG-first | Depth in indexed data |
| **Pydantic AI** | — | Type-safety and structured outputs |

**No single framework does exactly the same thing**: prismal is an integrated *meta-assembly*. The closest in philosophy is Microsoft Agent Framework (graph + enterprise + A2A/MCP) and Google ADK (multi-agent + LiteLLM + A2A).

---

## 2. Is it the most complete?

- **By integrated breadth in a single codebase: it is at the top.** Almost no OSS brings together supervisor + advanced RAG (11 engines) + 5-layer security + sandbox + MCP + A2A + skills + scheduler + multimodal out of the box.
- **By product/ecosystem maturity: no.** Completeness in 2026 is also measured by managed runtime, first-party observability (LangSmith), **standard interop (A2A/Agent Cards)**, **identity governance**, system evaluation, and community. Prismal is **beta, essentially a single maintainer**, with the entire upper layer (`prismal-server`, `prismal-dashboard`, `prismal-sdk`) in a **"Planned"** state. *(2026-10-12, v3.19.0: still beta and still single-maintainer, and the upper layer is still not developed — `prismal` is the only repo in active development. Standard interop (A2A v0.3 + v1.0, MCP stateless, AG-UI 1.0), identity governance and system evaluation now ship in the core; a managed runtime, a first-party observability UI and a community are still missing.)*

**Verdict: the most complete as a *library of capabilities*, not as a *production platform*.**

---

## 3. Weaknesses (cross-referenced with the 2026 industry gaps)

1. **Experimentation→production gap.** The #1 failure of the industry and of prismal: the deployment/serving layer is missing (server/dashboard/SDK "Planned"). *Status 2026-10: still open. The framework side is done — `build_runtime()` composition root (v3.1.3), multi-tenant `org_id` composition, and a read-only host surface on `RuntimeContext` (v3.17.0) — but `prismal-server` is not developed yet.*
2. **No A2A / Agent Cards interop.** It has MCP (*tool* interop) but no agent-to-agent protocol; MS Agent Framework and Google ADK already ship it natively (150+ orgs support it). Risk of isolation from the multi-agent ecosystem. *Status: closed — A2A v0.3 inbound/outbound with Agent Cards (v3.5.0), then A2A v1.0 dual-stack on one endpoint with `ListTasks`, PKCE/device-code OAuth and a cross-tenant task guard, MCP 2026-07-28 stateless client with the tasks extension, and an AG-UI 1.0 event translator (v3.17.0). Serving the HTTP routes is the host's job (see #1).*
3. **Agent identity/governance.** The most cited gap in 2026. It has `PermissionManager` + `AuditLogger`, but no per-agent identity (DID), OAuth-on-behalf credentials, or IAM for autonomous agents. *Status: closed — `prismal/identity/` with per-agent DIDs, scoped credential vaults, OAuth on-behalf-of delegation and an identity-aware `PolicyEngine` (v3.4.0); DID-keyed `PermissionManager` grants (v3.10.0). The external IdP remains a host concern.*
4. **No system-level evaluation harness** (the "scaffold gap": evaluating the model in isolation does not predict the agentic system). *Status: closed — `prismal/eval/` runs eval-sets and a red-team suite against the real compiled graph (v3.3.0, CI job `test-eval`), later extended with token, routing and synthetic LoCoMo-style memory gates.*
5. **Cost/latency without governance.** The debate/ToT/LATS/MoA patterns are expensive; no budget/cap per run nor cost circuit-breakers. *Status: closed — Cost & Budget Governance with soft/hard circuit-breakers in `react_loop` and the expensive patterns (v3.1.5), then Token Efficiency (cache-aware accounting, prompt caching, tool-output budgets; v3.13.0), LLM gateways with billed-cost capture and quota ledgers (v3.14.0), Typed Decision Models that answer closed questions without an LLM call (v3.15.0, local Ollama tier in v3.19.0) and Adaptive Model Routing (tiered/cascade/budget-aware/batch model choice; v3.16.0). All opt-in.*
6. **Partial type-safety.** `AgentState` is a `TypedDict` without per-node I/O validation. *Status: reduced — opt-in per-node Pydantic `input_model`/`output_model` at the `@prismal_node` boundary (`off|warn|enforce`, v3.8.0) with three pilot nodes, plus `StructuredOutputGuard` (v3.6.0) and typed decisions (v3.15.0). `AgentState` itself is still a `TypedDict`.*
7. **Observability without its own UI** (OTel/Langfuse; depends on third parties). *Status: reduced — `ObservabilityPort` with a queryable `RunSummary`, LangSmith/Langfuse parity and dataset export (v3.9.0), a 12-key usage wire and host read surface (v3.17.0). A first-party UI is still a host (`prismal-dashboard`) item and not built.*
8. **Dependency/security chain debt** (18 Dependabot alerts; LangChain stack churn). *Status: remediated (18/18, 2026-06; refresh addendum 2026-07) and gated in CI by pip-audit, trivy, gitleaks, semgrep and bandit. A few upstream-unfixed CVEs remain as documented, re-evaluated ignores; LangChain churn is an ongoing cost.*
9. **Pending couplings:** vector store tied to Chroma; multi-tenant not real; MCP/Skills/vector injection not finished. *Status: closed — `ToolProviderPort` (Phase Y), `VectorStorePort` with LanceDB / sqlite-vec / Qdrant / pgvector adapters (Phase Z, v3.1.2), composition root with per-`org_id` isolation (v3.1.3) and `ConfigSourcePort` (v3.1.4).*
10. **Bus factor / community** minimal versus vendor frameworks. *Status: still open.*

---

## 4. Specification status (what remains to implement)

*Updated 2026-10-12: every spec below is implemented; the full list (27 spec folders, all implemented, no full SDD pending in-repo) is in [`specs/roadmap.md`](../specs/roadmap.md).*

| Spec | Phase | Actual status |
|---|---|---|
| `extension-surface` | X | ✅ IMPLEMENTED |
| `advanced-architectures` | A/B/C | ✅ IMPLEMENTED |
| `multimodal-agents` | F | ✅ IMPLEMENTED |
| `tool-provider-injection` | Y | ✅ IMPLEMENTED (by v3.1.0; was 🟡 In progress at writing) |
| `vector-store-port` | Z | ✅ IMPLEMENTED (v3.1.2; was 🔲 at writing) |
| `composition-root` | R | ✅ IMPLEMENTED (v3.1.3; host read surface delta v3.17.0; was 🔲 at writing) |
| `dependency-security-remediation` | — | ✅ IMPLEMENTED (18/18 alerts in terminal state: closed/remediated/mitigated) |

---

## 5. Prioritized feature roadmap

Priority by **production unblocking** and **risk**. Features marked "spec ✔" already have SDD artifacts; new ones are generated in `specs/` for upcoming development.

*Status 2026-10-12: all eight P0–P2 features below have shipped (versions in the "Shipped" notes); both P3 items have a framework-side implementation, and only the UI/host halves remain.*

### P0 — Close the last mile to production (specs already exist)
| # | Feature | Spec | Why P0 |
|---|---|---|---|
| 1 | **Tool Provider Injection** (Phase Y) | `specs/tool-provider-injection/` ✔ | Close Y6–Y8; decouples MCP/Skills from the core — *Shipped (by v3.1.0)* |
| 2 | **Vector Store Port** (Phase Z) | `specs/vector-store-port/` ✔ | Removes Chroma lock-in; reduces surface (chromadb CVE) — *Shipped v3.1.2* |
| 3 | **Runtime Composition Root** (Phase R) | `specs/composition-root/` ✔ | Unblocks `prismal-server`/`dashboard` (the missing layer) — *Shipped v3.1.3* |
| 4 | **Dependency Security Remediation** | `specs/dependency-security-remediation/` ✅ | Already executed: 18/18 alerts in terminal state + trivy incident closed |

### P1 — Interop and governance (production blockers, new specs)
| # | Feature | Spec | Why P1 |
|---|---|---|---|
| 5 | **A2A / Agent Cards interop** (agent-to-agent) | `specs/a2a-interop/` 🆕 (full) | 2026 interop standard (150+ orgs); avoids isolation; complements MCP — *Shipped v3.5.0; v1.0 dual-stack v3.17.0* |
| 6 | **Agent Identity & Access Governance** | `specs/agent-identity-governance/` 🆕 (PRD) | #1 enterprise gap; DID + per-agent credentials; trust foundation for A2A — *Shipped v3.4.0 (+ ID8 v3.10.0)* |

### P2 — Reliability and cost (new specs)
| # | Feature | Spec | Why P2 |
|---|---|---|---|
| 7 | **Agent Evaluation Harness** | `specs/agent-eval-harness/` 🆕 (PRD) | Closes the "scaffold gap"; system reliability regression — *Shipped v3.3.0* |
| 8 | **Cost & Budget Governance** | `specs/cost-budget-governance/` 🆕 (PRD) | Cost/call cap per run; circuit-breakers (expensive patterns) — *Shipped v3.1.5; extended by TKE/GW/DEC/AMR (v3.13.0–v3.16.0)* |

### P3 — Polish (no spec yet)
- **First-party observability UI** (or deep LangSmith/Langfuse integration). — *Deep integration shipped as `ObservabilityPort` (v3.9.0); the UI is still unbuilt (host side).*
- **Per-node type-safety** (Pydantic validation of node I/O; evolution of `AgentState`). — *Shipped opt-in as node I/O type-safety (v3.8.0); `AgentState` is still a `TypedDict`.*

---

## 5.1 Framework or host? (where each feature lives)

Rule: **contract/logic → framework (`prismal/`); serving HTTP, authenticating, displaying, persisting config → host (`prismal-server` / `prismal-dashboard`).** A2A and Identity are split.

| # | Feature | Framework (`prismal/`) | Host (`prismal-server` / `dashboard`) |
|---|---|---|---|
| 1 | Tool Provider (Phase Y) | ports/providers (`agents/extension`) | composes and injects at startup |
| 2 | Vector Store Port (Phase Z) | `rag/stores/` + `VectorStorePort` | chooses backend by config |
| 3 | Composition Root (Phase R) | `composition/runtime.py` / `build_runtime()` | calls it in the lifespan |
| 4 | Cost & Budget Governance | guard in `react_loop` + patterns | per-tenant quotas |
| 5 | A2A / Agent Cards (Phase I) | types · card · client · `A2AToolProvider` · handler | **HTTP endpoint (`/a2a`, `/.well-known/agent-card.json`) + auth** |
| 6 | Agent Identity & Governance | `prismal/identity/`: DIDs, `PolicyEngine`, vault port + `EnvVault`/`FileVault`, OBO delegation | **IdP/OAuth + production vault** |
| 7 | Agent Eval Harness | eval engine (module) | dev/CI tool (or separate package) |
| 8 | Polish | per-node type-safety (`AgentState`) | observability UI |

The framework defines ports and logic; the host composes and exposes them. That is why A2A and Identity have one half in the core (contract) and another in the host (serve/authenticate).

---

## 6. Recommended sequence

```
P0 (Y -> Z -> R -> security)   →  viable production layer (prismal-server/dashboard)
        │
        ▼
P1 (A2A interop  +  Agent Identity)   →  interoperable and governed (ecosystem + enterprise)
        │
        ▼
P2 (Eval harness  +  Cost governance) →  reliable and with bounded cost
        │
        ▼
P3 (Observability UI, type-safety)   →  competitive polish
```

Reasoning: without **P0** there is no deployable product; **A2A + Identity (P1)** is what closes the most visible gap versus MS Agent Framework / Google ADK and enables enterprise trust; **Eval + Cost (P2)** turns "it works" into "it is reliable and predictable"; **P3** is differentiation.

---

## 7. Artifacts generated with this analysis

- `docs/competitive-analysis.md` (this document).
- `specs/a2a-interop/` — full SDD set (PLAN, ARCHITECTURE, SPEC, TASKS).
- `specs/agent-identity-governance/PLAN.md` — seed PRD.
- `specs/agent-eval-harness/PLAN.md` — seed PRD.
- `specs/cost-budget-governance/PLAN.md` — seed PRD.

The three seed PRDs can be expanded into full SDD sets (ARCHITECTURE/SPEC/TASKS) in the next iteration. *(Done: all three were expanded into full SDD sets and implemented — v3.4.0, v3.3.0 and v3.1.5 respectively.)*

---

## Sources

- AI Agent Frameworks Compared 2026 — PE Collective, Alice Labs, Turing.
- Microsoft Agent Framework (Microsoft Learn, GitHub microsoft/agent-framework); Morph "8 SDKs, ACP".
- State of AI Agents 2026 (Lovelytics); Runtime Governance for AI Agents (arXiv); The AI Agent Identity Crisis (Strata).
- A2A Protocol: a2a-protocol.org, github.com/a2aproject/A2A, IBM Think, Atlan.
