# Skynet — Swarm Supervisor (map-reduce over agents)

**Skynet** is Prismal's opt-in swarm supervisor: a meta-supervisor that
decomposes one order into many independent sub-orders, dispatches a
**dynamically-sized swarm of workers** (LangGraph `Send` fan-out), reduces
their outputs into a single answer, and iterates until the goal is met — all
bounded, observable, and audited.

> Naming: *Skynet* (the Terminator franchise's distributed command AI) is a
> thematic metaphor for a central controller coordinating many autonomous
> units — the behavior is bounded, audited, and human-gated by design. It
> pairs with the sibling [`kokoro`](./kokoro.md) deliberation layer.

> Specs: [`specs/skynet-swarm/`](../specs/skynet-swarm/) ·
> Example: [`examples/skynet_swarm.py`](../examples/skynet_swarm.py)

## Quick start

```bash
export PRISMAL_SKYNET_ENABLED=true
```

```python
from prismal.agents.subgraphs.skynet import build_skynet_subgraph, register_skynet
from prismal.agents.subgraphs.factory import assemble_state_graph
from langchain_core.messages import HumanMessage

graph = assemble_state_graph(build_skynet_subgraph()).compile()
result = await graph.ainvoke(
    {"messages": [HumanMessage(content="Research competitors A, B and C in parallel")]}
)
print(result["messages"][-1].content)                  # answer + swarm summary
print(result["metadata"]["skynet"]["result"])          # SwarmResult value object
```

With the supervisor (`skynet_enabled=True`), swarm intents ("research these in
parallel", "fan this out", "run a swarm over…", "split this across agents", or
any mention of *skynet*) route deterministically to the `skynet` subgraph via
`intent_router.match_intent()`. With the flag off (the default) the framework
is byte-for-byte unchanged (snapshot-tested).

## Pipeline

```
plan ──[one Send per order, ≤ cap]──► worker ⇉ … ⇉ worker → reduce → evaluate ─┐
  ▲                                                                              │
  └────────────── re-plan while not complete and round < max_rounds ────────────┘
                                       │ complete / round cap
                                       ▼
                                    output → END
```

1. **plan** — `SkynetSupervisor.plan()` decomposes the goal into N
   `SwarmOrder`s. **The supervisor sizes the swarm**: dynamic by default
   (`N = len(plan.orders)`), or fixed when `skynet_swarm_size > 0` (exactly K
   load-balanced orders). N is hard-capped at
   `min(skynet_max_swarm, parallel_max_workers)`; overflow orders are
   **deferred to the next round, never dropped**.
2. **fan-out** — the reused `make_parallel_dispatcher()` emits one `Send` per
   order; workers run concurrently and merge their results through an
   `operator.add` channel (no clobbering).
3. **worker** — each `SwarmWorker.execute()` runs one order: secure prompt,
   tools resolved per `order.role` through the injected `ToolProviderPort`,
   every requested tool action gated by `ActionInterceptor`. A failing worker
   yields `WorkerResult(success=False)` — it never aborts the swarm.
4. **reduce** — `reduce_results()` merges the successful outputs
   (`synthesis` | `concat` | `first_success`); a failing synthesis degrades to
   the deterministic concat.
5. **evaluate** — `SkynetSupervisor.evaluate()` decides completion. Unmet:
   failed orders + deferred overflow seed the next round (a deterministic
   re-dispatch — no re-planning LLM call), bounded by `skynet_max_rounds`.
6. **output** — appends the answer + per-worker summary; the full
   `SwarmResult` lands under `state["metadata"]["skynet"]["result"]`.

All durable state lives under `state["metadata"]["skynet"]`.

## Swarm sizing — who decides N?

| Mode | Setting | Behaviour |
|---|---|---|
| **Dynamic** (default) | `skynet_swarm_size=0` | The planner chooses N from the goal's structure ("research 8 competitors" → N≈8, capped) |
| **Fixed** | `skynet_swarm_size=K` | The planner must emit exactly K load-balanced orders; `K > cap` raises `SkynetConfigError` at settings load |

The operator ceiling always wins: `N_effective = min(N, skynet_max_swarm,
parallel_max_workers)`; overflow is deferred and surfaced in audit.

## Settings

| Setting (`PRISMAL_*`) | Default | Purpose |
|---|---|---|
| `skynet_enabled` | `False` | Master opt-in toggle (supervisor route + intents) |
| `skynet_swarm_size` | `0` | `0` = dynamic (supervisor chooses N); `>0` = fixed N |
| `skynet_max_swarm` | `8` | Hard cap on workers per round (clamped to `parallel_max_workers`) |
| `skynet_max_rounds` | `3` | Hard cap on plan→dispatch→evaluate iterations |
| `skynet_reduce_strategy` | `"synthesis"` | `synthesis` \| `concat` \| `first_success` |
| `skynet_worker_model` | `""` | Optional worker model override |
| `skynet_planner_model` | `""` | Optional planner/evaluator/reducer model override |
| `skynet_token_budget` | `0` | `0` = unlimited; `>0` = soft per-run token budget (S+) |

## Security model

- Sub-order text (the goal, instructions, worker outputs) is **user-derived
  content**: it reaches a model only through `SecurePromptBuilder` (canary
  tokens, `<user_input>` isolation, `InputSanitizer`) — never f-stringed.
- Every worker tool action passes the `ActionInterceptor` gateway first; a
  denial is noted in the output (no exception). Unresolved tools never reach
  the gate.
- Workers resolve tools **only** through the injected `ToolProviderPort`
  (`agent_name="skynet_worker"`, `capabilities=[order.role]`) — Skynet never
  imports `prismal.mcp` / `prismal.skills` (AST-guarded).
- A swarm cannot run away: fan-out, rounds, and the operator ceilings are hard
  caps; overflow is deferred and audited, never silent.
- `AuditLogger` records plan, fan-out sizes, and evaluations **hash-first**
  (`skynet_plan` / `skynet_evaluate` events) — never raw content.

## Testing your own composition

Every backend is callable-injected, so the full loop runs without an LLM:

```python
from prismal.agents.skynet import SwarmOrder, SwarmPlan

async def fake_plan(messages):
    return SwarmPlan(goal="", orders=[SwarmOrder(order_id="ord-1", instruction="part 1")])

async def fake_worker(messages): return "done"
async def fake_evaluate(messages): return (True, "all parts done")
async def fake_reduce(goal, results): return "combined"

definition = build_skynet_subgraph(
    plan_fn=fake_plan, worker_fn=fake_worker,
    evaluate_fn=fake_evaluate, reduce_fn=fake_reduce,
)
```

Injection points: `plan_fn` / `evaluate_fn` (or a full `supervisor=`),
`worker_fn` / `tool_provider` / `interceptor` (or a full `worker=`),
`reduce_fn`, plus `audit` / `prompt_builder` on `SkynetSupervisor` and
`SwarmWorker`.

## Observability

OTel spans per stage — `skynet.plan` (`swarm_size_requested`,
`swarm_size_effective`, `deferred`, `round`), `skynet.worker` (`order_id`,
`success`, `tool_calls`), `skynet.reduce` (`strategy`, `fallback`),
`skynet.evaluate` (`complete`, `failures`) — and structured logs throughout.
