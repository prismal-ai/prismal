# Prismal Cost & Budget Governance — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-12 |
| **PLAN** | `specs/cost-budget-governance/PLAN.md` |
| **SPEC** | `specs/cost-budget-governance/SPEC.md` |
| **TASKS** | `specs/cost-budget-governance/TASKS.md` |

---

## 1. Context

Prismal's advanced patterns multiply LLM calls (debate `N×M`, ToT `depth×beam×breadth`,
LATS `max_simulations×2`, MoA `proposers+layers`, plus Skynet swarms). Today
`react_loop` caps *iterations* and the parallel dispatcher caps *workers*, but there
is **no aggregate ceiling on tokens / cost / calls** for a turn, session, or tenant,
and **token usage is never extracted from a response** — `ProviderRegistry.track_usage`
and `monitoring/cost_tracker.py` exist but nothing on the hot path feeds them. Two
budget hooks are already declared yet dormant: `settings.skynet_token_budget` (never
enforced) and `SkynetBudgetExceeded` (never raised).

This phase adds an **enforcement** layer (`prismal/budget/`) on top of the existing
**observation** layer (`prismal/monitoring/`): meter real usage per run, compare it
to a `Budget`, and cut off (soft = degrade, hard = abort) when exceeded — finally
wiring the dormant Skynet budget into one engine.

## 2. Design principles

1. **Observe vs enforce split.** `monitoring/` keeps recording history (SQLite);
   `budget/` owns in-memory, hot-path enforcement. `CostMeter` may bridge to
   `CostTracker` for persistence, but enforcement never depends on I/O.
2. **Opt-in, zero-overhead-off.** `budget_enabled=False` ⇒ no seeding, `None` guards
   everywhere, graph snapshot byte-for-byte unchanged.
3. **Provider isolation preserved.** Only `providers/cost.py` imports `litellm`.
   Token extraction reads the LangChain-standard `usage_metadata`, which is not a
   provider SDK type, so it lives in `budget/usage.py`.
4. **Reuse, do not reimplement.** Cost history → `CostTracker`; OTel → `OTelManager`;
   audit → `AuditLogger`; pricing → LiteLLM first. New code is the *engine*, not new
   plumbing.
5. **One budget engine.** Skynet's dormant budget becomes a `Budget` consumed by the
   same `CostMeter`/`BudgetGuard`; `SkynetBudgetExceeded` becomes a `BudgetExceeded`.

## 3. Data flow (one metered turn)

```
supervisor entry (budget_enabled)
   └─ seed_budget_run(state, settings, org_id)
        state["metadata"]["budget"] = {meter: CostMeter, guard: BudgetGuard}

agent node → react_loop(..., budget_guard=guard)
   for each iteration:
        status = guard.check()                    # pre-call, O(1)
        if status.hard_exceeded:                   # cut off
            audit("budget_cutoff", action=abort); append "[budget exhausted]"; break
        response = await llm.ainvoke(...)
        guard.meter.record_response(response, model, agent=name)
             extract_token_usage(response) -> TokenCounts
             compute_cost_usd(model, in, out, settings) -> CostEstimate
             usage += Usage(...); OTel++; (CostTracker.record? )

expensive pattern (debate/ToT/LATS/MoA/reflection)
   guard_fn = make_budget_guard_fn(guard)
   before each round/branch/simulation:
        if not await guard_fn(ctx): degrade-or-stop   # soft: shrink; hard: raise
```

## 4. Component boundaries

| Unit | Does | Depends on | Tested with |
|---|---|---|---|
| `types.py` | immutable Budget/Usage/Status math | — | pure unit |
| `usage.py` | message → TokenCounts | langchain message shapes | fake messages |
| `providers/cost.py` | tokens → USD | litellm, settings table | monkeypatched litellm |
| `meter.py` | accumulate + attribute + persist | usage, cost, OTel, CostTracker | fakes |
| `guard.py` | compare + degrade/abort + audit | meter, AuditLogger | unit |
| `resolve.py` | settings/state ↔ engine | settings, composition org overrides | unit |
| integrations | call the engine at choke points | the above | behaviour tests |

Each unit is understandable and testable in isolation; integrations only *call* the
engine, so the engine is verified without LangGraph.

## 5. Soft vs hard semantics

For each *limited* dimension `d` with `limit_d > 0` and current `used_d`:
- `ratio_d = used_d / limit_d`.
- **hard** when any `used_d >= limit_d`.
- **soft** when not hard and any `ratio_d >= soft_ratio`.
- `breached_dimension` is the dimension with the largest ratio among the tripping set.

Hard handling differs by site (graceful where a partial answer exists, exception
where it does not):
- `react_loop`: break with a best-effort partial `AIMessage` (a turn never crashes).
- patterns: stop expanding, return current best; the guard-fn raises only when
  `hard_cap` and there is no partial to hand back (pre-first-generation).
- Skynet: raise `SkynetBudgetExceeded` (the supervisor records the partial swarm
  result before propagating).

`budget_hard_cap=False` downgrades every hard cap to a soft degrade (warn + best
effort, never raise) — for operators who want metering + alerts without aborts.

## 6. Why these seams

- `react_loop` is the single shared ReAct choke point for the 26 text agents — one
  edit meters them all. The `budget_guard` param defaults `None`, so non-budget
  callers are unaffected; the node-factory that builds react calls passes the
  per-run guard from `state["metadata"]["budget"]`.
- Patterns already take callable injection (`generate_fn`, `evaluate_fn`, …); adding
  one more optional callable (`budget_guard_fn`) matches the established factory
  pattern and keeps the patterns LLM-agnostic and testable.
- Skynet already centralises its LLM calls in the supervisor/worker, so a single
  shared meter there enforces the whole swarm.

## 7. Multi-tenant (basic, Phase R hook)

`resolve_budget(settings, org_id=...)` is the seam where per-tenant ceilings apply.
With Phase R's `apply_org_overrides(settings, org_id, overrides, source=...)`, a host
builds tenant settings and the budget follows. Collections/attribution already isolate
by `org_id` (`collection_for`); the meter tags usage with `tenant=org_id`. Full quota
enforcement across concurrent tenants (shared counters in a store) is deferred — this
phase delivers per-run, per-tenant *resolution* and attribution.

## 8. Backward compatibility

- `budget_enabled` default `False`; no seeding ⇒ `get_budget_guard()` returns `None`
  ⇒ every integration takes its existing path.
- `react_loop` keeps its signature except a trailing optional `budget_guard=None`.
- Pattern signatures gain a trailing optional `budget_guard_fn=None`.
- `SkynetBudgetExceeded` keeps its name and its `SkynetError` ancestry (added
  `BudgetExceeded` is an *additional* base); existing `except SkynetError` is intact.
- A graph snapshot test asserts the compiled supervisor is identical with the flag off.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Imprecise cost | LiteLLM native first; `estimated` flag marks table fallback; history in CostTracker |
| Hard cap cuts useful output | Soft cap degrades first; hard cap returns audited best effort, not a crash, in react_loop/patterns |
| Metering overhead | O(1) accumulation, no I/O on hot path; OTel/persistence best-effort & suppressed |
| Patterns ignore the guard | `make_budget_guard_fn` + per-pattern enforcement tests assert degrade/abort |
| Double-counting | Single per-run `CostMeter` in `state["metadata"]["budget"]`; patterns share the run guard |
