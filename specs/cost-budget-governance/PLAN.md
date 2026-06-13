# Prismal — Cost & Budget Governance

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (full SDD: SPEC + ARCHITECTURE + TASKS; shipped Phase C) |
| **Version** | 0.1 |
| **Date** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect, FinOps |
| **Priority** | P2 (cost predictability) |
| **Related** | `agents/tool_registry.py` (`react_loop`), `agents/patterns/`, `monitoring/`, `providers/` |

---

## 1. Executive Summary

Prismal's advanced patterns are **expensive by design**: debate (N agents × M rounds), Tree-of-Thoughts, LATS (MCTS), Mixture-of-Agents, and parallel dispatch multiply the LLM calls (a 4×5 debate = 20+ calls minimum). Today there is no **per-execution budget** nor **cost/call/token circuit-breakers**: a misconfigured flow or a loop can blow up spending without a cap. This feature adds **cost governance**: a budget per run/session/tenant, real-time cost/token measurement, and cutoffs (soft/hard) when limits are exceeded — turning "it works" into "it works within a predictable budget".

---

## 2. Context and Problem

- **No per-execution cap:** `react_loop` limits iterations (`_MAX_REACT_ITERATIONS`) and the supervisor routes, but there is no aggregate budget of **tokens/cost/calls** per turn or per session.
- **Multiplier patterns:** debate/ToT/LATS/MoA/parallel can explode the number of calls; the cost is neither bounded nor reported up front.
- **No cost attribution:** cost is not measured per agent/pattern/tenant; FinOps has no visibility.
- **No circuit-breakers:** in the face of a loop or an expensive remote (A2A) agent, there is no automatic cutoff.
- **Multi-tenant:** without a quota per `org_id`, one tenant can consume others' budget.

---

## 3. Target Users

- **FinOps / Operator:** set budgets per tenant/session; see attributed cost; alerts.
- **Flow Author:** declare a `budget` per expensive pattern (debate/ToT) and degrade gracefully when exceeding it.
- **Platform Host (`prismal-server`):** quota enforcement per `org_id` (via Phase R); reject/queue on exceeding.
- **SRE:** circuit-breakers against loops/runaway cost.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Per-run budget | Cap of tokens/cost/calls per turn/session | Configurable |
| Real-time measurement | Accumulated cost/tokens per run (via `monitoring/`) | Available |
| Circuit-breakers | Soft cutoff (warns/degrades) and hard (aborts) on exceeding | Implemented |
| Attribution | Cost per agent/pattern/tenant | Reported |
| Multi-tenant quota | Limit per `org_id` (Phase R) | Supported |
| Backward-compat | Without configured limits, current behavior | 100% |

---

## 5. Scope (proposed)

### In Scope
- **`Budget`** (tokens, estimated USD cost, number of LLM calls, wall-clock) per **scope** (turn / session / tenant).
- **`CostMeter`** that accumulates usage from the `providers/` callbacks (LiteLLM usage) and `react_loop`; cost estimation per model (configurable pricing table).
- **`BudgetGuard`** integrated into `react_loop` and the expensive patterns: pre-call check; **soft cap** (degrade: fewer rounds/branches, cheaper model, terminate with best-effort) and **hard cap** (`BudgetExceeded` → abort with an audited partial response).
- **Attribution** per agent/pattern/tenant in spans/metrics (`monitoring/`).
- **Per-tenant quota** via Phase R (budget resolved per `org_id`).
- Settings `budget_*`; configurable degradation per pattern.

### Out of Scope
- Real billing/chargeback (metrics are exported; billing is the host's).
- Real-time provider pricing (configurable table; manual/periodic update).
- Automatic prompt optimization to reduce cost (future).

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-CST-001 | `Budget` per scope (turn/session/tenant) with token/cost/call limits | `MUST` |
| RF-CST-002 | `CostMeter` accumulates real usage from `providers/` + pricing-table estimation | `MUST` |
| RF-CST-003 | `BudgetGuard` with soft cap (degrade) and hard cap (`BudgetExceeded`) in `react_loop` and patterns | `MUST` |
| RF-CST-004 | Cost attribution per agent/pattern/tenant (metrics/spans) | `SHOULD` |
| RF-CST-005 | Quota per `org_id` via Phase R | `SHOULD` |
| RF-CST-006 | Settings `budget_*`; configurable degradation per pattern | `MUST` |
| RF-CST-007 | Auditing of cutoffs (what was aborted/degraded and why) | `SHOULD` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Imprecise cost estimation | Use real LiteLLM usage; versioned pricing table; mark estimates |
| Hard cap cuts useful responses | Soft cap first (degrade); hard cap with partial response + clear notice |
| Measurement overhead | O(1) accumulation per call; no I/O on the hot path |
| Patterns that ignore the guard | `BudgetGuard` injected into the pattern factory; enforcement test |

---

## 8. Dependencies

- `agents/tool_registry.py::react_loop` (main checkpoint).
- `agents/patterns/` (debate, ToT, LATS, MoA, parallel) — integrate the guard.
- `providers/` (real LiteLLM usage), `monitoring/` (metrics/spans), `core/config.py`.
- `specs/composition-root/` (per-tenant quota), `specs/a2a-interop/` (cost of remote delegations).

---

## 9. Next Steps

Expand to the full SDD set: design of `CostMeter`/`BudgetGuard`, exact integration points in `react_loop` and the pattern factory, pricing table, per-pattern degradation strategies, and multi-tenant enforcement.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1 | 2026-06-06 | Ernesto Crespo | Seed PRD — cost and budget governance |
