# Prismal Adaptive Model Routing — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | AMR |
| **Target package version** | `3.16.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/adaptive-model-routing/PLAN.md` |
| **Architecture** | `specs/adaptive-model-routing/ARCHITECTURE.md` |
| **TASKS** | `specs/adaptive-model-routing/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`; frozen dataclasses for value objects; constructors accept `settings: Settings | None = None`.
- Policies are **sync, pure and must not raise** on the hot path: any internal failure returns the `StaticTierPolicy` choice for the agent (fail-open on cost) with `reason="fallback:<error class>"`. Security floors (SPEC-AMR-SEC-001) are fail-closed.
- No provider SDK imports outside `prismal/providers/`; `prismal/routing/batch.py` reaches provider batch APIs only through `prismal/providers/batch_adapters.py`.
- Routing state lives under `state["metadata"]["routing"]` (serializable: tier, model_ref, escalations, affinity key). Live objects (policy instances, caches) live in an in-process registry keyed by `session_id`, mirroring `budget/resolve.py`.
- Acceptance criteria use EARS. Priority: **MUST** unless marked **SHOULD**.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/routing/types.py` | `ModelTier`, `TaskFeatures`, `ModelChoice`, `RoutingOutcome`, `BatchItem`, `BatchAssignment` |
| `prismal/routing/ports.py` | `RoutingPolicyPort`, `SuccessPredictorPort` |
| `prismal/routing/policies/{static,budget_aware,cascade,batch_assignment,composite}.py` | `StaticTierPolicy` + `load_tier_models`, `BudgetAwarePolicy`, `CascadePolicy` + `AdequacyChecker`, `BatchAssignmentPolicy` + `solve_dual`/`solve_greedy`, `CompositePolicy` |
| `prismal/routing/predictor.py` | `TableSuccessPredictor`, `load_predictor_table` |
| `prismal/routing/{features,select}.py` | `extract_features`; `select_model`, `record_outcome`, per-run seeding trio |
| `prismal/routing/{plan_cache,response_cache}.py` | `PlanCacheKey`, `PlanCache`, `PlanCacheBackend`; `ResponseCache` |
| `prismal/routing/batch.py` | `BatchExecutor`, `BatchJob`, `BatchResult` |
| `prismal/providers/batch_adapters.py` | `BatchApiPort` + Anthropic / OpenAI / OpenRouter adapters |
| `prismal/providers/registry.py` | *(extend)* `get_llm(..., features=None)` |
| `prismal/core/config.py` / `core/exceptions.py` / `monitoring/otel.py` | settings, `RoutingError` hierarchy, instruments |

---

## SPEC-AMR-TYP-001: Value objects (`routing/types.py`)

```python
class ModelTier(StrEnum):
    NANO = "nano"; SMALL = "small"; MEDIUM = "medium"; FRONTIER = "frontier"; AUTO = "auto"
TIER_ORDER: tuple[ModelTier, ...] = (ModelTier.NANO, ModelTier.SMALL, ModelTier.MEDIUM, ModelTier.FRONTIER)
from prismal.decisions.types import RiskTier   # DEC `str` Enum: low|medium|high|critical (string-comparable)
@dataclass(frozen=True)
class TaskFeatures:
    agent: str
    intent: str | None               # match_intent() / DEC-ROUTE label; None = unknown
    est_input_tokens: int
    needs_tools: bool
    needs_structured_output: bool
    risk_tier: RiskTier = RiskTier.LOW
    language: str = "en"             # BCP-47 primary subtag
    tainted: bool = False            # Phase H TaintRegistry flag for the current turn
@dataclass(frozen=True)
class ModelChoice:
    model_ref: str                   # LiteLLM string or "gw:<gateway>/<model>"
    tier: ModelTier
    reason: str                      # "static" | "budget_soft_cap" | "cascade_start" | "escalation:<cause>" | "batch" | "fallback:<err>"
    escalation_of: ModelChoice | None = None
    policy: str = "static"
@dataclass(frozen=True)
class RoutingOutcome:
    success: bool
    structured_valid: bool | None = None
    adequacy: float | None = None    # DecisionPort Noul P(true), when computed
    tool_errors: int = 0
    usage: Usage | None = None       # TKE1 Usage incl. cache fields
    served_model: str | None = None  # gateway-reported model, may differ from model_ref
```

**Acceptance:** THE SYSTEM SHALL compare tiers only through `TIER_ORDER`; `ModelTier.AUTO` SHALL NOT appear in `TIER_ORDER` and SHALL be treated as terminal (no up/downgrade).

## SPEC-AMR-TYP-002: Ports (`routing/ports.py`)

```python
@runtime_checkable
class RoutingPolicyPort(Protocol):
    name: str
    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice: ...
    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None:
        """Return an escalated ModelChoice to retry with, or None to accept. Must not raise."""
@runtime_checkable
class SuccessPredictorPort(Protocol):
    version: str
    def p_success(self, features: TaskFeatures, model_ref: str) -> float: ...          # in [0, 1]
    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int: ...
    def update(self, features: TaskFeatures, model_ref: str, success: bool) -> None: ...
```

**Acceptance:** WHEN any policy's `select()` raises internally, THE SYSTEM SHALL return the `StaticTierPolicy` choice with `reason` starting `"fallback:"` and increment `prismal.routing_decisions_total{policy="fallback"}`.

## SPEC-AMR-POL-001: `StaticTierPolicy` (`routing/policies/static.py`)

```python
TierModels = Mapping[ModelTier, str]                       # tier -> model_ref
AgentTiers = Mapping[str, ModelTier]                       # agent -> default tier
class StaticTierPolicy:
    name = "static"
    def __init__(self, *, settings: Settings | None = None,
                 tier_models: TierModels | None = None, agent_tiers: AgentTiers | None = None) -> None: ...
    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice: ...
    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> None: ...
    def model_for(self, tier: ModelTier) -> str: ...
def load_tier_models(path: str | None = None) -> tuple[TierModels, AgentTiers, Mapping[RiskTier, ModelTier]]:
    """Load config/routing_tiers.yaml: {tiers: {nano: ..., small: ...}, agents: {coder: medium, ...},
    risk_floor: {high: medium, critical: frontier}}. Invalid shape raises RoutingConfigError at load."""
```

- Unknown agent ⇒ tier derived from the agent's legacy setting (`skynet_worker_model`, `kokoro_judge_model`, …) when it equals a configured tier model, else `ModelTier.MEDIUM`.
- **Acceptance:** WHEN `routing_policy="static"` and `routing_tier_models` maps every agent to the model it uses in 3.15.0, THE SYSTEM SHALL produce identical `ChatLiteLLM.model` values for all 26 agents (parity test).

## SPEC-AMR-POL-002: `BudgetAwarePolicy` (`routing/policies/budget_aware.py`)

```python
class BudgetAwarePolicy:
    name = "budget_aware"
    def __init__(self, inner: RoutingPolicyPort, *, settings: Settings | None = None,
                 floor: Mapping[RiskTier, ModelTier] | None = None) -> None: ...
    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice: ...
    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None: ...
```

- WHEN `budget.soft_exceeded` is True and `budget.hard_exceeded` is False, THE SYSTEM SHALL return the inner choice downgraded by exactly one tier in `TIER_ORDER`, never below the risk floor, with `reason="budget_soft_cap"`.
- WHEN `budget.hard_exceeded` is True, THE SYSTEM SHALL return the inner choice unchanged (the existing `BudgetGuard.enforce()` aborts the run; AMR never masks a hard cap).
- WHEN `budget` is None, THE SYSTEM SHALL delegate unchanged.

## SPEC-AMR-POL-003: `CompositePolicy` (`routing/policies/composite.py`)

```python
class CompositePolicy:
    name = "composite"
    def __init__(self, chain: Sequence[RoutingPolicyPort], *, settings: Settings | None = None) -> None: ...
    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice: ...
    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None: ...
```

- Default composite for `routing_policy="composite"`: `BudgetAwarePolicy(CascadePolicy(StaticTierPolicy()))`. `on_outcome` is offered to each element innermost-first; the first non-`None` escalation wins.

## SPEC-AMR-CAS-001: `CascadePolicy` (`routing/policies/cascade.py`)

```python
EscalationCause = Literal["structured_invalid", "inadequate", "tool_errors"]
class AdequacyChecker:
    def __init__(self, decision_port: DecisionPort | None, *, tau: float, timeout_ms: int) -> None: ...
    async def check(self, request_summary: str, answer: str, *, risk_tier: RiskTier) -> float | None:
        """DecisionPort Noul 'Is this answer adequate for the request?' -> P(true), or None when
        the port is absent/abstains/times out. Both strings enter the question via SecurePromptBuilder."""
class CascadePolicy:
    name = "cascade"
    def __init__(self, inner: RoutingPolicyPort, *, settings: Settings | None = None,
                 adequacy: AdequacyChecker | None = None) -> None: ...
    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice: ...
    def on_outcome(self, choice: ModelChoice, outcome: RoutingOutcome) -> ModelChoice | None: ...
```

- `select` starts at the cheapest tier allowed by `routing_cascade_start_tier` (default `small`), the risk floor and capability requirements (`needs_tools`, `needs_structured_output` per `routing_tier_capabilities`).
- WHEN `outcome.structured_valid is False`, THE SYSTEM SHALL escalate one tier with cause `structured_invalid`.
- WHEN `outcome.adequacy` is not None and `< routing_adequacy_tau` (default 0.7), THE SYSTEM SHALL escalate with cause `inadequate`.
- WHEN `outcome.tool_errors >= routing_cascade_tool_error_threshold` (default 2), THE SYSTEM SHALL escalate with cause `tool_errors`.
- WHEN the adequacy check returns None AND `features.risk_tier in ("high", "critical")`, THE SYSTEM SHALL escalate (abstention never silently accepts a high-risk cheap answer); for low/medium risk it SHALL accept.
- IF the escalation count would exceed `routing_cascade_max_escalations` (default 2) or the tier is `FRONTIER`/`AUTO`, THEN THE SYSTEM SHALL return None (accept) and record `prismal.routing_escalations_total{reason="cap"}`.

## SPEC-AMR-CAS-002: Cache affinity

- THE SYSTEM SHALL key affinity by `(session_id, agent)` in the per-run registry and persist only `state["metadata"]["routing"]["affinity"][agent] = {"tier": ..., "model_ref": ...}`.
- WHEN a thread has escalated for an agent, THE SYSTEM SHALL start subsequent turns of that agent in that thread at the escalated tier (no re-descent), so the provider prompt cache prefix is reused.
- WHILE `routing_cache_affinity_enabled` is True (default), THE SYSTEM SHALL NOT change the model for an `(session_id, agent)` pair within a turn except by escalation.

## SPEC-AMR-PRD-001: Success predictor (`routing/predictor.py`)

```python
@dataclass(frozen=True)
class PredictorCell:
    successes: int
    trials: int
    median_output_tokens: int
class TableSuccessPredictor:
    version: str
    def __init__(self, table: Mapping[tuple[str, ModelTier], PredictorCell], *,
                 tier_of: Callable[[str], ModelTier], prior_a: float = 1.0, prior_b: float = 1.0,
                 score_port: DecisionPort | None = None, score_weight: float = 0.0) -> None: ...
    def p_success(self, features: TaskFeatures, model_ref: str) -> float: ...
    def expected_output_tokens(self, features: TaskFeatures, model_ref: str) -> int: ...
    def update(self, features: TaskFeatures, model_ref: str, success: bool) -> None: ...
def load_predictor_table(path: str) -> TableSuccessPredictor:
    """Load config/routing_predictor/<version>.json produced by the eval harness exporter."""
def export_predictor_table(eval_report: EvalReport, *, out_dir: str) -> str: ...
```

- `p_success = (s + a) / (n + a + b)` (Beta-Binomial posterior mean) for cell `(intent or "_", tier)`; missing cell ⇒ prior mean of the tier row; blended `(1 − w)·p + w·score` when `score_port` is set.
- THE SYSTEM SHALL key cells by `served_model`'s tier when `RoutingOutcome.served_model` differs from `model_ref`.

## SPEC-AMR-BAT-001: `BatchAssignmentPolicy` (`routing/policies/batch_assignment.py`)

```python
@dataclass(frozen=True)
class BatchItem:
    item_id: str
    features: TaskFeatures
@dataclass(frozen=True)
class BatchAssignment:
    choices: Mapping[str, ModelChoice]          # item_id -> choice
    predicted_mean_accuracy: float
    predicted_cost_usd: float
    feasible: bool                              # mean >= alpha and caps respected
    solver: Literal["dual", "greedy", "max_accuracy"]
    iterations: int
    duality_gap: float | None
    waves: int
class BatchAssignmentPolicy:
    name = "batch"
    def __init__(self, *, predictor: SuccessPredictorPort, candidates: Sequence[str],
                 settings: Settings | None = None, price_fn: PriceFn | None = None) -> None: ...
    def assign(self, items: Sequence[BatchItem], *, alpha: float | None = None,
               concurrency: Mapping[str, int] | None = None) -> BatchAssignment: ...
    def select(self, features: TaskFeatures, *, budget: BudgetStatus | None = None) -> ModelChoice: ...  # N=1 case
PriceFn = Callable[[str, int, int], float]       # (model_ref, in_tokens, out_tokens) -> USD (TKE1 compute_cost_usd)
def solve_dual(cost: Sequence[Sequence[float]], acc: Sequence[Sequence[float]], caps: Sequence[int],
               alpha: float, *, max_iter: int, eta0: float, eps: float) -> tuple[list[int], int, float]: ...
def solve_greedy(cost: Sequence[Sequence[float]], acc: Sequence[Sequence[float]], caps: Sequence[int],
                 alpha: float) -> tuple[list[int], bool]: ...
```

Problem (formal statement in ARCHITECTURE §3.4): minimize `Σ_i Σ_j c_ij x_ij` subject to `(1/N) Σ_i Σ_j a_ij x_ij ≥ α`, `Σ_i x_ij ≤ C_j·W` for every model `j`, `Σ_j x_ij = 1` for every item `i`, `x_ij ∈ {0,1}`, with `x_ij` forced to 0 where model `j` is ineligible for item `i` (risk floor, capabilities, gateway policy).

- THE SYSTEM SHALL run `solve_dual` first and return its best feasible primal assignment when one exists (`solver="dual"`).
- IF no feasible primal is found within `routing_batch_max_iter` (default 200), THEN THE SYSTEM SHALL run `solve_greedy` (`solver="greedy"`).
- IF greedy cannot reach α, THEN THE SYSTEM SHALL return the capacity-feasible maximum-accuracy assignment with `feasible=False`, `solver="max_accuracy"`, and increment `prismal.routing_decisions_total{policy="batch",tier="infeasible"}` — never raise.
- WHEN `Σ_j C_j < N`, THE SYSTEM SHALL set `waves = ⌈N / Σ_j C_j⌉` and scale caps by `waves`.

## SPEC-AMR-BAT-002: Determinism and bounds

- THE SYSTEM SHALL produce identical `BatchAssignment` for identical inputs (ties broken by `(cost, model_ref, item_id)` lexicographic; no randomness; no wall-clock dependence).
- THE SYSTEM SHALL complete `assign()` for N=200, K=8 within 50 ms p95 on the CI runner (benchmark test, **SHOULD**).
- THE SYSTEM SHALL exclude `ModelTier.AUTO` refs from `candidates` (cost and accuracy unknowable).

## SPEC-AMR-PLC-001: Plan cache (`routing/plan_cache.py`)

```python
@dataclass(frozen=True)
class PlanCacheKey:
    intent: str
    canonical_target: str           # normalized slot values (lowercased, NFKD, sorted k=v; sha256 if > 256 chars)
    org_id: str
    tool_scope_hash: str            # sha256 of sorted tool names + schema hashes visible to the planner
    prompt_version: str             # planner prompt template version
@dataclass(frozen=True)
class CachedPlan:
    key: PlanCacheKey
    plan: Mapping[str, object]      # LLM-Compiler DAG or plan-and-execute steps (JSON-safe)
    created_at: float
    hits: int = 0
class PlanCacheBackend(Protocol):
    def get(self, key: PlanCacheKey) -> CachedPlan | None: ...
    def put(self, plan: CachedPlan, *, ttl_s: int) -> None: ...
    def invalidate(self, *, org_id: str | None = None, tool_scope_hash: str | None = None,
                   prompt_version: str | None = None) -> int: ...
class PlanCache:
    def __init__(self, backend: PlanCacheBackend | None = None, *, settings: Settings | None = None,
                 adapter_model_ref: str | None = None) -> None: ...
    def eligible(self, features: TaskFeatures, *, tool_names: Sequence[str]) -> bool: ...
    async def lookup(self, key: PlanCacheKey, *, request_summary: str) -> Mapping[str, object] | None: ...
    def store(self, key: PlanCacheKey, plan: Mapping[str, object]) -> None: ...
```

- `eligible` is True only WHEN `features.intent ∈ plan_cache_intents` (read-only allowlist) AND no tool in scope is write/exec-classified by `ActionInterceptor` AND `features.tainted is False`.
- WHEN `lookup` hits, THE SYSTEM SHALL adapt the plan's free-text slots with the `small`-tier model (`adapter_model_ref`) through `SecurePromptBuilder` and skip the planner call; on adaptation failure it SHALL treat the lookup as a miss.
- Backends: in-memory LRU (default) and SQLite (`plan_cache_path`). Embedding/semantic backends MAY be plugged via `PlanCacheBackend` but are never the default.

## SPEC-AMR-PLC-002: Invalidation

- WHEN the tool catalog visible to an agent changes (new `tool_scope_hash`) or a planner prompt version is bumped, THE SYSTEM SHALL miss on all keys carrying the old hash/version (structural miss) and SHALL purge them on the next `invalidate()` sweep.
- WHEN TTL `plan_cache_ttl_s` (default 86,400) elapses, THE SYSTEM SHALL miss.
- THE SYSTEM SHALL never return a plan whose `org_id` differs from the requester's (tenant isolation test).

## SPEC-AMR-RSC-001: Response cache (`routing/response_cache.py`) — SHOULD

```python
class ResponseCache:
    def __init__(self, *, settings: Settings | None = None, max_entries: int = 1024) -> None: ...
    def key(self, *, org_id: str, agent: str, model_ref: str, messages_hash: str, prompt_version: str) -> str: ...
    def get(self, key: str) -> str | None: ...
    def put(self, key: str, content: str, *, ttl_s: int) -> None: ...
```

- WHERE `response_cache_enabled` is True, THE SYSTEM SHALL serve only turns with no tools bound, temperature 0, a read-only intent and untainted input; all other turns bypass.

## SPEC-AMR-BEX-001: `BatchExecutor` (`routing/batch.py`, `providers/batch_adapters.py`)

```python
class BatchStatus(StrEnum):
    SUBMITTED = "submitted"; RUNNING = "running"; COMPLETED = "completed"; FAILED = "failed"; EXPIRED = "expired"; FALLBACK = "fallback"
@dataclass(frozen=True)
class BatchRequest:
    custom_id: str
    model_ref: str
    messages: Sequence[Mapping[str, object]]
    max_tokens: int
    response_format: Mapping[str, object] | None = None
@dataclass(frozen=True)
class BatchResult:
    custom_id: str
    content: str | None
    usage: Usage
    error: str | None = None
class BatchApiPort(Protocol):
    family: str                                       # "anthropic" | "openai" | "openrouter"
    async def submit(self, requests: Sequence[BatchRequest]) -> str: ...
    async def poll(self, job_id: str) -> BatchStatus: ...
    async def results(self, job_id: str) -> Sequence[BatchResult]: ...
    async def cancel(self, job_id: str) -> None: ...
class BatchExecutor:
    def __init__(self, *, settings: Settings | None = None, adapters: Mapping[str, BatchApiPort] | None = None,
                 meter: CostMeter | None = None) -> None: ...
    async def run(self, requests: Sequence[BatchRequest], *, deadline_s: float | None = None,
                  allow_sync_fallback: bool = True) -> Sequence[BatchResult]: ...
```

- THE SYSTEM SHALL group requests by provider family, submit one job per family, poll with exponential backoff (`batch_poll_initial_s` 10 → cap `batch_poll_max_s` 300, ±20 % jitter) and meter each result at batch price (new keyword `batch: bool = False` added to the TKE1 `compute_cost_usd` signature by AMR5-05; source precedence unchanged).
- IF a family has no adapter, the job fails, or `deadline_s` is exceeded, THEN THE SYSTEM SHALL execute the remaining requests synchronously via `ProviderRegistry` when `allow_sync_fallback` is True (status `fallback`), else return `BatchResult.error`.
- THE SYSTEM SHALL never batch a request whose input is tainted or whose agent is HITL-gated.

## SPEC-AMR-INT-001: Registry and helper wiring
> **Amended 2026-10-05 (M0/X0-03):** the snippet below shows only the AMR kwargs. The merged signature also keeps `agent=` (TKE2) and `session_id=` (GW1) — see `docs/adr/ADR-WAVE-001-llm-construction-contract.md` §2.1. AMR acts only on pipeline step 1 (model string); it never wraps the model.

```python
# providers/registry.py
def get_llm(self, model: str | None = None, streaming: bool = False, temperature: float | None = None,
            *, features: TaskFeatures | None = None, state: AgentState | None = None) -> BaseChatModel: ...
# routing/select.py
def extract_features(state: AgentState, agent: str, *, settings: Settings | None = None) -> TaskFeatures: ...
def select_model(state: AgentState, agent: str, *, settings: Settings | None = None) -> ModelChoice: ...
def record_outcome(state: AgentState, agent: str, outcome: RoutingOutcome) -> ModelChoice | None: ...
def maybe_seed_routing_run(state: AgentState, settings: Settings) -> None: ...
def get_routing_policy(state: AgentState) -> RoutingPolicyPort | None: ...
def clear_routing_run(state: AgentState) -> None: ...
```

- WHEN `adaptive_routing_enabled` is False OR `features` is None, THE SYSTEM SHALL execute `get_llm` exactly as in 3.15.0 (same arguments to `ChatLiteLLM`).
- WHEN `model` is passed explicitly, THE SYSTEM SHALL honour it and skip policy selection (explicit beats adaptive).
- `extract_features` is deterministic and LLM-free (intent from `metadata["routing"]["intent"]`, DEC-ROUTE label or `match_intent()`; tokens via TKE5 heuristics; risk from DEC `GatePolicy` tier map).

## SPEC-AMR-INT-002: Pattern integrations

- Skynet: `SkynetSupervisor` passes all sub-orders of one round to `BatchAssignmentPolicy.assign()` WHERE `routing_policy` includes `batch`; each `SwarmWorker` receives its `ModelChoice`.
- MoA: proposers assigned via `assign()`; the aggregator always uses the agent's static tier (no downgrade of the synthesis step).
- Eval harness: `--routing` flag runs a suite under a policy and reports cost, quality and `routing_savings_usd`; exporter writes the predictor table (SPEC-AMR-PRD-001).
- Supervisor-routed agents adopt `select_model(state, agent)` only through `get_llm(features=...)`; agent code changes are one line per agent and gated.

## SPEC-AMR-GW-001: No double routing with gateways

- THE SYSTEM SHALL let prismal policies choose the model (semantic choice) and let gateways choose the upstream provider for that model (provider choice).
- WHEN a `ModelChoice.model_ref` resolves through `GatewayRouter` AND `CascadePolicy` is active for the call, THE SYSTEM SHALL send the request without gateway model-level fallbacks (`extra_body["models"]` omitted, `owns_model_fallback=False`) and without `settings.fallback_model` (no double fallback, consistent with GW).
- WHEN `tier == AUTO` (e.g. `gw:openrouter/openrouter/auto`, allowed only if `openrouter_allow_auto`), THE SYSTEM SHALL treat it as terminal: no escalation, no batch candidacy, served model recorded in `RoutingOutcome.served_model` and `prismal.gateway_requests_total`.
- IF a gateway data policy (`GatewayDataPolicy`) forbids the routed model for the tenant/agent, THEN THE SYSTEM SHALL mark that candidate ineligible before solving (never route-then-deny).

## SPEC-AMR-SEC-001: Security floors (fail-closed)

- THE SYSTEM SHALL never select a tier below `routing_risk_floor[risk_tier]` (defaults: high → medium, critical → frontier).
- THE SYSTEM SHALL never use `AUTO` for `risk_tier="critical"`.
- IF the risk floor configuration is missing or invalid, THEN THE SYSTEM SHALL refuse to start (`RoutingConfigError`) when `adaptive_routing_enabled` is True.
- Routing decisions over tainted turns SHALL NOT downgrade below the agent's static tier.

## SPEC-AMR-CFG-001: Settings (`core/config.py`)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `adaptive_routing_enabled` | `bool` | `False` | Master flag |
| `routing_mode` | `Literal["shadow","enforce"]` | `"shadow"` | Shadow computes choices + counterfactual savings but uses the static model |
| `routing_policy` | `Literal["static","cascade","budget_aware","composite","batch"]` | `"static"` | Top-level policy |
| `routing_tier_models` | `str` | `"config/routing_tiers.yaml"` | Tier map / agent tiers / risk floor |
| `routing_risk_floor` | `dict[str, str]` | `{"high":"medium","critical":"frontier"}` | Overrides YAML floor |
| `routing_cascade_start_tier` | `str` | `"small"` | Cascade entry tier |
| `routing_cascade_max_escalations` | `int` | `2` | Escalation cap |
| `routing_cascade_tool_error_threshold` | `int` | `2` | Tool errors before escalation |
| `routing_adequacy_tau` | `float` | `0.7` | Adequacy Noul threshold |
| `routing_cache_affinity_enabled` | `bool` | `True` | Sticky model per thread/agent |
| `routing_batch_alpha` | `float` | `0.8` | Mean accuracy floor α |
| `routing_model_concurrency` | `dict[str, int]` | `{}` | Per-model concurrency cap C_j (missing ⇒ `parallel_max_workers`) |
| `routing_batch_max_iter` | `int` | `200` | Dual iterations |
| `routing_predictor_path` | `str \| None` | `None` | Predictor table file |
| `plan_cache_enabled` | `bool` | `False` | Plan cache |
| `plan_cache_ttl_s` | `int` | `86400` | TTL |
| `plan_cache_intents` | `list[str]` | `[]` | Read-only intent allowlist |
| `plan_cache_backend` | `Literal["memory","sqlite"]` | `"memory"` | Backend |
| `response_cache_enabled` | `bool` | `False` | Exact response cache |
| `batch_api_enabled` | `bool` | `False` | BatchExecutor |
| `batch_poll_initial_s` / `batch_poll_max_s` | `float` | `10` / `300` | Polling backoff |

`_validate_routing` rejects: α ∉ (0, 1]; negative caps; unknown tiers; escalations > 3; floor tiers not in `TIER_ORDER`; `routing_policy="batch"` without a predictor path when enabled.

## SPEC-AMR-ERR-001: Exceptions (`core/exceptions.py`)

```python
class RoutingError(PrismalError): ...
class RoutingConfigError(RoutingError): ...        # load-time only
class BatchAssignmentError(RoutingError): ...      # internal; assign() never raises it outward
class PlanCacheError(RoutingError): ...            # backend I/O; caught, counted, treated as miss
class BatchExecutorError(RoutingError): ...        # raised only when allow_sync_fallback=False
```

## SPEC-AMR-OTEL-001: Instruments (`monitoring/otel.py`)

`prismal.routing_decisions_total{policy,tier}`, `prismal.routing_escalations_total{reason}`, `prismal.plan_cache_hits_total{intent}` (+ `prismal.plan_cache_misses_total{reason}`), `prismal.batch_jobs_total{status}`, `prismal.routing_savings_usd` (counter in micro-USD: frontier counterfactual cost − actual cost, from TKE1 `Usage`). Span `prismal.routing.select` with attributes `policy`, `tier`, `model_ref`, `reason`.

## SPEC-AMR-SNP-001: Disabled path unchanged

- WHEN `adaptive_routing_enabled`, `plan_cache_enabled`, `response_cache_enabled` and `batch_api_enabled` are all False, THE SYSTEM SHALL compile a supervisor graph byte-for-byte identical to 3.15.0 and SHALL pass identical arguments to every `ChatLiteLLM` construction (snapshot + call-recording test).

## Acceptance Criteria Index

All requirements above are **MUST** except SPEC-AMR-RSC-001 and the latency bound in SPEC-AMR-BAT-002 (**SHOULD**). Each EARS clause is mapped to ≥ 1 task and ≥ 1 test in `TASKS.md` §8 (traceability matrix).
