# Prismal Cost & Budget Governance — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-12 |
| **PLAN** | `specs/cost-budget-governance/PLAN.md` |
| **SPEC** | `specs/cost-budget-governance/SPEC.md` |
| **Architecture** | `specs/cost-budget-governance/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Delivered in seven phases (C1–C7), each independently testable and landing behind
`settings.budget_enabled` (default `False`) so `main`/`develop` stay green and the
26 agents are unaffected until the wiring phases. Built **test-first (TDD)**: a
failing test precedes each unit. Every component uses callable injection or pure
value objects, so all unit tests run without an LLM backend. The layer reuses
`CostTracker`, `OTelManager`, `AuditLogger`, and LiteLLM pricing — it adds the
enforcement *engine*, not new plumbing.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Branch `feature/cost-budget-governance`.
- Reuse, do not modify: `monitoring/cost_tracker.py`, `monitoring/otel.py`,
  `security/audit.py`, `providers/registry.py` (only add `providers/cost.py`),
  `composition/config_sources.py` (`apply_org_overrides`).
- Provider isolation: `litellm` only in `providers/cost.py`.

## 3. Implementation Phases

### PHASE C1 — Value objects + Exceptions + Settings

| ID | Task | Dependency | Status |
|---|---|---|---|
| C1-01 | `budget/types.py`: `BudgetScope`, `Budget`, `TokenCounts`, `Usage`, `BudgetStatus`, `Degradation` (SPEC-CST-TYP-001) | — | DONE |
| C1-02 | `core/exceptions.py`: `BudgetExceeded`; re-parent `SkynetBudgetExceeded(BudgetExceeded, SkynetError)` (SPEC-CST-ERR-001) | — | DONE |
| C1-03 | `core/config.py`: `budget_*` settings (SPEC-CST-CFG-001) | — | DONE |
| C1-04 | `_validate_budget` (clamp `soft_ratio∈[0,1]`, reject bad `scope`) | C1-03 | DONE |

**Done when:** value objects round-trip + `Budget(0,…).is_unlimited`; `Usage.__add__`
correct; `BudgetExceeded` carries `dimension/used/limit/scope`; `SkynetBudgetExceeded`
is both a `BudgetExceeded` and a `SkynetError`; settings parse from `PRISMAL_BUDGET_*`.

### PHASE C2 — Cost computation + token extraction

| ID | Task | Dependency | Status |
|---|---|---|---|
| C2-01 | `budget/usage.py`: `extract_token_usage()` (usage_metadata → response_metadata → zeros) (SPEC-CST-USG-001) | C1 | DONE |
| C2-02 | `providers/cost.py`: `CostEstimate` + `compute_cost_usd()` (litellm → table → none) (SPEC-CST-COST-001) | C1 | DONE |

**Done when:** extraction handles all three message shapes; cost prefers litellm,
falls back to `settings.budget_pricing`, flags `estimated`; neither raises.

### PHASE C3 — CostMeter

| ID | Task | Dependency | Status |
|---|---|---|---|
| C3-01 | `budget/meter.py`: `CostMeter.record()` + `usage` snapshot + OTel (SPEC-CST-MET-001) | C2 | DONE |
| C3-02 | `CostMeter.record_response()` (extract+cost+record) | C3-01 | DONE |
| C3-03 | Optional `CostTracker` bridge + attribution labels | C3-01 | DONE |

**Done when:** N records accumulate O(1); `record_response` meters a fake message;
bridge persists when a tracker is injected; OTel counters increment.

### PHASE C4 — BudgetGuard

| ID | Task | Dependency | Status |
|---|---|---|---|
| C4-01 | `budget/guard.py`: `check()` (within/soft/hard per dimension) (SPEC-CST-GRD-001) | C3 | DONE |
| C4-02 | `enforce()` (audit + raise on hard, warn on soft) + `degradation()` | C4-01 | DONE |
| C4-03 | `make_budget_guard_fn()` adapter (None → always-True) | C4-01 | DONE |

**Done when:** each dimension trips soft at `soft_ratio` and hard at the limit;
unlimited never trips; `enforce` raises `BudgetExceeded` only on hard + `hard_cap`;
cutoffs audited hash-first; the adapter returns the right bool / raises.

### PHASE C5 — Resolution + OTel + seeding

| ID | Task | Dependency | Status |
|---|---|---|---|
| C5-01 | `budget/resolve.py`: `resolve_budget()`, `seed_budget_run()`, `get_budget_guard()` (SPEC-CST-RES-001) | C4 | DONE |
| C5-02 | `monitoring/otel.py`: budget counters + histogram (SPEC-CST-OTEL-001) | — | DONE |
| C5-03 | `budget/__init__.py` thin re-exports | C5-01 | DONE |

**Done when:** seeding is a no-op when disabled and installs meter+guard when enabled;
`get_budget_guard` returns it / `None`; new metrics registered.

### PHASE C6 — Integration: react_loop + patterns

| ID | Task | Dependency | Status |
|---|---|---|---|
| C6-01 | `react_loop(..., budget_guard=None)`: record after, check before, hard→partial+break (SPEC-CST-INT-001) | C5 | DONE |
| C6-02 | Wire the per-run guard from `state["metadata"]["budget"]` at the node seam | C6-01 | DONE |
| C6-03 | `debate_round` honours `budget_guard_fn` | C5 | DONE |
| C6-04 | `tree_of_thoughts` honours `budget_guard_fn` | C5 | DONE |
| C6-05 | `LATSAgent.search` honours `budget_guard_fn` | C5 | DONE |
| C6-06 | `MixtureOfAgents.generate` honours `budget_guard_fn` | C5 | DONE |
| C6-07 | `reflection_loop` honours `budget_guard_fn` | C5 | DONE |

**Done when:** with a guard, `react_loop` meters every call and a hard cap yields a
partial answer with no further calls; each pattern stops expanding on a soft/hard
cap; with `None`/disabled, behaviour is unchanged.

### PHASE C7 — Skynet unification + snapshot + docs

| ID | Task | Dependency | Status |
|---|---|---|---|
| C7-01 | Skynet supervisor/worker build `Budget(max_tokens=skynet_token_budget)` + shared `CostMeter`; raise `SkynetBudgetExceeded` on breach | C5 | DONE |
| C7-02 | Graph snapshot test: compiled supervisor byte-for-byte identical with `budget_enabled=False` | C6 | DONE |
| C7-03 | `docs/budget.md` user guide + `examples/budget_governance.py` | C6 | DONE |
| C7-04 | Update `specs/roadmap.md` (move Cost & Budget Governance to ✅ Implemented) | C7-03 | DONE |

**Done when:** the dormant `skynet_token_budget` is enforced; the off-path snapshot is
identical; docs + example run; roadmap updated.

## 4. Test Inventory (TDD)

| Test module | Covers |
|---|---|
| `tests/unit/budget/test_types.py` | C1-01 |
| `tests/unit/core/test_exceptions_budget.py` | C1-02 |
| `tests/unit/core/test_config_budget.py` | C1-03/04 |
| `tests/unit/budget/test_usage.py` | C2-01 |
| `tests/unit/providers/test_cost.py` | C2-02 |
| `tests/unit/budget/test_meter.py` | C3 |
| `tests/unit/budget/test_guard.py` | C4 |
| `tests/unit/budget/test_resolve.py` | C5-01 |
| `tests/unit/budget/test_react_loop_budget.py` | C6-01/02 |
| `tests/unit/budget/test_patterns_budget.py` | C6-03..07 |
| `tests/unit/budget/test_skynet_budget.py` | C7-01 |
| `tests/unit/agents/test_graph_snapshot_budget.py` | C7-02 |

## 5. Definition of Done

- `uv run pytest -m "not live_api"` green; coverage ≥ 80% on `prismal/budget/**`.
- `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy prismal` clean.
- `uv run bandit -r prismal -c pyproject.toml` clean.
- Graph snapshot identical with the flag off.
- All four SDD docs `IMPLEMENTED`; roadmap updated.