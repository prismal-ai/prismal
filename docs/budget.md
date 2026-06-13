# Cost & Budget Governance

Prismal's advanced patterns are **expensive by design** — debate (`N×M` calls),
Tree-of-Thoughts, LATS (MCTS), Mixture-of-Agents, parallel dispatch, and Skynet
swarms all multiply LLM calls. The **budget** layer (`prismal/budget/`) meters
real usage per run and **cuts off** when a ceiling is reached: a *soft cap*
degrades (fewer rounds/branches), a *hard cap* aborts with a best-effort partial
answer. It is the enforcement counterpart to the observation layer in
`prismal/monitoring/`.

> Specs: [`specs/cost-budget-governance/`](../specs/cost-budget-governance/) ·
> Example: [`examples/budget_governance.py`](../examples/budget_governance.py)

The whole layer is **opt-in**. With `budget_enabled=False` (the default) the
compiled graph and every agent behave exactly as before — byte-for-byte.

## Quick start

```bash
export PRISMAL_BUDGET_ENABLED=true
export PRISMAL_BUDGET_MAX_TOKENS=50000     # 0 = unlimited
export PRISMAL_BUDGET_MAX_COST_USD=2.50    # 0 = unlimited
export PRISMAL_BUDGET_MAX_CALLS=40         # 0 = unlimited
```

Once enabled, `supervisor_node` seeds a per-run meter+guard each turn; every
`react_loop` LLM call is metered, and a hard cap before a call returns a
best-effort partial answer instead of calling the model again.

## Concepts

| Piece | Role |
|---|---|
| `Budget` | A spend ceiling for one `BudgetScope` (turn/session/tenant). `0` on any dimension = unlimited. |
| `Usage` | Cumulative `prompt_tokens` / `completion_tokens` / `cost_usd` / `calls` / `wall_clock_s`, summable with `+`. |
| `CostMeter` | O(1) per-run accumulator. `record_response(msg, model)` extracts tokens, prices them, accumulates, emits OTel, optionally persists to `CostTracker`. |
| `BudgetGuard` | Compares the meter to the budget — `check()` (within/soft/hard), `enforce()` (audits + raises on hard cap), `degradation()` (advice for patterns). |
| `BudgetExceeded` | Raised on a hard cap; carries `dimension` / `used` / `limit` / `scope`. |

### Soft vs hard caps

For each *limited* dimension `d` (limit > 0) with usage `used_d`:

- **hard** when `used_d >= limit_d` → abort (or, with `budget_hard_cap=False`,
  degrade only).
- **soft** when `used_d / limit_d >= budget_soft_ratio` (default `0.8`) →
  degrade: patterns reduce rounds/branches; `react_loop` warns and proceeds.

Hard handling is graceful where a partial answer exists (`react_loop` returns a
best-effort `AIMessage`; patterns return their current best) and an exception
where it does not.

## Cost estimation

`providers/cost.py::compute_cost_usd` is the **only** module that imports
`litellm`. It prices a call from LiteLLM's native model map first; for models
LiteLLM cannot price it falls back to a configurable table and flags the figure
as estimated:

```bash
# {"model": {"input": usd_per_1k, "output": usd_per_1k}}
export PRISMAL_BUDGET_PRICING='{"acme/model": {"input": 1.0, "output": 2.0}}'
```

## Settings

| Env var | Default | Meaning |
|---|---|---|
| `PRISMAL_BUDGET_ENABLED` | `false` | Master opt-in. |
| `PRISMAL_BUDGET_MAX_TOKENS` | `0` | Per-run token ceiling (0 = unlimited). |
| `PRISMAL_BUDGET_MAX_COST_USD` | `0.0` | Per-run USD ceiling. |
| `PRISMAL_BUDGET_MAX_CALLS` | `0` | Per-run LLM-call ceiling. |
| `PRISMAL_BUDGET_MAX_WALL_CLOCK_S` | `0.0` | Per-run wall-clock ceiling. |
| `PRISMAL_BUDGET_SCOPE` | `turn` | `turn` \| `session` \| `tenant`. |
| `PRISMAL_BUDGET_SOFT_RATIO` | `0.8` | Soft-cap fraction. |
| `PRISMAL_BUDGET_HARD_CAP` | `true` | Abort on hard cap vs soft-only metering. |
| `PRISMAL_BUDGET_PRICING` | `{}` | Fallback per-model pricing table (JSON). |

## Using the engine directly

```python
from prismal.budget import Budget, CostMeter, BudgetGuard, make_budget_guard_fn

meter = CostMeter()
guard = BudgetGuard(Budget(max_tokens=10_000, max_cost_usd=1.0), meter)

# After each LLM response:
meter.record_response(response, "gpt-4o", agent="researcher")

# Before each expensive step:
status = guard.check()
if status.hard_exceeded:
    ...  # stop, return best effort

# Or hand patterns the callable they accept:
guard_fn = make_budget_guard_fn(guard)
result = await debate_round("q", state, budget_guard_fn=guard_fn)
```

## Expensive patterns

Each accepts an optional `budget_guard_fn` checked before every expansion unit;
a soft cap stops deepening and returns best effort, a hard cap raises:

| Pattern | Checked before each |
|---|---|
| `reflection_loop` | refinement iteration |
| `tree_of_thoughts` | depth level (beam/bfs/dfs) |
| `LATSAgent.search` | MCTS simulation (≥1 runs first) |
| `debate_round` | additional round |
| `MixtureOfAgents.generate` | aggregator layer |

## Skynet unification

The dormant `skynet_token_budget` is now enforced through the same engine:
`SkynetSupervisor` meters its planner/evaluator calls into a shared `CostMeter`
and raises `SkynetBudgetExceeded` (now a `BudgetExceeded`) at the `evaluate()`
round boundary when the token budget is exceeded.

## Attribution & observability

`CostMeter` tags OTel counters (`prismal.budget_tokens_total`,
`prismal.budget_cost_usd_total`) with `agent` / `pattern` / `model` / `tenant`,
emits `prismal.budget_cutoffs_total{dimension,action}` on cutoffs, and records
`prismal.cost_per_call_usd`. Cutoffs are audited hash-first
(`AuditLogger.log_event("budget_cutoff", …)`) — dimensions and counts only,
never user content. When a `CostTracker` is injected, each call is also
persisted to SQLite for FinOps history.

## Multi-tenant

`resolve_budget(settings, org_id=...)` is the per-tenant seam: a host threads
tenant ceilings via the composition root's `apply_org_overrides`, and the meter
tags usage with `tenant=org_id`. Shared cross-tenant quota counters are a
follow-up; this phase delivers per-run, per-tenant resolution and attribution.

## Notes

- Metering is O(1) and never blocks the hot path; OTel/persistence are
  best-effort and suppressed on error.
- The per-run engine lives in an in-process registry keyed by `session_id`,
  never in checkpointed state — a live `CostMeter` never reaches the
  checkpoint serializer.
```
