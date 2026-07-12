# Prismal — Skynet S+ (Heterogeneous / Metered / Remote Swarms)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` (full SDD; not implemented) |
| **Version** | 1.0 |
| **Date** | 2026-07-11 |
| **Phase** | S+ |
| **Target package version** | `3.12.0` (additive, opt-in ⇒ SemVer minor) |
| **Parent spec** | `specs/skynet-swarm/` (Phase S, `IMPLEMENTED` v3.1.0) |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Executive Summary

Phase **S** shipped a bounded, audited swarm supervisor: a meta-supervisor
decomposes one order into N independent sub-orders, fans out a dynamically-sized
swarm of **homogeneous** workers (LangGraph `Send`), reduces their outputs, and
re-plans unmet orders until done. Phase S explicitly deferred three follow-ups
(`specs/skynet-swarm/PLAN.md §6.4`):

1. **Heterogeneous specialist swarms keyed by capability.**
2. **Cost/budget circuit-breaker integration.**
3. **Remote workers via the A2A interop layer.**

Since then, two of the three dependencies **shipped**: Phase C
(`cost-budget-governance`) and Phase I (`a2a-interop`). **Skynet S+** delivers
all three follow-ups on top of them, each **additive and opt-in** so that with
every S+ flag off the Phase-S swarm behaves **byte-for-byte identically** (the
26 base agents and the main supervisor are unaffected).

| S+ sub-feature | What it adds | Reuses (shipped) | New flag |
|---|---|---|---|
| **S+1 Specialist swarms** | Each `SwarmOrder` carries a meaningful `role`; a **role registry** binds role → (model, capabilities, persona prompt); the planner assigns the best specialist per sub-task; the worker resolves a **per-role model + prompt** (tools were already role-scoped in Phase S) | `SwarmOrder.role` (already present), `ProviderRegistry`, `ToolProviderPort`, `DEFAULT_CAPABILITY_MAP` | `skynet_specialists_enabled` |
| **S+2 Metered workers** | Each worker records its real token/cost usage into the **shared per-run `CostMeter`**; the swarm `Budget` (`skynet_token_budget` + cost/calls) is enforced **across workers** (soft = stop dispatching / degrade, hard = `SkynetBudgetExceeded`) — closing the Phase-S gap where only the planner/evaluator were metered | Phase C `CostMeter` / `BudgetGuard` / `make_budget_guard_fn` / per-run registry | `budget_enabled` (existing) |
| **S+3 Remote workers** | A role may bind to a **remote A2A agent**; the worker delegates that order over A2A (`A2AClient` through `A2AConnectionManager` allowlist + pool); remote output is L1-sanitized + audited; a remote failure degrades to `WorkerResult(success=False)` and never aborts the swarm | Phase I `A2AClient` / `A2AConnectionManager` / `A2AToolProvider` | `skynet_remote_workers_enabled` + `a2a_enabled` |

## 2. Feasibility — is this possible without new capability?

**Yes** — S+ is a composition of already-shipped primitives, exactly as Phase S
was for LangGraph fan-out:

- **S+1** needs no new node/edge: `SwarmOrder.role` already exists (pre-provisioned
  in Phase S as "*Phase S+: capability key*"), and the worker already resolves
  tools with `capabilities=[order.role]`. S+1 only adds a **role registry** (data)
  and makes the planner/worker *use* the role for model + persona selection.
- **S+2** needs no new node/edge: Phase C already provides a per-run `CostMeter`
  seeded by `session_id`, `BudgetGuard`, and `make_budget_guard_fn` (the exact
  seam `react_loop` and the expensive patterns already use). S+2 records the
  worker's response into that meter and consults the guard — the same pattern,
  applied at the worker seam.
- **S+3** needs no new node/edge: Phase I ships `A2AClient.send_task()` and the
  `A2AConnectionManager` allowlist/pool. S+3 makes the worker's backend delegate
  one order over A2A when its role is remote-bound — the swarm topology is
  unchanged (a remote worker is still one `Send` invocation).

So **Skynet S+ = role registry + worker metering + remote worker backend**, all
behind the existing `Send` fan-out. No new LangGraph capability; no change to the
Phase-S subgraph topology.

## 3. Context and Problem

### 3.1 Current situation (Phase S, shipped)

- Workers are **homogeneous** (`DD-SKY-003`): every worker runs the same
  `skynet_worker_model` with the same generic persona; `order.role` scopes only
  the worker's *tool* capabilities, not its model or behavior.
- The swarm `Budget(max_tokens=skynet_token_budget)` is built over a shared
  `CostMeter`, but only the **supervisor's planner/evaluator** calls are recorded
  into it — the **workers' own LLM usage is not metered**, so `skynet_token_budget`
  under-counts the swarm's real cost (the dominant cost is the N worker calls).
- Workers run **in-process only** (`§6.3`): there is no way to delegate a
  sub-order to a specialized remote agent, even though Phase I now makes remote
  agents first-class.

### 3.2 Why now

The two blockers named in Phase S's future-considerations are **shipped**:
`cost-budget-governance` (Phase C, v3.1.5 — `prismal/budget/`) and `a2a-interop`
(Phase I, v3.5.0 — `prismal/a2a/`). S+ is the natural convergence: a swarm whose
workers are **specialized, cost-bounded, and optionally remote**.

### 3.3 Problem statement

Make the swarm (a) **heterogeneous** so the right specialist handles each
sub-task, (b) **truthfully metered** so `skynet_token_budget` bounds the *whole*
swarm's cost (not just the supervisor's), and (c) **capable of remote delegation**
so a sub-order can run on a specialized A2A agent — **without** breaking the
Phase-S contract or the default (all-flags-off) behavior.

## 4. Goals / Non-Goals

### 4.1 Goals

- **G1** — A `role` on a `SwarmOrder` selects a specialist **model + persona +
  tool scope** via a role registry; the planner assigns roles per sub-order.
- **G2** — Every worker's real token/cost usage is recorded into the shared
  per-run `CostMeter`; the swarm `Budget` is enforced across workers (soft →
  stop dispatching further orders / degrade; hard → `SkynetBudgetExceeded`).
- **G3** — A role may bind to a **remote A2A agent**; the worker delegates that
  order over A2A behind the allowlist; remote content is sanitized + audited; a
  remote failure never aborts the swarm.
- **G4** — All three are **additive and opt-in**; with the S+ flags off the
  compiled graph and the Phase-S swarm are **byte-for-byte unchanged**
  (snapshot-tested).
- **G5** — **Callable injection preserved** end-to-end (`plan_fn`, `worker_fn`,
  `evaluate_fn`, a new `role_resolver`, a remote `send_fn`), so every unit test
  runs with **no LLM backend and no network**.

### 4.2 Non-Goals

- Long-lived / stateful workers (each worker stays per-order, stateless).
- Inter-worker negotiation beyond the existing optional `swarm_handoff`.
- A scheduler / autoscaler for remote workers (allowlist + pool + cap only).
- Replacing `network_supervisor` / `domain_supervisor` (S+ is orthogonal;
  §9 clarifies the relationship).
- Learned/RL role assignment (the planner assigns roles heuristically/LLM;
  no training loop).

## 5. Users & Use Cases

- **U1 (specialist split)** — "Research these 5 competitors, then write a
  comparison" → planner assigns `researcher` roles to the 5 fan-out orders and a
  `writer` role to the synthesis order; each runs its own tuned model + persona.
- **U2 (cost-bounded swarm)** — an operator sets `skynet_token_budget=200000`;
  the swarm dispatches workers until the metered usage (planner + all workers)
  approaches the cap, then degrades (stops dispatching new orders, reduces on what
  completed) instead of silently overspending.
- **U3 (remote specialist)** — a role `legal_review` is bound to a remote A2A
  agent operated by another team; the planner routes legal sub-orders to it; the
  swarm treats the remote result like any other `WorkerResult`.

## 6. Scope

### 6.1 In scope

- A `SpecialistRole` value object + a **role registry** (`skynet_roles.yaml`,
  operator-authored, loaded like `config/tool_policies.yaml`).
- Role-aware `SkynetSupervisor.plan()` (assigns `order.role`) and role-aware
  `SwarmWorker.execute()` (per-role model + persona + tools).
- Worker token/cost metering into the shared `CostMeter`; budget enforcement at
  the worker seam via `make_budget_guard_fn`; `WorkerResult.usage`.
- A remote worker backend delegating one order over A2A (behind
  `A2AConnectionManager`), sanitize + audit, graceful degradation.
- Settings, exceptions (extend `SkynetError`), audit, OTel spans + counters,
  docs, example, and unit tests with injected fakes (no LLM / no network).

### 6.2 Out of scope

- Distributed *supervisor* (the meta-supervisor stays in-process; only *workers*
  may be remote).
- Persisting the role registry in a database (it is file/settings-driven, like
  the Phase H tool policies).
- Autoscaling / cross-tenant worker sharing.

### 6.3 Future considerations (Phase S++)

- Learned role assignment / bandit over specialist performance.
- Remote-worker health checks + circuit breaking beyond A2A's per-call timeout.
- Streaming partial worker results into the reducer.

## 7. Functional Requirements (summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-SP-01 | A `SpecialistRole` binds a role name → model / capabilities / persona / optional remote agent | MUST |
| RF-SP-02 | A role registry loads from `skynet_roles.yaml`; unknown/absent role falls back to the generic `worker` (never raises at dispatch) | MUST |
| RF-SP-03 | `SkynetSupervisor.plan()` assigns a `role` to each `SwarmOrder` when `skynet_specialists_enabled` (else all `"worker"`, unchanged) | MUST |
| RF-SP-04 | `SwarmWorker.execute()` resolves the role's **model** + **persona prompt** + **tool capabilities**; homogeneous behavior preserved for role `"worker"` | MUST |
| RF-SP-05 | Each worker records real token/cost `Usage` into the shared per-run `CostMeter` (`WorkerResult.usage`) | MUST |
| RF-SP-06 | The swarm `Budget` is enforced across workers: soft → stop dispatching further orders + degrade; hard → `SkynetBudgetExceeded` | MUST |
| RF-SP-07 | A remote-bound role delegates its order over A2A (`A2AClient` via `A2AConnectionManager` allowlist); output L1-sanitized + audited | MUST |
| RF-SP-08 | A remote worker failure/timeout/deny yields `WorkerResult(success=False, error=...)`, never aborts the swarm | MUST |
| RF-SP-09 | With every S+ flag off, the compiled graph + Phase-S swarm are byte-for-byte unchanged (snapshot) | MUST |
| RF-SP-10 | Every component keeps callable injection (`role_resolver`, `send_fn`, `budget_guard_fn`); unit tests need no LLM / no network | MUST |
| RF-SP-11 | New OTel spans/counters: role assignment, worker tokens, remote calls, budget cutoffs | SHOULD |
| RF-SP-12 | `docs/skynet.md` + `examples/` updated; a runnable specialist + metered + (faked) remote demo | SHOULD |

## 8. Success Metrics

- `skynet_token_budget` reflects **whole-swarm** cost (planner + workers) within
  a small tolerance in a metered end-to-end test.
- A specialist run assigns ≥2 distinct roles and resolves ≥2 distinct models
  (fake-provider test).
- A (faked) remote worker round-trips one order and its failure is contained.
- Snapshot proves zero graph drift with S+ flags off.
- New-package coverage ≥ the repo's 80% floor.

## 9. Relationship to existing supervisors

- **`network_supervisor`** already routes a *whole task* to a remote node by
  capability; S+3 is finer-grained (routes a *single sub-order* of a swarm to a
  remote agent) and is A2A-native, not the legacy HTTP node map. They coexist.
- **`domain_supervisor`** routes by domain; S+ role assignment is intra-swarm and
  orthogonal. S+ does not replace either.

## 10. Milestones

| Milestone | Content | Exit criterion |
|---|---|---|
| **SP1** | Types + role registry + settings + exceptions | value objects round-trip; registry loads + falls back; flags parse + validate |
| **SP2** | Specialist planner + worker (model/persona/tools by role) | 2 roles → 2 models (fake provider); role `"worker"` unchanged |
| **SP3** | Worker metering + budget enforcement | worker usage recorded into shared meter; soft/hard cutoff proven |
| **SP4** | Remote worker backend (A2A) | faked remote order round-trips; failure contained; allowlist honored |
| **SP5** | Subgraph/supervisor wiring (gated) + snapshot | graph byte-for-byte unchanged when off; specialist+metered path e2e with fakes |
| **SP6** | Tests, docs, example, packaging, spec status flips | coverage ≥80%; ruff/mypy/bandit clean; docs + example |

Estimate roll-up: ~**9–11 person-days** across SP1–SP6. Critical path
SP1→SP2→SP3→SP5; SP4 can parallelize after SP1.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Metering the worker double-counts vs. the planner | Single shared per-run `CostMeter` keyed by `session_id` (Phase C registry); record each call exactly once at its own seam |
| Remote worker exfiltration / prompt injection | All remote content crosses the A2A trust boundary → L1-sanitized before touching state; allowlist + strict-mode deny; audited hash-first |
| Behavior drift when flags off | Every S+ path gated; role defaults to `"worker"`; snapshot test (RF-SP-09) |
| Specialist model sprawl / cost | Role registry is operator-authored + capped; per-role model optional (falls back to `skynet_worker_model`) |
| Budget cutoff mid-fan-out leaves partial work | Reduce over completed workers + carry unmet orders as `deferred` (reuse Phase-S deferral); audited |
