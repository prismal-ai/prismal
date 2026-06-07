# Prismal Skynet Swarm Supervisor — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/skynet-swarm/PLAN.md` |
| **SPEC** | `specs/skynet-swarm/SPEC.md` |
| **TASKS** | `specs/skynet-swarm/TASKS.md` |

---

## 1. Context

Skynet adds a **swarm supervisor**: an agent that turns one order into many,
dispatches a dynamically-sized worker swarm, and reduces the results. It composes
primitives Prismal already ships — `make_parallel_dispatcher` (dynamic `Send`
fan-out), the `swarm` pattern, and the `parallel_max_workers` ceiling — so no new
LangGraph capability is required (see `PLAN.md §2`).

## 2. Feasibility with LangGraph (confirmed)

LangGraph fan-out is **runtime-dynamic**: a conditional edge may return a
`list[Send]`, spawning one worker invocation per element with a per-invocation
state payload. The worker count is therefore decided at run time by the supervisor,
not fixed at compile time. Prismal's `make_parallel_dispatcher` already implements
this with an operator cap, and concurrent worker writes merge through a list-append
reducer channel (the same mechanism `messages` uses with `add_messages`). Skynet is
a composition, not an extension, of LangGraph.

## 3. How many agents are in the swarm? (Swarm sizing)

The **supervisor sizes the swarm**:

```
N_requested = len(SwarmPlan.orders)              # the supervisor's decomposition
N_effective = min(N_requested,
                  settings.skynet_max_swarm,
                  settings.parallel_max_workers)  # operator ceilings
overflow    = SwarmPlan.orders[N_effective:]      # deferred to the next round
```

- **Dynamic mode** (`skynet_swarm_size == 0`, default): the planner chooses N from
  the structure of the goal. "Research 8 competitors" → N≈8 (capped).
- **Fixed mode** (`skynet_swarm_size == K`): the planner is instructed to emit
  exactly K load-balanced orders. N = K (capped).

The operator ceiling (`skynet_max_swarm`, clamped to `parallel_max_workers`) always
wins; overflow orders are **deferred, not dropped**, and run in the next round. This
gives the supervisor authority over swarm size while guaranteeing bounded fan-out.

## 4. Proposed Architecture

### 4.1 New Modules

```
prismal/
├── agents/
│   ├── skynet/                       ← NEW agent package
│   │   ├── types.py                  ← SwarmOrder, SwarmPlan, WorkerResult, SwarmResult
│   │   ├── supervisor.py             ← SkynetSupervisor (plan + evaluate)
│   │   ├── worker.py                 ← SwarmWorker (execute one order)
│   │   └── reduce.py                 ← reduce_results (synthesis|concat|first_success)
│   └── subgraphs/
│       └── skynet/                   ← NEW subgraph
│           ├── builder.py            ← build_skynet_subgraph / register_skynet
│           ├── plan_node.py
│           ├── worker_node.py
│           ├── reduce_node.py
│           ├── evaluate_node.py
│           └── output_node.py
```

Reused (unchanged): `agents/patterns/parallel.py` (`make_parallel_dispatcher`),
`agents/patterns/swarm.py` (`swarm_handoff`/`HandoffRecord`), `security/*`
(`SecurePromptBuilder`, `ActionInterceptor`, `AuditLogger`),
`agents/subgraphs/registry.py`, `agents/intent_router.py`, and the injected
`ToolProviderPort` (Phase Y).

### 4.2 Subgraph Topology

```
                 ┌────────────┐
          START →│  plan      │  SkynetSupervisor.plan(goal) → SwarmPlan(N orders)
                 └─────┬──────┘  writes orders to metadata.skynet.orders
                       │
              (conditional edge: make_parallel_dispatcher → list[Send])
                       │  one Send per order, ≤ min(skynet_max_swarm, parallel_max_workers)
            ┌──────────┼───────────┬───────────── … (N workers, concurrent)
            ▼          ▼           ▼
        ┌────────┐ ┌────────┐  ┌────────┐
        │ worker │ │ worker │  │ worker │   SwarmWorker.execute(order)
        └────┬───┘ └────┬───┘  └────┬───┘   append WorkerResult → skynet.results (list-append reducer)
            └──────────┴───────────┘
                       ▼
                 ┌────────────┐
                 │  reduce    │  reduce_results(goal, results) → answer
                 └─────┬──────┘
                       ▼
                 ┌────────────┐   complete? ── no & round<max ──► back to plan (unmet/failed orders)
                 │  evaluate  │
                 └─────┬──────┘   complete or round == max_rounds
                       ▼
                 ┌────────────┐
                 │  output    │  append assistant message (answer + worker summary)
                 └─────┬──────┘
                       ▼
                      END
```

### 4.3 Data Flow

1. `plan` decomposes the goal into N `SwarmOrder`s (sizing per §3), stores the
   capped list and the deferred overflow under `metadata.skynet`.
2. The dispatcher conditional edge fans out one `Send` per order to `worker_node`,
   injecting the order under `state["_order"]`.
3. Each `worker_node` runs `SwarmWorker.execute()` and appends a `WorkerResult` to
   the list-append `skynet.results` channel (safe under concurrency).
4. `reduce` synthesizes the answer from successful results.
5. `evaluate` decides completion; if incomplete and `round < skynet_max_rounds`,
   the unmet/failed orders (+ any deferred overflow) seed the next `plan`.
6. `output` emits the final message.

All Skynet state is namespaced under `state["metadata"]["skynet"]`.

## 5. Design Decisions

### DD-SKY-001: Reuse `make_parallel_dispatcher` for fan-out

The dispatcher already emits `Send` per task with the operator cap and disabled-flag
handling. Skynet feeds it `metadata.skynet.orders`. **Alternative rejected:** a
bespoke fan-out loop — would duplicate the cap/empty/disabled logic.

### DD-SKY-002: Supervisor owns swarm sizing; operator owns the ceiling

N is the supervisor's decision (dynamic or fixed), but always clamped to
`min(skynet_max_swarm, parallel_max_workers)`. This satisfies "swarm size defined
from the supervisor" without sacrificing bounded, safe fan-out.

### DD-SKY-003: Homogeneous workers in Phase S

One generic `SwarmWorker` executes any order; specialization is carried by
`order.role` and resolved through the injected `ToolProviderPort`. Typed specialist
swarms are Phase S+. **Alternative rejected:** N distinct worker node types — harder
to size dynamically and to test.

### DD-SKY-004: List-append reducer for concurrent results

Workers run concurrently; their `WorkerResult`s merge through an `operator.add`
list channel (`skynet.results`), mirroring `messages` + `add_messages`. **Alternative
rejected:** a shared dict keyed by order id written by all workers — risks
last-write-wins clobbering under LangGraph's state-merge.

### DD-SKY-005: Bounded control loop with deferred overflow

`skynet_max_rounds` caps iterations; overflow beyond the swarm cap is deferred to the
next round rather than dropped, and re-planning targets only unmet/failed orders.
Guarantees termination and progress.

### DD-SKY-006: Callable injection end-to-end

`plan_fn`, `worker_fn`, `reduce_fn`, `evaluate_fn` are injectable; defaults lazily
wire `ProviderRegistry().get_llm()`. The whole loop is unit-testable with
deterministic fakes and **no** provider import.

### DD-SKY-007: Opt-in subgraph + supervisor route

`register_skynet()` is idempotent; the supervisor route and intent match are gated
on `settings.skynet_enabled`. With the flag off, the compiled graph is byte-for-byte
unchanged (snapshot test).

### DD-SKY-008: Security and cost as first-class controls

Sub-order text is isolated via `SecurePromptBuilder`; worker actions pass
`ActionInterceptor.check()`. Fan-out, rounds, and an optional token budget are hard
ceilings — a swarm cannot become a runaway. Everything is audited hash-first.

## 6. Relationship to existing supervisors

| Component | Role | How Skynet differs |
|---|---|---|
| `supervisor.py` (main) | Routes one turn to one specialist | Skynet runs **many** workers in parallel for one order |
| `network_supervisor` / `domain_supervisor` | Supervisor-of-supervisors routing | Skynet is **map-reduce** over a dynamic worker swarm, with a bounded loop |
| `parallel_research` | Fixed parallel research fan-out | Skynet is **goal-agnostic** and supervisor-sized, with re-planning |

Skynet is wired as one route off the main supervisor (like a domain supervisor); the
main supervisor delegates a parallelizable order to Skynet, which returns a single
reduced result.

## 7. Security

| Vector | Control |
|---|---|
| Prompt injection via sub-order text | `SecurePromptBuilder` (canary tokens); never raw-concatenated |
| Runaway fan-out / cost | `skynet_max_swarm` ∧ `parallel_max_workers` ∧ `skynet_max_rounds` ∧ optional `skynet_token_budget` |
| Unsafe worker action | `ActionInterceptor.check()` + guardrails on every tool/file/code op |
| Non-termination | Round cap + monotonic re-plan (only unmet/failed orders) |
| Sensitive content in logs | `AuditLogger` hash-first (plan size, worker hashes, reduce hash) |

## 8. Observability

### 8.1 OTel Spans

`skynet.plan` (attrs: `swarm_size_requested`, `swarm_size_effective`, `deferred`),
`skynet.dispatch`, `skynet.worker` (attrs: `order_id`, `success`, `tool_calls`),
`skynet.reduce` (attr: `strategy`), `skynet.evaluate` (attrs: `complete`, `round`).

### 8.2 Metrics

```
prismal.skynet_runs_total
prismal.skynet_swarm_size           (histogram)
prismal.skynet_rounds               (histogram)
prismal.skynet_orders_deferred_total
prismal.skynet_worker_failures_total
```

## 9. Testing Strategy (summary; detail in `TASKS.md`)

- **Sizing**: dynamic vs. fixed N; cap clamps N and defers overflow (audited).
- **Dispatch**: dispatcher emits exactly `plan.size` (capped) `Send`s; empty/disabled
  routes to `on_empty`.
- **Worker**: tool action passes `ActionInterceptor.check()` (spy); a failing worker
  yields `WorkerResult(success=False)` without aborting the swarm.
- **Reduce**: `synthesis`/`concat`/`first_success` behave per spec; failures excluded
  but retained.
- **Loop**: evaluate can re-plan; never exceeds `skynet_max_rounds`; deferred orders
  resume next round.
- **Subgraph**: end-to-end with fakes; no provider/mcp/skills import (AST guard reused).
- **Integration**: snapshot unchanged when `skynet_enabled=False`.

## 10. Rollout

1. Land `agents/skynet/` value objects + `SkynetSupervisor` + `SwarmWorker` +
   `reduce_results` (pure, injected).
2. Land the `skynet/` subgraph wiring the dispatcher + reducer channel (off by default).
3. Wire the opt-in supervisor route + intent match behind `skynet_enabled`.
4. Docs + example (`examples/skynet_swarm.py`).
