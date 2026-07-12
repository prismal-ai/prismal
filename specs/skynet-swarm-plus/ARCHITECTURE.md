# Prismal Skynet S+ — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (v3.12.0, 2026-07-12, TDD) |
| **Version** | 1.0 |
| **Date** | 2026-07-11 |
| **Phase** | S+ |
| **PLAN** | `specs/skynet-swarm-plus/PLAN.md` |
| **SPEC** | `specs/skynet-swarm-plus/SPEC.md` |
| **TASKS** | `specs/skynet-swarm-plus/TASKS.md` |
| **Parent** | `specs/skynet-swarm/` (Phase S) |

---

## 1. Context

Phase S ships a bounded swarm supervisor with **homogeneous, in-process,
partially-metered** workers (`DD-SKY-003`). S+ turns each worker into a
**specialist** (own model/persona/tools), makes the swarm **truthfully metered**
(worker tokens counted against `skynet_token_budget`), and lets a role be a
**remote A2A agent**. It composes primitives that already shipped after Phase S:
Phase C (`prismal/budget/`) and Phase I (`prismal/a2a/`). No new LangGraph
capability and **no change to the Phase-S subgraph topology**.

## 2. The three deficits S+ closes (verified against the code)

| Deficit (Phase S) | Evidence | S+ fix |
|---|---|---|
| Workers are homogeneous | `worker.py::_default_worker` uses one `skynet_worker_model`; `order.role` only feeds `capabilities=[order.role]` for tools | Role registry → per-role model + persona + tools (§4.1) |
| Worker tokens unmetered | `_default_worker` calls `llm.ainvoke` with **no** `record_response`; `SwarmWorker` has no `CostMeter`; supervisor holds a separate meter, so `enforce_token_budget()` counts planner+evaluator only | One **shared** meter threaded into supervisor + worker + reducer (§4.2) |
| No remote workers | `PLAN §6.3` "in-process; no remote workers in Phase S" | Role-bound A2A delegation via `A2AConnectionManager` (§4.3) |

## 3. Feasibility (confirmed against shipped code)

- **Role registry** — data only; `SwarmOrder.role` already exists (a Phase-S
  hook: *"Optional specialist role (Phase S+: capability key)"*). No topology
  change.
- **Metering** — `CostMeter.record_response(message, model, *, agent=, pattern=)`
  and `Usage` already exist (Phase C). The only structural change is **sharing
  one meter** across the supervisor and workers (the builder already constructs
  both — it just constructs them independently today).
- **Remote** — `A2AClient.send_task(msg, *, skill_id=)` and
  `A2AConnectionManager(allowlist, strict).get_client(url)` already exist
  (Phase I). A remote worker is still exactly one `Send` invocation of
  `skynet_worker`; only the worker's *backend* changes.

## 4. Proposed Architecture

### 4.1 Specialist swarms (S+1)

```
plan_node ── planner tags each order with a role ──► SwarmOrder(role="researcher")
                                                       │
skynet_worker: role = RoleRegistry.resolve(order.role)
   model  = role.model or skynet_worker_model      ─┐
   tools  = tool_provider.get_tools(               │  heterogeneous
              agent_name="skynet_worker",          │  by construction
              capabilities=role.capabilities)      │
   prompt = SecurePromptBuilder(system=role.persona,│
              user=order.instruction)              ─┘
```

- New module `agents/skynet/roles.py`: `SpecialistRole` + `RoleRegistry`
  (`from_yaml` mirrors `ToolPolicyEngine`'s YAML load; `resolve` never raises,
  falls back to `DEFAULT_ROLE`).
- The **planner** (default fn) is told the registry's `known_roles()` and tags
  each order; a bad/absent tag → `"worker"`. With `skynet_specialists_enabled`
  off, the planner prompt is the Phase-S prompt and every role stays `"worker"`.
- The **worker** resolves the role at execution time. Role `"worker"` reproduces
  Phase-S behaviour exactly (same model, empty persona, `capabilities=["general"]`
  ≈ today's `[order.role]="worker"`).

### 4.2 Metered workers (S+2) — the shared-meter fix

The root cause of the under-count is that `build_skynet_subgraph` constructs the
supervisor and the worker as **separate objects**, so the supervisor's
`self._meter` never reaches the worker. S+ threads **one** meter:

```
build_skynet_subgraph():
    meter = CostMeter(settings)                 # ONE per build
    supervisor = SkynetSupervisor(meter=meter, ...)   # planner+evaluator record here
    worker     = SwarmWorker(meter=meter, ...)        # every worker records here too
    reducer records via reduce_results(meter=meter)   # default synthesis reducer too
```

- `SwarmWorker.execute()` records its `worker_fn` response into the shared meter
  (`record_response`), populates `WorkerResult.usage`, and `SwarmResult.usage`
  becomes the true swarm total.
- `enforce_token_budget()` is unchanged in shape but now truthful.
- **Optional Phase-C convergence (DD-SP-004):** when `budget_enabled`, the
  builder can additionally reuse the per-run `_RUN_ENGINES` meter/guard
  (`seed_budget_run`/`get_budget_guard`, keyed by `session_id`) and pass
  `make_budget_guard_fn(guard)` as the worker's `budget_guard_fn`, so a *soft*
  cap degrades (stop dispatching new orders, reduce on completed) and a *hard*
  cap raises `SkynetBudgetExceeded` — unifying Skynet's bespoke check with the
  general budget engine used by `react_loop` and the expensive patterns.

### 4.3 Remote workers (S+3)

```
role.remote_agent set + skynet_remote_workers_enabled + a2a_enabled
        │
skynet_worker ── send_fn(role, order) ──► A2AConnectionManager.get_client(role.remote_agent)
                                              .send_task(A2AMessage(order.instruction))
        │  concat streamed artifact text
        ▼
   InputSanitizer.sanitize(text)  ──►  audit a2a.outbound  ──►  WorkerResult(remote=True)
```

- New module `agents/skynet/remote.py::make_remote_send_fn(...)` returns the
  injected `send_fn`. It reuses Phase I's `A2AConnectionManager` (allowlist +
  pool + strict deny-all) — no new client code.
- A remote worker is **still one `Send`**; the swarm topology, dispatcher, and
  reducer are untouched. `WorkerResult.remote=True` is the only new signal.
- Failure containment reuses the Phase-S invariant: `A2AAgentUnavailable` (deny/
  timeout/unreachable) is caught in `execute()` → `WorkerResult(success=False)`.

### 4.4 New / changed modules

```
prismal/agents/skynet/
├── roles.py        ← NEW: SpecialistRole + RoleRegistry
├── remote.py       ← NEW: make_remote_send_fn (A2A delegation)
├── types.py        ← EXTEND: WorkerResult.usage/role/remote; SwarmResult.usage
├── supervisor.py   ← EXTEND: meter injection; role-tagging planner prompt
├── worker.py       ← EXTEND: role resolution; metering; remote path; budget_guard_fn
└── reduce.py       ← EXTEND: meter the default reducer
prismal/agents/subgraphs/skynet/builder.py  ← EXTEND: one meter, role registry, send_fn
config/skynet_roles.example.yaml            ← NEW
prismal/core/{config,exceptions}.py         ← EXTEND (settings + SkynetRoleError)
```

Reused unchanged: `make_parallel_dispatcher`, the `parallel_results` reducer
channel, `_helpers.py`, `plan_node`/`worker_node`/`reduce_node`/`evaluate_node`/
`output_node` wiring, `register_skynet`, the `skynet` supervisor route, and
`intent_router`.

## 5. Design Decisions

### DD-SP-001: Reverse DD-SKY-003 with a *data* registry, not new nodes
Heterogeneity is a property of *what a worker resolves for its role*, not of the
graph shape. A `RoleRegistry` (file-driven, like the Phase-H tool policies) keeps
the swarp topology fixed and testable. **Rejected:** one graph node per role —
would fork the dispatcher and break the fan-out.

### DD-SP-002: Role defaults to `"worker"`, registry falls back silently
`SwarmOrder.role="worker"` and `RoleRegistry.resolve(unknown)→DEFAULT_ROLE`
guarantee Phase-S behaviour when specialists are off or a role is missing.
Resolve never raises (only *load* can, once, at startup). **Rejected:** raising on
unknown role — would let one bad planner tag abort a swarm.

### DD-SP-003: One shared `CostMeter`, injected by the builder
The builder owns the single meter and injects it into supervisor + worker +
reducer, so usage accumulates once per call at each seam. **Rejected:** a global
process meter (breaks per-run/tenant isolation) and per-worker meters summed
later (double-counts, races under fan-out).

### DD-SP-004: Truthful count now; Phase-C `BudgetGuard` convergence is opt-in
The minimum fix (make `enforce_token_budget` truthful) ships first and is
self-contained. Wiring `make_budget_guard_fn` for soft-degrade is layered on top
behind `budget_enabled`, so the token-accuracy fix is not coupled to the larger
guard/registry unification. **Rejected:** replacing `enforce_token_budget`
wholesale — larger blast radius, unnecessary for the accuracy fix.

### DD-SP-005: Remote worker = role-bound `send_fn`, not remote *tools*
A remote *worker* (the whole order runs elsewhere) is modelled as an injected
`send_fn` chosen by `role.remote_agent`, not by composing `A2AToolProvider` into
the worker's tools (which would only surface remote *tools* to a local worker).
This matches the "specialist agent owns the sub-order" intent and keeps the
swarm topology intact. **Rejected:** `A2AAgentNode.as_node` replacing the
`skynet_worker` node — would make *all* workers remote, not per-role.

### DD-SP-006: Callable injection preserved and extended
`role_resolver` (registry) and `send_fn` (remote) are injectable, so specialist
selection and remote delegation are both testable with pure fakes — no LLM, no
network. Mirrors Phase S's `plan_fn`/`worker_fn`/`evaluate_fn`.

### DD-SP-007: Additive value objects, opt-in flags, snapshot-guarded
New `WorkerResult`/`SwarmResult` fields default to empty `Usage`/`False`; the
three flags default off; a snapshot test proves zero graph drift (RF-SP-09).

### DD-SP-008: Remote content is untrusted → L1-sanitize + audit
Every remote worker output crosses the A2A trust boundary and is
`InputSanitizer`-sanitized before touching `AgentState`, audited hash-first, and
allowlist-gated (deny-all in strict mode). Mirrors `A2AAgentNode.ainvoke`.

## 6. Relationship to existing supervisors

`network_supervisor._find_node(capability)` is the precedent for capability→remote
routing, but at *whole-task* granularity over the legacy HTTP node map. S+3 is
finer (one sub-order) and A2A-native. `domain_supervisor` is domain- not
capability-keyed. S+ replaces neither.

## 7. Security

- Role personas are **trusted** config (operator-authored, like tool policies) —
  not sanitized, but never user-derived. Order instructions remain user-derived →
  `SecurePromptBuilder`.
- Remote delegation: allowlist (fnmatch) + strict deny-all + per-call timeout
  (Phase I `A2AConnectionManager`/`A2AClient`); output L1-sanitized + audited.
- `ActionInterceptor.check()` still gates every local worker tool action
  (Phase-S invariant, unchanged).
- Budget hard cap (`SkynetBudgetExceeded`) is a first-class abort, now truthful.

## 8. Observability

### 8.1 OTel spans
`skynet.worker` (+attrs `role`, `remote`, `worker_tokens`), new `skynet.remote`
(attrs `agent`, `success`), existing `skynet.plan`/`skynet.reduce`/`skynet.evaluate`.

### 8.2 Metrics
```
prismal.skynet_role_assignments_total{role}
prismal.skynet_worker_tokens_total
prismal.skynet_remote_calls_total
prismal.skynet_remote_failures_total
prismal.budget_cutoffs_total          (reused on a swarm hard breach)
```

## 9. Testing Strategy (summary; detail in `TASKS.md`)

- **Roles**: `from_yaml` loads + malformed → `SkynetRoleError`; `resolve` fallback.
- **Specialist planner/worker**: 2 roles → 2 models + 2 personas (fake provider);
  role `"worker"` path byte-for-byte Phase S.
- **Metering**: shared meter sums planner+evaluator+reducer+Σworkers; `SwarmResult.usage`
  correct; budget cutoff (soft degrade / hard raise) proven with fake token counts.
- **Remote**: injected `send_fn` spy round-trips one order; sanitized + audited;
  failure contained (`success=False`, swarm still reduces).
- **Snapshot**: compiled graph + subgraph unchanged with all S+ flags off.
- **AST guard**: `agents/skynet/**` still imports no `prismal.mcp`/`skills`.

## 10. Rollout

1. Land `roles.py` + value-object extensions + settings/exceptions (pure).
2. Land the shared-meter threading + role-aware worker/planner (off by default).
3. Land `remote.py` + the remote worker path (behind `skynet_remote_workers_enabled`).
4. Wire the builder (one meter, registry, send_fn); snapshot-guard the off path.
5. Docs (`docs/skynet.md` specialist/metered/remote sections) + example.
