# Prismal Skynet S+ — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` (not implemented) |
| **Version** | 1.0 |
| **Date** | 2026-07-11 |
| **Phase** | S+ |
| **Target package version** | `3.12.0` |
| **PLAN** | `specs/skynet-swarm-plus/PLAN.md` |
| **Architecture** | `specs/skynet-swarm-plus/ARCHITECTURE.md` |
| **TASKS** | `specs/skynet-swarm-plus/TASKS.md` |
| **Parent** | `specs/skynet-swarm/` (Phase S, `IMPLEMENTED`) |

---

## Conventions (inherited from Phase S + additions)

- All modules use `from __future__ import annotations`.
- Async where an LLM/network call happens; pure helpers are sync.
- Frozen dataclasses for value objects. Constructors accept
  `settings: Settings | None = None`.
- **No provider SDK imports outside `prismal/providers/`; no `prismal.mcp` /
  `prismal.skills` imports in `agents/skynet/**`** (existing AST guard extended).
- **Callable injection everywhere** — Phase S's `plan_fn`/`worker_fn`/
  `evaluate_fn`/`reduce_fn` plus S+'s `role_resolver` and remote `send_fn` — so
  every unit test runs with no LLM backend and no network.
- Order/role text is **user-derived**: it reaches a model only via
  `SecurePromptBuilder`; remote worker output crosses the A2A trust boundary and
  is **L1-sanitized (`InputSanitizer`) before touching state**.
- All Skynet runtime state stays under `state["metadata"]["skynet"]`.
- **Type-alias note (correcting the Phase-S SPEC prose):** the shipped callables
  take a *messages list*, not a bare string. S+ matches the code:
  `PlanFn = Callable[[list[dict[str, str]]], Awaitable[SwarmPlan]]`,
  `WorkerFn = Callable[[list[dict[str, str]]], Awaitable[str]]`,
  `EvaluateFn = Callable[[list[dict[str, str]]], Awaitable[tuple[bool, str]]]`.
- **Backward-compat invariant:** with every S+ flag off, all shipped signatures
  behave identically. New params are keyword-only with defaults; new value-object
  fields are optional with defaults; role defaults to `"worker"`.

---

## Module Summary

| Module | Status | Purpose |
|---|---|---|
| `prismal/agents/skynet/types.py` | **extend** | `WorkerResult.usage`; `SwarmResult.usage` |
| `prismal/agents/skynet/roles.py` | **new** | `SpecialistRole` + `RoleRegistry` (load `skynet_roles.yaml`) |
| `prismal/agents/skynet/supervisor.py` | **extend** | role-aware `plan()`; share the meter with workers |
| `prismal/agents/skynet/worker.py` | **extend** | per-role model/persona/tools; metering; remote delegation |
| `prismal/agents/skynet/remote.py` | **new** | `make_remote_send_fn()` — one order over A2A |
| `prismal/agents/skynet/reduce.py` | **extend** | meter the default reducer's LLM call |
| `prismal/agents/subgraphs/skynet/builder.py` | **extend** | thread the shared meter + role registry + remote send_fn |
| `prismal/core/config.py` | **extend** | `skynet_specialists_*`, `skynet_remote_workers_*`, `skynet_roles_path` |
| `prismal/core/exceptions.py` | **extend** | `SkynetRoleError` (config-time only) |

---

## SPEC-SP-TYP-001: Value-object extensions (`agents/skynet/types.py`)

Additive fields only — existing round-trips and defaults are unchanged.

```python
from prismal.budget.types import Usage   # Phase C value object

@dataclass(frozen=True)
class WorkerResult:
    order_id: str
    output: str
    success: bool
    error: str | None = None
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)   # S+: real per-worker token/cost
    role: str = "worker"                           # S+: the role that executed it
    remote: bool = False                           # S+: True when delegated over A2A

@dataclass(frozen=True)
class SwarmResult:
    goal: str
    answer: str
    worker_results: list[WorkerResult]
    rounds_completed: int
    deferred_orders: list[SwarmOrder]
    complete: bool
    usage: Usage = field(default_factory=Usage)   # S+: swarm total = planner+evaluator+reducer+Σworkers
```

`SwarmOrder.role` (already present, default `"worker"`) is reused unchanged.

## SPEC-SP-REG-001: Role registry (`agents/skynet/roles.py`, new)

```python
@dataclass(frozen=True)
class SpecialistRole:
    """One specialist worker profile, keyed by capability."""
    name: str                                   # role key, e.g. "researcher"
    model: str | None = None                    # per-role model; None → skynet_worker_model
    capabilities: list[str] = field(default_factory=list)  # tool filter (ToolProviderPort)
    persona: str = ""                           # role system prompt (trusted; not sanitized)
    remote_agent: str | None = None             # A2A card URL; when set the order is delegated (S+3)

# The generic fallback used when specialists are disabled or a role is unknown.
DEFAULT_ROLE = SpecialistRole(name="worker", capabilities=["general"])

class RoleRegistry:
    """Loads and resolves SpecialistRole profiles. Never raises at resolve time."""

    def __init__(self, roles: dict[str, SpecialistRole] | None = None) -> None: ...

    @classmethod
    def from_yaml(cls, path: str | Path, *, settings: Settings | None = None) -> RoleRegistry:
        """Load roles from ``skynet_roles.yaml`` (mirrors ToolPolicyEngine's YAML load).

        Raises SkynetRoleError only on a malformed file at load time (never at
        resolve time). A missing file yields an empty registry (all → DEFAULT_ROLE).
        """

    def resolve(self, role_name: str) -> SpecialistRole:
        """Return the SpecialistRole for *role_name*, or DEFAULT_ROLE if unknown.
        Best-effort — never raises (RF-SP-02)."""

    def known_roles(self) -> list[str]: ...
```

`skynet_roles.yaml` shape (operator-authored, `skynet_roles.example.yaml` shipped):

```yaml
roles:
  researcher:
    model: "claude-sonnet-4-5"
    capabilities: ["research", "web"]
    persona: "You are a meticulous research specialist. Cite sources."
  coder:
    model: "claude-opus-4-8"
    capabilities: ["code", "sandbox"]
    persona: "You are a senior engineer. Return runnable code only."
  legal_review:
    capabilities: ["legal"]
    remote_agent: "https://legal.example.com/.well-known/agent-card.json"
```

## SPEC-SP-SUP-001: Role-aware supervisor (`agents/skynet/supervisor.py`, extend)

```python
class SkynetSupervisor:
    def __init__(
        self,
        *,
        plan_fn: PlanFn | None = None,
        evaluate_fn: EvaluateFn | None = None,
        prompt_builder: SecurePromptBuilder | None = None,
        audit: AuditLogger | None = None,
        meter: CostMeter | None = None,     # S+: inject a SHARED meter (else builds its own)
        settings: Settings | None = None,
    ) -> None: ...

    # plan() gains role assignment when specialists are enabled.
    async def plan(self, goal: str, *, round: int = 1, unmet: list[SwarmOrder] | None = None) -> SwarmPlan:
        """As Phase S, plus: when settings.skynet_specialists_enabled, the default
        planner prompt asks the model to tag each SwarmOrder with a role from the
        registry's known_roles(); an unrecognised/absent tag falls back to
        "worker". With specialists disabled every order.role stays "worker"
        (Phase-S behaviour byte-for-byte)."""
```

- **Shared meter (RF-SP-05).** `__init__` accepts an injected `CostMeter`; the
  builder creates ONE meter and passes it to both the supervisor and every
  worker so planner + evaluator + reducer + all workers accumulate into the
  **same** running `Usage`. `enforce_token_budget()` (unchanged shape) now sees
  the whole swarm's tokens — closing the Phase-S under-count.
- `enforce_token_budget()` semantics are unchanged (`used >= skynet_token_budget`
  → `SkynetBudgetExceeded`), but `used` is now truthful. See ARCHITECTURE
  §DD-SP-004 for the optional convergence onto the Phase C `BudgetGuard`.

## SPEC-SP-WRK-001: Role-aware, metered, remote-capable worker (`agents/skynet/worker.py`, extend)

```python
class SwarmWorker:
    def __init__(
        self,
        *,
        worker_fn: WorkerFn | None = None,
        tool_provider: ToolProviderPort | None = None,
        interceptor: ActionInterceptor | None = None,
        prompt_builder: SecurePromptBuilder | None = None,
        role_registry: RoleRegistry | None = None,   # S+1: resolve role → model/persona/tools
        meter: CostMeter | None = None,              # S+2: shared meter (record each call once)
        send_fn: RemoteSendFn | None = None,         # S+3: delegate a remote-bound order over A2A
        budget_guard_fn: BudgetGuardFn | None = None,# S+2: pre-call soft/hard check (Phase C)
        settings: Settings | None = None,
    ) -> None: ...

    async def execute(self, order: SwarmOrder) -> WorkerResult:
        """Execute one order. New behaviour (all gated / defaulted):

        1. role = role_registry.resolve(order.role)  (DEFAULT_ROLE when disabled).
        2. If role.remote_agent and settings.skynet_remote_workers_enabled:
             delegate the order via send_fn (A2A). Output is L1-sanitized +
             audited; result.remote = True. (SPEC-SP-RMT-001.)
           Else: resolve tools via tool_provider.get_tools(
             agent_name="skynet_worker", capabilities=role.capabilities or [order.role]);
             build a secure prompt with role.persona as the system prompt and a
             per-role model = role.model or settings.skynet_worker_model.
        3. Record the LLM/A2A response into the shared meter (exactly once);
           populate WorkerResult.usage.
        4. When budget_guard_fn is wired, a hard breach raises SkynetBudgetExceeded
           and the fan-out stops dispatching further orders (soft → degrade).
        Failures (local or remote) are captured as WorkerResult(success=False,
        error=...) — never raised out of the node (Phase-S invariant preserved)."""
```

Type aliases:

```python
RemoteSendFn = Callable[[SpecialistRole, SwarmOrder], Awaitable[str]]   # -> remote output text
BudgetGuardFn = Callable[[dict[str, Any]], Awaitable[bool]]             # Phase C make_budget_guard_fn
```

## SPEC-SP-RMT-001: Remote worker over A2A (`agents/skynet/remote.py`, new)

```python
def make_remote_send_fn(
    *,
    manager: A2AConnectionManager | None = None,   # allowlist + pool (Phase I)
    sanitizer: InputSanitizer | None = None,
    audit: AuditLogger | None = None,
    settings: Settings | None = None,
) -> RemoteSendFn:
    """Return a send_fn that delegates one SwarmOrder to role.remote_agent over A2A.

    Uses A2AConnectionManager.get_client(role.remote_agent).send_task(
    A2AMessage(role="user", parts=[text=order.instruction], ...)); concatenates
    the streamed artifact text; InputSanitizer.sanitize()s it before returning;
    audits a2a.outbound (hash-first). Denied by the allowlist / unreachable /
    timeout → raises A2AAgentUnavailable, which SwarmWorker.execute() catches and
    turns into WorkerResult(success=False, remote=True, error=...)."""
```

Gating: effective only when `settings.skynet_remote_workers_enabled` **and**
`settings.a2a_enabled`; otherwise a remote-bound role is treated as local
(resolves the generic worker) and a `skynet.remote_disabled` warning is logged.

## SPEC-SP-RED-001: Metered reducer (`agents/skynet/reduce.py`, extend)

`reduce_results(...)` gains a keyword-only `meter: CostMeter | None = None`; the
default synthesis reducer records its LLM response into it (the current default
reducer call is unmetered — a second Phase-S gap). `concat`/`first_success`
remain LLM-free and record nothing. Signature otherwise unchanged.

## SPEC-SP-CFG-001: Settings (`core/config.py`, extend)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `skynet_specialists_enabled` | `bool` | `False` | S+1: assign roles + per-role model/persona |
| `skynet_roles_path` | `str` | `"config/skynet_roles.yaml"` | Role registry file |
| `skynet_remote_workers_enabled` | `bool` | `False` | S+3: allow remote-bound roles (also needs `a2a_enabled`) |
| `skynet_remote_allowlist` | `list[str]` | `[]` | fnmatch allowlist of A2A card URLs (empty + strict = deny-all) |

Existing `skynet_token_budget` is reused unchanged; its meaning becomes
**whole-swarm** once workers are metered. `_validate_skynet` gains: when
`skynet_remote_workers_enabled` is `True` but `a2a_enabled` is `False`, log a
`skynet.remote_needs_a2a` **warning** (not a hard error — remote roles simply
degrade to local).

## SPEC-SP-ERR-001: Exceptions (`core/exceptions.py`, extend)

```python
class SkynetRoleError(SkynetError):
    """Raised only at role-registry LOAD time for a malformed skynet_roles.yaml
    (never at resolve/dispatch time — resolve falls back to DEFAULT_ROLE)."""
```

`SkynetBudgetExceeded(BudgetExceeded, SkynetError)` is reused unchanged (now
raised with truthful whole-swarm `used`). Remote failures reuse Phase I's
`A2AAgentUnavailable` (caught in the worker, not re-raised).

## SPEC-SP-INT-001: Subgraph + supervisor wiring (gated)

- `build_skynet_subgraph(...)` gains keyword-only `role_registry`, `meter`,
  `send_fn`, `budget_guard_fn`; it builds ONE `CostMeter` and threads it into
  both the supervisor and the worker (fixing the separate-objects gap). Topology
  (`skynet_plan → dispatch → skynet_worker ⇉ skynet_reduce → skynet_evaluate →
  skynet_output`) is **unchanged** — a remote worker is still one `Send`.
- No new supervisor route, no new intent pattern: S+ enriches the existing
  `skynet` route. With all S+ flags off, `get_async_compiled_graph()` and the
  compiled subgraph are **byte-for-byte identical** to Phase S (snapshot).

## SPEC-SP-AUD-001: Audit + Observability

- Audit (hash-first, no raw content): `skynet_role_assigned` (order_id + role),
  reuse `a2a.outbound` for remote delegation, and the existing `skynet_plan` /
  `skynet_evaluate`.
- OTel spans: `skynet.worker` gains attrs `role`, `remote`, `worker_tokens`;
  new `skynet.remote` span for A2A delegation.
- Counters: `prismal.skynet_role_assignments_total{role}`,
  `prismal.skynet_worker_tokens_total`, `prismal.skynet_remote_calls_total`,
  `prismal.skynet_remote_failures_total`; reuse `prismal.budget_cutoffs_total`
  for a swarm hard breach.

## Acceptance Criteria (per requirement)

| Requirement | Acceptance criterion |
|---|---|
| RF-SP-01/02 | `RoleRegistry.from_yaml` loads roles; `resolve("unknown")` → `DEFAULT_ROLE`; a malformed file raises `SkynetRoleError` at load, never at resolve |
| RF-SP-03 | With specialists enabled, a fake planner tags ≥2 distinct roles; with it disabled every `order.role == "worker"` |
| RF-SP-04 | Two roles with different `model` resolve to two distinct models (fake `ProviderRegistry`), and each uses its `persona`; role `"worker"` path is byte-for-byte Phase S |
| RF-SP-05 | After an end-to-end run, `SwarmResult.usage.total_tokens` equals planner+evaluator+reducer+Σ(worker `usage`) from the shared meter (fake token counts) |
| RF-SP-06 | With `skynet_token_budget` below the swarm's metered usage, dispatch stops and `SkynetBudgetExceeded` carries the truthful `used`; a soft cap degrades instead of aborting |
| RF-SP-07 | A remote-bound role delegates via an injected `send_fn` spy; output is sanitized (spy sees `InputSanitizer`) and `a2a.outbound` is audited; `WorkerResult.remote is True` |
| RF-SP-08 | A `send_fn` raising `A2AAgentUnavailable` yields `WorkerResult(success=False, remote=True)` and the swarm still reduces the other workers |
| RF-SP-09 | Compiled graph + skynet subgraph snapshot byte-for-byte unchanged with all S+ flags off |
| RF-SP-10 | Every new component runs in a unit test with injected fakes — no LLM, no network (`send_fn`/`worker_fn`/`role_resolver` all faked) |
