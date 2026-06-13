# Prismal Cost & Budget Governance — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-12 |
| **PLAN** | `specs/cost-budget-governance/PLAN.md` |
| **Architecture** | `specs/cost-budget-governance/ARCHITECTURE.md` |
| **TASKS** | `specs/cost-budget-governance/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Frozen dataclasses for value objects; constructors accept `settings: Settings | None = None`.
- **No provider SDK imports outside `prismal/providers/`** — `litellm.completion_cost()`
  lives only in `providers/cost.py` (Critical Rule #4).
- The whole layer is **opt-in**: `settings.budget_enabled` (default `False`). With the
  flag off the compiled supervisor graph is byte-for-byte unchanged and every existing
  agent behaves identically (snapshot-tested).
- `0 = unlimited` for every budget dimension (mirrors the existing
  `skynet_token_budget` convention).
- All runtime state lives under `state["metadata"]["budget"]` (mirrors
  `state["metadata"]["skynet"]` / `["kokoro"]`).
- Hot path is O(1) per call — no I/O while metering.
- Audit is hash-first: cutoffs record dimension/used/limit/action/scope — never user content.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/budget/types.py` | `BudgetScope`, `Budget`, `Usage`, `TokenCounts`, `BudgetStatus`, `Degradation` |
| `prismal/budget/usage.py` | `extract_token_usage()` — pull token counts off an LLM message |
| `prismal/budget/meter.py` | `CostMeter` — per-run accumulator + attribution + OTel + optional `CostTracker` bridge |
| `prismal/budget/guard.py` | `BudgetGuard` (`check`/`enforce`/`degradation`) + `make_budget_guard_fn` |
| `prismal/budget/resolve.py` | `resolve_budget()`, `seed_budget_run()`, `get_budget_guard()` |
| `prismal/providers/cost.py` | `compute_cost_usd()` — LiteLLM native + pricing-table fallback |
| `prismal/core/exceptions.py` | `BudgetExceeded`; re-parent `SkynetBudgetExceeded` |
| `prismal/core/config.py` | `budget_*` settings + `_validate_budget` |
| `prismal/monitoring/otel.py` | budget counters + histogram |

---

## SPEC-CST-TYP-001: Value objects (`budget/types.py`)

```python
class BudgetScope(str, Enum):
    TURN = "turn"
    SESSION = "session"
    TENANT = "tenant"


@dataclass(frozen=True)
class Budget:
    """A spend ceiling for one scope. 0 on a dimension = unlimited."""
    max_tokens: int = 0
    max_cost_usd: float = 0.0
    max_calls: int = 0
    max_wall_clock_s: float = 0.0
    scope: BudgetScope = BudgetScope.TURN

    @property
    def is_unlimited(self) -> bool: ...        # all dimensions 0


@dataclass(frozen=True)
class TokenCounts:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int: ...


@dataclass(frozen=True)
class Usage:
    """Cumulative usage. ``estimated`` is True if any cost came from the
    pricing-table fallback rather than a provider-authoritative figure."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    wall_clock_s: float = 0.0
    estimated: bool = False

    @property
    def total_tokens(self) -> int: ...

    def __add__(self, other: Usage) -> Usage: ...   # dimension-wise; estimated = OR


@dataclass(frozen=True)
class BudgetStatus:
    within: bool
    soft_exceeded: bool
    hard_exceeded: bool
    breached_dimension: str | None     # "tokens" | "cost" | "calls" | "wall_clock"
    usage: Usage
    budget: Budget


@dataclass(frozen=True)
class Degradation:
    """Advice a pattern consumes on a soft cap."""
    terminate: bool = False            # hard cap -> stop now, return best effort
    reduce: bool = False               # soft cap -> shrink rounds/branches
    reason: str = ""
```

**Rules**
- `Budget(0,0,0,0)` ⇒ `is_unlimited` ⇒ every check is `within`.
- `Usage.__add__` adds each numeric dimension and ORs `estimated`.

---

## SPEC-CST-USG-001: Token extraction (`budget/usage.py`)

```python
def extract_token_usage(message: object) -> TokenCounts:
    """Read prompt/completion tokens off a LangChain message.

    Order of precedence:
      1. ``message.usage_metadata``  -> {"input_tokens","output_tokens"}
      2. ``message.response_metadata["token_usage"]`` ->
         {"prompt_tokens","completion_tokens"}
      3. fallback -> TokenCounts(0, 0)
    Never raises; unknown shapes yield zeros.
    """
```

Pure LangChain only — no provider SDK import.

---

## SPEC-CST-COST-001: Cost computation (`providers/cost.py`)

```python
@dataclass(frozen=True)
class CostEstimate:
    cost_usd: float
    source: str            # "litellm" | "table" | "none"

    @property
    def estimated(self) -> bool:      # True unless source == "litellm"
        return self.source != "litellm"


def compute_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    settings: Settings | None = None,
) -> CostEstimate:
    """USD cost for one call.

    1. Try ``litellm.cost_per_token(model, prompt_tokens, completion_tokens)``;
       on success -> source="litellm".
    2. Else consult ``settings.budget_pricing[model]`` =
       {"input": usd_per_1k, "output": usd_per_1k} -> source="table".
    3. Else -> CostEstimate(0.0, "none").
    Never raises.
    """
```

This is the **only** new module that imports `litellm`.

---

## SPEC-CST-MET-001: CostMeter (`budget/meter.py`)

```python
class CostMeter:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        cost_tracker: CostTracker | None = None,   # optional SQLite persistence
        session_id: str | None = None,
        tenant: str | None = None,
    ) -> None: ...

    @property
    def usage(self) -> Usage: ...                   # cumulative snapshot

    def record(
        self,
        usage: Usage,
        *,
        agent: str | None = None,
        pattern: str | None = None,
        model: str | None = None,
    ) -> Usage:
        """O(1) accumulate; emit OTel; optionally persist to CostTracker.
        Returns the new cumulative usage."""

    def record_response(
        self,
        message: object,
        model: str,
        *,
        wall_clock_s: float = 0.0,
        agent: str | None = None,
        pattern: str | None = None,
    ) -> Usage:
        """extract_token_usage + compute_cost_usd + record. The single call
        react_loop/patterns use after each LLM response."""
```

- Attribution: OTel counters tagged `{agent, pattern, model, tenant}`.
- Bridge: when `cost_tracker` is set, also `cost_tracker.record(session_id, model, tokens_in, tokens_out, cost_usd, user_id=tenant)`.

---

## SPEC-CST-GRD-001: BudgetGuard (`budget/guard.py`)

```python
class BudgetGuard:
    def __init__(
        self,
        budget: Budget,
        meter: CostMeter,
        *,
        soft_ratio: float = 0.8,
        hard_cap: bool = True,
        audit: AuditLogger | None = None,
    ) -> None: ...

    def check(self) -> BudgetStatus:
        """Compare meter.usage to budget. soft when any dimension's ratio
        >= soft_ratio; hard when any dimension >= its limit. Unlimited
        dimensions never trip."""

    def enforce(self) -> BudgetStatus:
        """check(); on hard_exceeded audit a 'budget_cutoff' (action=abort)
        and — when hard_cap — raise BudgetExceeded. On soft_exceeded audit
        (action=degrade) and warn. Returns the status."""

    def degradation(self) -> Degradation: ...   # derived from check()


def make_budget_guard_fn(
    guard: BudgetGuard | None,
) -> Callable[[dict[str, object]], Awaitable[bool]]:
    """Adapt a guard to the callable the patterns accept.
    Returns async fn(ctx)->bool: True = proceed, False = degrade/stop.
    Raises BudgetExceeded on hard cap when guard.hard_cap. A None guard
    yields a fn that always returns True (zero-overhead disabled path)."""
```

---

## SPEC-CST-RES-001: Resolution & seeding (`budget/resolve.py`)

```python
def resolve_budget(settings: Settings, *, org_id: str | None = None) -> Budget:
    """Build a Budget from settings (per-tenant via org_id when Phase R
    overrides are present)."""

def seed_budget_run(
    state: dict, settings: Settings, *, org_id: str | None = None,
    cost_tracker: CostTracker | None = None, audit: AuditLogger | None = None,
) -> None:
    """When settings.budget_enabled, set state['metadata']['budget'] =
    {'meter': CostMeter(...), 'guard': BudgetGuard(...)}. No-op otherwise."""

def get_budget_guard(state: dict) -> BudgetGuard | None:
    """Return the per-run guard from state, or None when disabled/unseeded."""
```

---

## SPEC-CST-ERR-001: Exceptions (`core/exceptions.py`)

```python
class BudgetExceeded(PrismalError):  # noqa: N818 — SPEC-CST-ERR-001 name
    """A run exceeded one budget dimension."""
    def __init__(self, dimension: str, used: float, limit: float,
                 scope: str = "turn") -> None:
        self.dimension = dimension
        self.used = used
        self.limit = limit
        self.scope = scope
        super().__init__(
            f"Budget exceeded on {dimension} ({scope}): {used} >= {limit}"
        )


class SkynetBudgetExceeded(BudgetExceeded, SkynetError):  # noqa: N818
    """Skynet run exceeded its skynet_token_budget. Now a BudgetExceeded so
    general handlers catch it, while SkynetError handlers keep working."""
    def __init__(self, used: float, limit: float) -> None:
        super().__init__("tokens", used, limit, scope="skynet")
```

---

## SPEC-CST-CFG-001: Settings (`core/config.py`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `budget_enabled` | `bool` | `False` | master opt-in |
| `budget_max_tokens` | `int` (ge 0) | `0` | 0 = unlimited |
| `budget_max_cost_usd` | `float` (ge 0) | `0.0` | 0 = unlimited |
| `budget_max_calls` | `int` (ge 0) | `0` | 0 = unlimited |
| `budget_max_wall_clock_s` | `float` (ge 0) | `0.0` | 0 = unlimited |
| `budget_scope` | `str` | `"turn"` | turn\|session\|tenant |
| `budget_soft_ratio` | `float` (ge 0, le 1) | `0.8` | soft-cap fraction |
| `budget_hard_cap` | `bool` | `True` | abort on hard cap vs soft-only |
| `budget_pricing` | `dict[str, dict[str, float]]` | `{}` | fallback `{model: {input, output}}` per-1k USD |

`@model_validator(mode="after") _validate_budget`: clamp `budget_soft_ratio` to
`[0,1]`; reject an unknown `budget_scope`.

---

## SPEC-CST-OTEL-001: Metrics (`monitoring/otel.py`)

| Metric | Kind | Labels |
|---|---|---|
| `prismal.budget_cost_usd_total` | counter | `agent,pattern,model,tenant` |
| `prismal.budget_tokens_total` | counter | `agent,pattern,model,tenant` |
| `prismal.budget_cutoffs_total` | counter | `dimension,action` |
| `prismal.cost_per_call_usd` | histogram | `model` |

---

## SPEC-CST-INT-001: Integration contracts

- `react_loop(..., budget_guard: BudgetGuard | None = None)`:
  after each LLM response → `budget_guard.meter.record_response(resp, model, agent=agent_name)`;
  before each LLM call → `status = budget_guard.check()`; on `hard_exceeded`
  audit + break the loop with a best-effort partial `AIMessage`
  (`"[budget exhausted]"`) and stop calling. `None` ⇒ unchanged behaviour.
- Patterns (`debate_round`, `tree_of_thoughts`, `LATSAgent.search`,
  `MixtureOfAgents.generate`, `reflection_loop`): optional
  `budget_guard_fn: Callable[[dict], Awaitable[bool]] | None = None`, checked
  before each round/branch/simulation/iteration. `False` ⇒ stop expanding and
  return best effort; hard cap raises inside the fn.
- Skynet: `SkynetSupervisor`/`SwarmWorker` build `Budget(max_tokens=skynet_token_budget)`
  and enforce via a shared `CostMeter`; on breach raise `SkynetBudgetExceeded`.
