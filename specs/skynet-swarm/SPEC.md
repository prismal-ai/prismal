# Prismal Skynet Swarm Supervisor — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/skynet-swarm/PLAN.md` |
| **Architecture** | `specs/skynet-swarm/ARCHITECTURE.md` |
| **TASKS** | `specs/skynet-swarm/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Async where applicable (planner/worker/evaluator LLM calls are `async`); pure
  helpers are `sync`.
- Frozen dataclasses / Pydantic models for value objects.
- Constructors accept `settings: Settings | None = None`.
- No provider SDK imports outside `prismal/providers/`.
- **Callable injection** in every component (`plan_fn`, `worker_fn`, `reduce_fn`,
  `evaluate_fn`) so tests run without an LLM backend.
- Sub-order text is **user-derived**: it reaches a model only via
  `SecurePromptBuilder`; never f-stringed into a prompt.
- Reuse `make_parallel_dispatcher` for fan-out and `swarm_handoff`/`HandoffRecord`
  for optional peer handoff; do not fork them.
- Skynet must not import `prismal.mcp` / `prismal.skills`; tools come from the
  injected `ToolProviderPort`.
- All Skynet runtime state lives under `state["metadata"]["skynet"]`.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/agents/skynet/types.py` | `SwarmOrder`, `SwarmPlan`, `WorkerResult`, `SwarmResult` |
| `prismal/agents/skynet/supervisor.py` | `SkynetSupervisor` — `plan()` + `evaluate()` |
| `prismal/agents/skynet/worker.py` | `SwarmWorker` — `execute()` one order |
| `prismal/agents/skynet/reduce.py` | `reduce_results()` — merge worker outputs |
| `prismal/agents/subgraphs/skynet/` | LangGraph subgraph + `build_*`/`register_*` |
| `prismal/core/config.py` | Settings extension (`skynet_*`) |
| `prismal/core/exceptions.py` | `SkynetError` hierarchy |

---

## SPEC-SKY-TYP-001: Value objects (`agents/skynet/types.py`)

```python
@dataclass(frozen=True)
class SwarmOrder:
    """A single sub-order to be executed by one worker."""
    order_id: str                 # stable id, e.g. "ord-3"
    instruction: str              # what this worker must do (user-derived)
    role: str = "worker"          # optional specialist role (Phase S+: capability key)
    context: dict = field(default_factory=dict)   # small, audited context snapshot
    attempt: int = 1              # incremented on re-dispatch


@dataclass(frozen=True)
class SwarmPlan:
    """The supervisor's decomposition of an order into sub-orders."""
    goal: str
    orders: list[SwarmOrder]
    round: int = 1                # 1-indexed control-loop round
    rationale: str = ""           # why the work was split this way

    @property
    def size(self) -> int:        # the swarm size the supervisor chose
        return len(self.orders)


@dataclass(frozen=True)
class WorkerResult:
    order_id: str
    output: str
    success: bool
    error: str | None = None
    tool_calls: int = 0


@dataclass(frozen=True)
class SwarmResult:
    """The reduced outcome of one or more rounds."""
    goal: str
    answer: str                   # synthesized final result
    worker_results: list[WorkerResult]
    rounds_completed: int
    deferred_orders: list[SwarmOrder]   # capped overflow, carried to next round
    complete: bool
```

## SPEC-SKY-SUP-001: `SkynetSupervisor` (`agents/skynet/supervisor.py`)

The meta-supervisor. It owns swarm sizing and the control loop.

```python
PlanFn = Callable[[str], Awaitable[SwarmPlan]]                 # (secure_prompt) -> plan
EvaluateFn = Callable[[str], Awaitable[tuple[bool, str]]]      # -> (complete, synthesized_answer)


class SkynetSupervisor:
    def __init__(
        self,
        *,
        plan_fn: PlanFn | None = None,        # default wires ProviderRegistry().get_llm()
        evaluate_fn: EvaluateFn | None = None,
        prompt_builder: SecurePromptBuilder | None = None,
        audit: AuditLogger | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def plan(self, goal: str, *, round: int = 1, unmet: list[SwarmOrder] | None = None) -> SwarmPlan:
        """Decompose *goal* into a SwarmPlan.

        Swarm sizing (RF-SKY-02/03):
          • settings.skynet_swarm_size == 0 → dynamic: planner chooses len(orders).
          • settings.skynet_swarm_size  > 0 → fixed: planner must emit exactly that
            many orders (load-balanced).
          • If a prior round left `unmet` orders, they seed this round's plan.
        The resulting plan.size is hard-capped at min(skynet_max_swarm,
        settings.parallel_max_workers); overflow orders are returned via the plan's
        deferred set rather than dropped.

        Security: *goal* is wrapped via SecurePromptBuilder before planning.
        """

    async def evaluate(self, goal: str, results: list[WorkerResult]) -> tuple[bool, str]:
        """Decide whether the goal is met and synthesize the current best answer.

        Returns (complete, answer). When not complete, the control loop re-plans
        the unmet/failed orders for the next round (bounded by skynet_max_rounds).
        """
```

## SPEC-SKY-WRK-001: `SwarmWorker` (`agents/skynet/worker.py`)

A homogeneous worker that executes exactly one `SwarmOrder`.

```python
WorkerFn = Callable[[str], Awaitable[str]]                     # (secure_prompt) -> output


class SwarmWorker:
    def __init__(
        self,
        *,
        worker_fn: WorkerFn | None = None,
        tool_provider: ToolProviderPort | None = None,         # injected; resolves the worker's tools
        interceptor: ActionInterceptor | None = None,
        prompt_builder: SecurePromptBuilder | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def execute(self, order: SwarmOrder) -> WorkerResult:
        """Execute one order and return a WorkerResult.

        Builds a secure prompt from order.instruction (+ small context), resolves
        tools for the order.role via the injected ToolProviderPort, runs worker_fn,
        and gates any tool/file/code action through ActionInterceptor.check().
        Failures are captured as WorkerResult(success=False, error=...), never raised
        out of the node (so one worker's failure does not abort the swarm).
        """
```

## SPEC-SKY-RED-001: Reduce (`agents/skynet/reduce.py`)

```python
ReduceFn = Callable[[str, list[WorkerResult]], Awaitable[str]]   # (goal, results) -> synthesis


async def reduce_results(
    goal: str,
    results: list[WorkerResult],
    *,
    reduce_fn: ReduceFn | None = None,        # default: LLM synthesis; deterministic concat fallback
    strategy: Literal["synthesis", "concat", "first_success"] = "synthesis",
    settings: Settings | None = None,
) -> str:
    """Merge worker outputs into a single answer.

    `synthesis` (default) asks the model to combine successful outputs; `concat`
    deterministically joins them (no LLM); `first_success` returns the earliest
    successful output. Failed results are excluded from the synthesis but retained
    in the SwarmResult for audit / re-planning.
    """
```

## SPEC-SKY-SG-001: Subgraph (`agents/subgraphs/skynet/`)

```
plan_node → dispatch (Send fan-out) → worker_node ⇉ reduce_node → evaluate_node ─┐
   ▲                                                                              │
   └───────────────── re-plan when not complete and round < max_rounds ──────────┘
                                         │ complete or round cap
                                         ▼
                                    output_node → END
```

- `plan_node` — `SkynetSupervisor.plan()`; writes `SwarmPlan` and the orders list to
  `state["metadata"]["skynet"]["orders"]`.
- `dispatch` — a **conditional edge** built with
  `make_parallel_dispatcher(tasks_field="metadata.skynet.orders", worker_node="skynet_worker",
  max_workers=skynet_max_swarm, task_key="_order")`; emits one `Send` per order
  (≤ `min(skynet_max_swarm, parallel_max_workers)`).
- `worker_node` — wraps `SwarmWorker.execute(state["_order"])`; appends a
  `WorkerResult` to the `skynet.results` channel (list-append reducer → safe under
  concurrency).
- `reduce_node` — `reduce_results()`; writes the synthesized answer.
- `evaluate_node` — `SkynetSupervisor.evaluate()`; sets `complete`. If not complete
  and `round < skynet_max_rounds`, routes back to `plan_node` with the unmet orders;
  otherwise to `output_node`.
- `output_node` — appends the final assistant message (answer + per-worker summary).

```python
def build_skynet_subgraph(settings: Settings | None = None) -> SubgraphDefinition:
    """Return the SubgraphDefinition for the skynet swarm subgraph."""

async def register_skynet(registry: SubgraphRegistry | None = None) -> None:
    """Idempotently register the skynet subgraph (mirrors register_debate_consensus)."""
```

### State channel for concurrent results

```python
# In AgentState metadata, the worker fan-out writes to a list-append channel so
# concurrent Send invocations merge without clobbering:
#   state["metadata"]["skynet"]["results"]: Annotated[list[WorkerResult], operator.add]
# (Implemented via the subgraph's state schema; mirrors how `messages` uses add_messages.)
```

## SPEC-SKY-INT-001: Supervisor + intent integration

- `settings.skynet_enabled` (default `False`). When `True`,
  `get_async_compiled_graph()` wires a single `skynet` supervisor route;
  `effective_valid_routes` / `build_system_prompt` gate on the flag.
- `intent_router.match_intent()` returns `skynet` for swarm/parallel-decomposition
  intents (regex over phrases like "do these in parallel", "fan this out", "swarm",
  "split this across agents"). Deterministic, ahead of LLM supervision.
- `DEFAULT_CAPABILITY_MAP["skynet_worker"]` declares the worker's default capability
  set, resolved through the injected `ToolProviderPort`.
- With `skynet_enabled=False`, behavior is byte-for-byte unchanged.

## SPEC-SKY-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `skynet_enabled` | `bool` | `False` | Master opt-in toggle |
| `skynet_swarm_size` | `int` | `0` | `0` = dynamic (supervisor chooses N); `>0` = fixed N |
| `skynet_max_swarm` | `int` | `8` | Hard cap on workers per round (≤ `parallel_max_workers`) |
| `skynet_max_rounds` | `int` | `3` | Hard cap on plan→dispatch→evaluate iterations |
| `skynet_reduce_strategy` | `str` | `"synthesis"` | `synthesis` \| `concat` \| `first_success` |
| `skynet_worker_model` | `str` | `""` | Optional worker model override |
| `skynet_planner_model` | `str` | `""` | Optional planner/evaluator model override |
| `skynet_token_budget` | `int` | `0` | `0` = unlimited; `>0` = soft per-run token budget |

Env prefix `PRISMAL_` (e.g. `PRISMAL_SKYNET_ENABLED`, `PRISMAL_SKYNET_MAX_SWARM`).
`skynet_max_swarm` is additionally clamped to `settings.parallel_max_workers` at
dispatch time (the operator-visible ceiling always wins).

## SPEC-SKY-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class SkynetError(PrismalError): ...
class SkynetPlanError(SkynetError): ...
class SwarmWorkerError(SkynetError): ...      # captured per-worker, not raised out of the node
class SkynetConfigError(SkynetError): ...     # e.g. fixed size > cap, invalid budget
class SkynetBudgetExceeded(SkynetError): ...
```

## Acceptance Criteria (per requirement)

| Requirement | Acceptance criterion |
|---|---|
| RF-SKY-01 | `plan()` returns a `SwarmPlan` with ≥1 `SwarmOrder`; `goal` round-trips |
| RF-SKY-02 | `skynet_swarm_size=0` → N varies with the goal; `=K` → `plan.size == K` |
| RF-SKY-03 | With `skynet_max_swarm=3` and a 5-order plan, 3 dispatch and 2 are deferred (audited) |
| RF-SKY-04 | The dispatcher emits exactly `plan.size` (capped) `Send` objects |
| RF-SKY-06 | Concurrent workers' `WorkerResult`s all appear in `skynet.results` (list-append reducer) |
| RF-SKY-07/08 | `evaluate()` can request a re-plan; the loop never exceeds `skynet_max_rounds` |
| RF-SKY-10 | With `skynet_enabled=False`, the compiled graph has no `skynet` route (snapshot test) |
| RF-SKY-11 | Full subgraph runs end-to-end with injected fakes and no provider import |
| RF-SKY-14 | A worker's tool action passes through `ActionInterceptor.check()` (spy-verified) |
