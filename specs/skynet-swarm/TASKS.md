# Prismal Skynet Swarm Supervisor — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/skynet-swarm/PLAN.md` |
| **SPEC** | `specs/skynet-swarm/SPEC.md` |
| **Architecture** | `specs/skynet-swarm/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Skynet is delivered in six phases (S1–S6), each independently testable and landing
behind `settings.skynet_enabled` (default `False`) so `main` stays green and the
existing agents are unaffected until the final wiring phase. Every component uses
callable injection, so all unit tests run without an LLM backend. Skynet composes
existing primitives (`make_parallel_dispatcher`, `swarm`, `SecurePromptBuilder`,
`ActionInterceptor`, `AuditLogger`) — no new LangGraph capability.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Branch `feature/new_agents` (current).
- Reuse, do not modify: `agents/patterns/parallel.py`, `agents/patterns/swarm.py`,
  `security/secure_prompt.py`, `security/action_interceptor.py`, `security/audit.py`,
  `agents/subgraphs/registry.py`, `agents/intent_router.py`, and the injected
  `ToolProviderPort`.
- Confirm the worker fan-out can write to a list-append (`operator.add`) channel in
  the subgraph's state schema (mirrors `messages` + `add_messages`).

## 3. Implementation Phases

### PHASE S1 — Types + Exceptions + Settings

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| S1-01 | `agents/skynet/types.py`: `SwarmOrder`, `SwarmPlan`, `WorkerResult`, `SwarmResult` | 0.4 d | — | DONE |
| S1-02 | `core/exceptions.py`: `SkynetError` hierarchy (SPEC-SKY-ERR-001) | 0.2 d | — | DONE |
| S1-03 | `core/config.py`: `skynet_*` settings (SPEC-SKY-CFG-001) | 0.3 d | — | DONE |
| S1-04 | Settings validation (`skynet_swarm_size ≥ 0`; clamp `skynet_max_swarm` ≤ `parallel_max_workers`) | 0.2 d | S1-03 | DONE |

**Done when:** value objects round-trip; settings parse from `PRISMAL_*`; fixed size
> cap raises `SkynetConfigError`.

### PHASE S2 — Supervisor (`agents/skynet/supervisor.py`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| S2-01 | `SkynetSupervisor.__init__` with injected `plan_fn` / `evaluate_fn` | 0.4 d | S1 | DONE |
| S2-02 | `plan()`: decompose goal (dynamic vs. fixed size); seed from `unmet` orders | 0.7 d | S2-01 | DONE |
| S2-03 | Cap N at `min(skynet_max_swarm, parallel_max_workers)`; return deferred overflow | 0.4 d | S2-02 | DONE |
| S2-04 | `evaluate()`: `(complete, answer)`; goal/results via `SecurePromptBuilder` | 0.5 d | S2-01 | DONE |
| S2-05 | Default `plan_fn`/`evaluate_fn` lazily wire `ProviderRegistry().get_llm()` | 0.3 d | S2-01 | DONE |

**Done when:** dynamic mode varies N with the goal; fixed mode yields exactly K;
overflow is deferred (not dropped) and visible in audit.

### PHASE S3 — Worker + Reduce

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| S3-01 | `SwarmWorker.execute()`: secure prompt, tools via `ToolProviderPort`, gated actions | 0.7 d | S1 | DONE |
| S3-02 | Per-worker failure captured as `WorkerResult(success=False)` (never raised out) | 0.3 d | S3-01 | DONE |
| S3-03 | `reduce_results()`: `synthesis` \| `concat` \| `first_success` | 0.5 d | S1 | DONE |

**Done when:** a worker's tool action passes `ActionInterceptor.check()` (spy);
reduce excludes failures but retains them; one failing worker does not abort others.

### PHASE S4 — Subgraph (`agents/subgraphs/skynet/`)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| S4-01 | State schema with list-append `skynet.results` channel | 0.4 d | S1 | DONE |
| S4-02 | `plan_node`, `worker_node`, `reduce_node`, `evaluate_node`, `output_node` | 0.8 d | S2, S3 | DONE |
| S4-03 | Dispatch conditional edge via `make_parallel_dispatcher(tasks_field, worker_node, max_workers)` | 0.4 d | S4-02 | DONE |
| S4-04 | Re-plan edge: evaluate → plan when not complete and `round < max_rounds` | 0.4 d | S4-02 | DONE |
| S4-05 | `build_skynet_subgraph()` + idempotent `register_skynet()` | 0.3 d | S4-02 | DONE |
| S4-06 | All Skynet state under `state["metadata"]["skynet"]` | 0.2 d | S4-02 | DONE |

**Done when:** the subgraph runs end-to-end with injected fakes and no provider /
mcp / skills import (AST guard reused); fan-out respects the cap; the loop terminates.

### PHASE S5 — Supervisor + intent integration (the only behavior-changing phase)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| S5-01 | `intent_router.match_intent()` returns `skynet` for swarm/parallel intents | 0.3 d | S4 | DONE |
| S5-02 | `get_async_compiled_graph()` wires `skynet` route when `skynet_enabled` | 0.4 d | S4 | DONE |
| S5-03 | `effective_valid_routes` / `build_system_prompt` gate on the flag | 0.3 d | S5-02 | DONE |
| S5-04 | `DEFAULT_CAPABILITY_MAP["skynet_worker"]` (tools via `ToolProviderPort`) | 0.2 d | S5-02 | DONE |

**Done when:** with `skynet_enabled=False` the compiled-graph snapshot is unchanged;
with `True` a parallel-decomposition intent routes to `skynet` end-to-end.

### PHASE S6 — Tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| S6-01 | Unit tests: sizing (dynamic/fixed/cap/defer) | 0.5 d | S2 | DONE |
| S6-02 | Unit tests: worker (secure-prompt + interceptor spies; failure isolation) | 0.4 d | S3 | DONE |
| S6-03 | Unit tests: reduce strategies | 0.3 d | S3 | DONE |
| S6-04 | Unit tests: dispatcher fan-out count + cap + empty/disabled | 0.4 d | S4 | DONE |
| S6-05 | Unit tests: control loop (re-plan, max-rounds, deferred resume) | 0.5 d | S4 | DONE |
| S6-06 | Unit tests: subgraph end-to-end with fakes + no-provider-import guard | 0.5 d | S4 | DONE |
| S6-07 | Integration test: graph snapshot unchanged when `skynet_enabled=False` | 0.3 d | S5 | DONE |
| S6-08 | `docs/skynet.md` + `examples/skynet_swarm.py` | 0.5 d | S5 | DONE |
| S6-09 | `README.md` + `CHANGELOG.md` entries | 0.2 d | S5 | DONE |

**Done when:** `uv run pytest -m unit` green; `ruff`, `mypy --strict`, `bandit`
clean; coverage ≥ project target on new modules.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Runaway fan-out / cost | Hard caps (`skynet_max_swarm`, `parallel_max_workers`, `skynet_max_rounds`, optional budget) |
| Non-terminating re-plan | Round cap; re-plan only unmet/failed orders (monotonic progress) |
| Concurrent result clobbering | List-append (`operator.add`) channel keyed by `order_id` |
| Prompt injection via sub-orders | `SecurePromptBuilder`; worker actions gated by `ActionInterceptor` |
| Behavior leak when disabled | Gate every wiring point on `skynet_enabled`; snapshot test (S6-07) |

## 5. Definition of Done (feature)

- [x] All MUST requirements (RF-SKY-01…RF-SKY-14) implemented and tested.
- [x] Supervisor-sized swarm runs a parallel order end-to-end and reduces to one result.
- [x] Fan-out, rounds, and budget caps proven by tests (token budget enforcement is S+;
      the `skynet_token_budget` setting and `SkynetBudgetExceeded` ship now).
- [x] With `skynet_enabled=False`, zero behavior change (snapshot proven).
- [x] No provider SDK or `prismal.mcp`/`prismal.skills` import inside `agents/skynet/`.
- [x] `ruff` + `mypy --strict` + `bandit` clean; unit suite green.
- [x] `PLAN`/`SPEC`/`ARCHITECTURE` marked `IMPLEMENTED`; `README`/`CHANGELOG` updated.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| S1 | Types + exceptions + settings | ~1.1 d |
| S2 | Supervisor (plan + evaluate + sizing) | ~2.3 d |
| S3 | Worker + reduce | ~1.5 d |
| S4 | Subgraph + dispatcher + loop | ~2.5 d |
| S5 | Supervisor integration | ~1.2 d |
| S6 | Tests + docs + packaging | ~3.6 d |
| **Total** | | **~12.2 d** |
