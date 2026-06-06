# Prismal — Skynet Swarm Supervisor

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **Reviewers** | Tech Lead, AI Architect |
| **Related documents** | `ARCHITECTURE.md`, `SPEC.md`, `TASKS.md` |

---

## 1. Executive Summary

**Skynet** is a new opt-in **swarm supervisor**: a meta-supervisor agent that
decomposes an incoming order into independent sub-orders and dispatches them to a
dynamically-sized **swarm of worker agents**, then reduces their outputs into a
single result and reports back. The **swarm size is decided by the supervisor at
runtime** — it is a function of how the supervisor splits the work — bounded by an
operator-configured ceiling.

Skynet is implemented as a LangGraph subgraph and **reuses existing Prismal
primitives**: `make_parallel_dispatcher()` (dynamic fan-out via LangGraph
`Send()`), the `swarm` pattern (`swarm_handoff` / `HandoffRecord`), and the
`parallel_max_workers` ceiling. It is **opt-in**: gated by `settings.skynet_enabled`
(default `False`); when off, the 26 existing agents and the main supervisor behave
identically.

> Naming: *Skynet* (the Terminator franchise's distributed command AI) is an apt
> metaphor — a central controller coordinating many autonomous units — and pairs
> with the sibling `kokoro-deliberation` feature. The name is thematic only; the
> behavior is bounded, audited, and human-gated by design (see Security).

## 2. Feasibility — Is this possible with LangGraph?

**Yes.** LangGraph supports dynamic fan-out natively via `Send(node, state)`
returned from a conditional edge, which spawns one worker invocation per item at
runtime — the worker count is not fixed at graph-compile time. Prismal already
wraps this:

- `agents/patterns/parallel.py::make_parallel_dispatcher(tasks_field, worker_node,
  max_workers, …)` reads a task list from state and emits one `Send` per task,
  capped by `min(max_workers, settings.parallel_max_workers)`.
- `agents/patterns/swarm.py` provides decentralised `swarm_handoff()` with
  `HandoffRecord` audit, for peer-to-peer handoff variants.
- Result aggregation uses LangGraph's reducer model (the `messages` channel uses
  `add_messages`; a dedicated `skynet.results` channel uses a list-append reducer
  so concurrent workers merge safely).

So Skynet = **planner (supervisor) → dynamic `Send` fan-out to N workers → reducer
→ supervisor evaluate → loop or finish**. No new LangGraph capability is required;
Skynet composes existing ones.

## 3. Context and Problem

### 3.1 Current Situation

Prismal has a central `supervisor_node` routing to 26 specialists, plus
`network_supervisor` / `domain_supervisor` for supervisor-of-supervisors flows,
and `make_parallel_dispatcher` for fan-out. But there is no first-class agent that
**decomposes one order into many, runs a dynamically-sized worker swarm, and
reduces the results back** as a single reusable unit.

### 3.2 Problem

Some goals are naturally parallel ("research these 8 competitors", "refactor these
5 modules", "draft replies to these 12 tickets"). Doing this today means manually
wiring a dispatcher and a reducer per use case, with no standard control loop,
bounded budget, or audit for the swarm.

### 3.3 Opportunity

Provide a **Skynet swarm supervisor**: give it an order, it plans the sub-orders,
sizes and launches the swarm, reduces the results, and (optionally) iterates until
the goal is met — all bounded, observable, and audited.

## 4. Target Users

### Persona 1: Applied AI Engineer

Wants a reusable "map-reduce over agents" component: hand Skynet a goal and a
worker capability, get back a synthesized result without hand-wiring `Send`.

### Persona 2: Ops / Platform Operator

Needs hard ceilings (max swarm size, max rounds, budget) and full audit of what
each worker did — controllable per deployment via settings.

### Persona 3: Workflow Author

Defines *how* an order is split (the planning prompt / strategy) and *how* results
combine (the reduce strategy), without touching graph internals.

## 5. Objectives and Success Metrics

### 5.1 Business Objectives

- A reusable, bounded swarm map-reduce component over agents.
- Zero behavior change when disabled.
- Reuse fan-out / swarm / audit primitives rather than re-implementing them.

### 5.2 User Objectives

| Objective | Success Metric |
|---|---|
| Decompose an order into sub-orders | Supervisor emits a typed `SwarmPlan` of N orders |
| Size the swarm from the supervisor | N = `len(plan.orders)`, capped by `skynet_max_swarm` |
| Run workers concurrently | One `Send` per order; concurrency ≤ `parallel_max_workers` |
| Reduce results deterministically | A single `SwarmResult` synthesized from worker outputs |
| Iterate to completion | Supervisor re-plans unmet orders up to `skynet_max_rounds` |
| Bounded + safe | Hard caps on workers, rounds, and (optional) token budget |

## 6. Scope

### 6.1 Swarm sizing — "defined from the supervisor"

The **supervisor decides the swarm size**, in one of two modes:

- **Dynamic (default):** the planner decomposes the order into a variable number of
  sub-orders; `N = len(plan.orders)`. The swarm is exactly as large as the work
  requires.
- **Fixed:** when `settings.skynet_swarm_size > 0`, the planner is instructed to
  split the order into exactly that many sub-orders (load-balanced), so `N` is a
  constant chosen by the operator and enforced by the supervisor.

In both modes `N` is hard-capped by `min(skynet_max_swarm, parallel_max_workers)`;
orders beyond the cap are deferred to the next round rather than dropped silently.

### 6.2 In Scope (Phase S)

- `SkynetSupervisor` (planner + evaluator): order → `SwarmPlan` (N sub-orders),
  then evaluate `SwarmResult` → done or re-plan.
- A homogeneous `SwarmWorker` agent that executes one order (tools resolved via the
  injected `ToolProviderPort`); optional role/specialist map.
- Dynamic `Send` fan-out (reusing `make_parallel_dispatcher`) and a results reducer.
- A control loop bounded by `skynet_max_rounds`.
- LangGraph subgraph `skynet/` with `build_skynet_subgraph()` + `register_skynet()`.
- Opt-in supervisor route + intent routing, gated by `skynet_enabled`.
- Settings, exceptions, audit, observability, and unit tests with injected fakes.

### 6.3 Out of Scope (Excluded)

- Distributed/multi-process execution (workers run as LangGraph `Send` invocations
  in-process; no remote workers in Phase S).
- Long-lived/persistent worker agents (each worker is per-order, stateless beyond
  its order context).
- Inter-worker negotiation beyond the optional `swarm_handoff` primitive.
- Replacing `network_supervisor` / `domain_supervisor`.

### 6.4 Future Considerations (Phase S+)

- Heterogeneous specialist swarms keyed by capability.
- Cost/budget circuit-breaker integration (the planned `cost-budget-governance`).
- Remote workers via the A2A interop layer (`specs/a2a-interop/`).

## 7. Functional Requirements (Summary — detail in `SPEC.md`)

| ID | Requirement | Priority |
|---|---|---|
| RF-SKY-01 | `SkynetSupervisor.plan()` decomposes an order into a `SwarmPlan` of N sub-orders | MUST |
| RF-SKY-02 | Swarm size N decided by the supervisor (dynamic) or fixed via `skynet_swarm_size` | MUST |
| RF-SKY-03 | N hard-capped by `min(skynet_max_swarm, parallel_max_workers)`; overflow deferred | MUST |
| RF-SKY-04 | Dynamic `Send` fan-out: one `SwarmWorker` invocation per order | MUST |
| RF-SKY-05 | `SwarmWorker.execute()` runs one order; tools via injected `ToolProviderPort` | MUST |
| RF-SKY-06 | Results reducer merges concurrent worker outputs safely | MUST |
| RF-SKY-07 | `SkynetSupervisor.evaluate()` → done or re-plan unmet orders | MUST |
| RF-SKY-08 | Control loop bounded by `skynet_max_rounds` | MUST |
| RF-SKY-09 | Expose as subgraph `build_skynet_subgraph()` + `register_skynet()` | MUST |
| RF-SKY-10 | Opt-in supervisor route + intent routing, gated by `skynet_enabled` | MUST |
| RF-SKY-11 | Callable injection (`plan_fn`, `worker_fn`, `reduce_fn`, `evaluate_fn`) for tests | MUST |
| RF-SKY-12 | All Skynet state under `state["metadata"]["skynet"]` | MUST |
| RF-SKY-13 | Audit each plan, dispatch, worker, and reduce (hash-first) | SHOULD |
| RF-SKY-14 | Per-order content isolated via `SecurePromptBuilder`; worker tool calls gated | MUST |

## 8. Non-Functional Requirements

### Security

- Sub-order text is derived from user input → isolated with `SecurePromptBuilder`;
  never f-stringed into prompts.
- Worker tool/file/code actions pass `ActionInterceptor.check()` + guardrails.
- `AuditLogger` records plan, fan-out size, each worker, and the reduce (hash-first).

### Performance / Cost (control)

- Concurrency bounded by `min(skynet_max_swarm, parallel_max_workers)`.
- Rounds bounded by `skynet_max_rounds`; optional token budget per run.
- Overflow orders are deferred, never silently dropped, and surfaced in audit.

### Observability

- OTel spans (`skynet.plan`, `skynet.dispatch`, `skynet.worker`, `skynet.reduce`,
  `skynet.evaluate`) + counters for swarm size, rounds, and deferred orders.

### Maintainability

- No provider SDK imports outside `prismal/providers/`.
- Callable injection so the whole loop tests without an LLM backend.
- State namespaced under `metadata["skynet"]`.

## 9. Constraints and Dependencies

- Python 3.13+, LangGraph `StateGraph[AgentState]`, async via
  `get_async_compiled_graph()`.
- Reuse `make_parallel_dispatcher`, `swarm`, `SecurePromptBuilder`,
  `ActionInterceptor`, `AuditLogger`, `SubgraphRegistry`, `intent_router`,
  and the injected `ToolProviderPort` (Skynet must not import `prismal.mcp` /
  `prismal.skills`).

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Runaway fan-out / cost explosion | High | Hard caps (`skynet_max_swarm`, `parallel_max_workers`, `skynet_max_rounds`); optional budget |
| Non-terminating re-plan loop | High | Round cap; evaluator must make monotonic progress or stop |
| Prompt injection via sub-order text | High | `SecurePromptBuilder`; worker actions gated by `ActionInterceptor` |
| Reducer loses/duplicates worker output | Medium | List-append reducer keyed by order id; idempotent merge |
| Behavior leak when off | Medium | Gate every wiring point on `skynet_enabled`; snapshot test |

## 11. Open Questions

- Default planning strategy: LLM-decomposition vs. a deterministic splitter when
  the order is already a list? (Phase S: LLM planner with a deterministic
  pass-through when `state` already carries an explicit order list.)
- Worker homogeneity: a single generic worker vs. capability-typed specialists?
  (Phase S: homogeneous worker + optional `role` per order; specialists are S+.)
- Partial-failure policy: fail the run vs. reduce over successful workers and
  re-plan the failures? (Phase S: reduce over successes, re-plan failures within
  the round cap.)
