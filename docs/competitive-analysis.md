# Prismal — Competitive Analysis and Production Roadmap

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Date** | 2026-06-06 |
| **Status** | `DRAFT` |
| **Basis** | Structure of the `prismal` repo (v3.0.0, ~52k LOC, 191 test files) + specs in `specs/` + Obsidian notes `Documentacion/Prismal` |
| **Scope** | Positioning versus 2026 agent frameworks, weaknesses, and a prioritized feature roadmap |

> Caveat: this analysis is based on the repo structure, the specs, and the notes — not on running the suite or auditing each capability end-to-end. The specs describe the *intended* design; actual maturity may be lower.

> **2026-07-04 update:** every P0–P2 item in §5 below (Tool Provider, Vector Store Port, Composition Root, Dependency Remediation, A2A, Agent Identity, Eval Harness, Cost & Budget Governance) has since shipped — verified against real code, tests, and `CHANGELOG.md`, not just spec metadata. This document is kept for historical framing only; for the current state and the *next* round of gaps (external, versus 2026 Loops/Harness/Guardrails state of the art rather than internal backlog) see [`gap-analysis-loops-harness-guardrails-2026-07.md`](./gap-analysis-loops-harness-guardrails-2026-07.md), whose priority list (§5) is now tracked as SDD artifacts: [`specs/guardrails-modernization/`](../specs/guardrails-modernization/), [`specs/loop-hardening/`](../specs/loop-hardening/), [`specs/node-io-typesafety/`](../specs/node-io-typesafety/), [`specs/observability-integration/`](../specs/observability-integration/), [`specs/reference-host-bootstrap/PLAN.md`](../specs/reference-host-bootstrap/PLAN.md) (PRD seed), [`specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md`](../specs/agent-identity-governance/ADDENDUM-ID8-permission-manager-did.md), and [`specs/dependency-security-remediation/ADDENDUM-refresh-2026-07.md`](../specs/dependency-security-remediation/ADDENDUM-refresh-2026-07.md).

---

## 1. What prismal is and what it resembles

Prismal is a **"LangGraph with batteries included"**: it wraps LangGraph (state-graph engine) and adds, in a single package, what is normally assembled from several libraries — supervisor + 26 agents, 7 RAG engines, 9 patterns, 12 subgraphs, 5-layer security, sandbox, MCP, skills, scheduler, and multimodal.

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

- **By integrated breadth in a single codebase: it is at the top.** Almost no OSS brings together supervisor + advanced RAG (7 engines) + 5-layer security + sandbox + MCP + skills + scheduler + multimodal out of the box.
- **By product/ecosystem maturity: no.** Completeness in 2026 is also measured by managed runtime, first-party observability (LangSmith), **standard interop (A2A/Agent Cards)**, **identity governance**, system evaluation, and community. Prismal is **beta (v3.0.0), essentially a single maintainer**, with the entire upper layer (`prismal-server`, `prismal-dashboard`, `prismal-sdk`) in a **"Planned"** state.

**Verdict: the most complete as a *library of capabilities*, not as a *production platform*.**

---

## 3. Weaknesses (cross-referenced with the 2026 industry gaps)

1. **Experimentation→production gap.** The #1 failure of the industry and of prismal: the deployment/serving layer is missing (server/dashboard/SDK "Planned").
2. **No A2A / Agent Cards interop.** It has MCP (*tool* interop) but no agent-to-agent protocol; MS Agent Framework and Google ADK already ship it natively (150+ orgs support it). Risk of isolation from the multi-agent ecosystem.
3. **Agent identity/governance.** The most cited gap in 2026. It has `PermissionManager` + `AuditLogger`, but no per-agent identity (DID), OAuth-on-behalf credentials, or IAM for autonomous agents.
4. **No system-level evaluation harness** (the "scaffold gap": evaluating the model in isolation does not predict the agentic system).
5. **Cost/latency without governance.** The debate/ToT/LATS/MoA patterns are expensive; no budget/cap per run nor cost circuit-breakers.
6. **Partial type-safety.** `AgentState` is a `TypedDict` without per-node I/O validation.
7. **Observability without its own UI** (OTel/Langfuse; depends on third parties).
8. **Dependency/security chain debt** (18 Dependabot alerts; LangChain stack churn).
9. **Pending couplings:** vector store tied to Chroma; multi-tenant not real; MCP/Skills/vector injection not finished.
10. **Bus factor / community** minimal versus vendor frameworks.

---

## 4. Specification status (what remains to implement)

| Spec | Phase | Actual status |
|---|---|---|
| `extension-surface` | X | ✅ IMPLEMENTED |
| `advanced-architectures` | A/B/C | ✅ IMPLEMENTED |
| `multimodal-agents` | F | ✅ IMPLEMENTED |
| `tool-provider-injection` | Y | 🟡 In progress (Y1–Y5 with code; close Y6–Y8 + mark IMPLEMENTED) |
| `vector-store-port` | Z | 🔲 Specified, not implemented |
| `composition-root` | R | 🔲 Specified, not implemented |
| `dependency-security-remediation` | — | ✅ IMPLEMENTED (18/18 alerts in terminal state: closed/remediated/mitigated) |

---

## 5. Prioritized feature roadmap

Priority by **production unblocking** and **risk**. Features marked "spec ✔" already have SDD artifacts; new ones are generated in `specs/` for upcoming development.

### P0 — Close the last mile to production (specs already exist)
| # | Feature | Spec | Why P0 |
|---|---|---|---|
| 1 | **Tool Provider Injection** (Phase Y) | `specs/tool-provider-injection/` ✔ | Close Y6–Y8; decouples MCP/Skills from the core |
| 2 | **Vector Store Port** (Phase Z) | `specs/vector-store-port/` ✔ | Removes Chroma lock-in; reduces surface (chromadb CVE) |
| 3 | **Runtime Composition Root** (Phase R) | `specs/composition-root/` ✔ | Unblocks `prismal-server`/`dashboard` (the missing layer) |
| 4 | **Dependency Security Remediation** | `specs/dependency-security-remediation/` ✅ | Already executed: 18/18 alerts in terminal state + trivy incident closed |

### P1 — Interop and governance (production blockers, new specs)
| # | Feature | Spec | Why P1 |
|---|---|---|---|
| 5 | **A2A / Agent Cards interop** (agent-to-agent) | `specs/a2a-interop/` 🆕 (full) | 2026 interop standard (150+ orgs); avoids isolation; complements MCP |
| 6 | **Agent Identity & Access Governance** | `specs/agent-identity-governance/` 🆕 (PRD) | #1 enterprise gap; DID + per-agent credentials; trust foundation for A2A |

### P2 — Reliability and cost (new specs)
| # | Feature | Spec | Why P2 |
|---|---|---|---|
| 7 | **Agent Evaluation Harness** | `specs/agent-eval-harness/` 🆕 (PRD) | Closes the "scaffold gap"; system reliability regression |
| 8 | **Cost & Budget Governance** | `specs/cost-budget-governance/` 🆕 (PRD) | Cost/call cap per run; circuit-breakers (expensive patterns) |

### P3 — Polish (no spec yet)
- **First-party observability UI** (or deep LangSmith/Langfuse integration).
- **Per-node type-safety** (Pydantic validation of node I/O; evolution of `AgentState`).

---

## 5.1 Framework or host? (where each feature lives)

Rule: **contract/logic → framework (`prismal/`); serving HTTP, authenticating, displaying, persisting config → host (`prismal-server` / `prismal-dashboard`).** A2A and Identity are split.

| # | Feature | Framework (`prismal/`) | Host (`prismal-server` / `dashboard`) |
|---|---|---|---|
| 1 | Tool Provider (Phase Y) | ports/providers (`agents/extension`) | composes and injects at startup |
| 2 | Vector Store Port (Phase Z) | `rag/stores/` + `VectorStorePort` | chooses backend by config |
| 3 | Composition Root (Phase R) | `composition.py` / `build_runtime()` | calls it in the lifespan |
| 4 | Cost & Budget Governance | guard in `react_loop` + patterns | per-tenant quotas |
| 5 | A2A / Agent Cards (Phase I) | types · card · client · `A2AToolProvider` · handler | **HTTP endpoint (`/a2a`, `/.well-known/agent-card.json`) + auth** |
| 6 | Agent Identity & Governance | `PolicyEngine` + identity port (`security/`) | **IdP/OAuth + credential vault + DID** |
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

The three seed PRDs can be expanded into full SDD sets (ARCHITECTURE/SPEC/TASKS) in the next iteration.

---

## Sources

- AI Agent Frameworks Compared 2026 — PE Collective, Alice Labs, Turing.
- Microsoft Agent Framework (Microsoft Learn, GitHub microsoft/agent-framework); Morph "8 SDKs, ACP".
- State of AI Agents 2026 (Lovelytics); Runtime Governance for AI Agents (arXiv); The AI Agent Identity Crisis (Strata).
- A2A Protocol: a2a-protocol.org, github.com/a2aproject/A2A, IBM Think, Atlan.
