# Prismal Loop Hardening — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Target package version** | `3.7.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/loop-hardening/PLAN.md` |
| **Architecture** | `specs/loop-hardening/ARCHITECTURE.md` |
| **TASKS** | `specs/loop-hardening/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Async only where an LLM call is involved (`ContextCompactor`'s `summarize` strategy); pure helpers (`resolve_phase`, threshold checks, phase-narrowing) are `sync` and must **not raise** on the hot path (fail-open, mirroring `ToolProviderPort.get_tools`'s existing "must not raise" contract and Phase H's `warn`-before-`enforce` philosophy).
- Frozen dataclasses for value objects.
- Constructors accept `settings: Settings | None = None`.
- No provider SDK imports outside `prismal/providers/`; no `prismal.mcp` / `prismal.skills` import inside `prismal/agents/**`.
- All loop-hardening runtime state lives under `state["metadata"]["loop"]`; the LH1 per-run watermark lives in an in-process registry keyed by `session_id` (never in checkpointed state), mirroring `budget/resolve.py` and `security/hardening_run.py`.
- `context_compaction_enabled=False` and `tool_gating_enabled=False` ⇒ zero wiring observable (both flags default `False`, independently).

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/agents/context_compaction.py` | `CompactionStrategy`, `CompactionResult`, `ContextCompactor`, per-run seeding trio |
| `prismal/agents/loop_phase.py` | `resolve_phase` |
| `prismal/agents/extension/ports.py` | *(extend)* `ToolProviderPort.get_tools(..., phase=...)` |
| `prismal/agents/extension/providers.py` | *(extend)* `CompositeToolProvider(..., phase_capability_map=...)`, `load_phase_capability_map` |
| `prismal/agents/tool_registry.py` | *(extend)* `get_tools_for_agent(..., phase=...)`, `get_tools_for_agent_ctx(..., phase=...)` |
| `prismal/core/config.py` | `context_compaction_*` / `tool_gating_*` settings |
| `prismal/core/exceptions.py` | `LoopHardeningError` hierarchy |

---

## SPEC-LH-CTX-001: Compaction value objects and strategy (`agents/context_compaction.py`)

```python
class CompactionStrategy(str, Enum):
    TRUNCATE = "truncate"      # default — drop the middle segment, no LLM call
    SUMMARIZE = "summarize"    # opt-in — replace the middle segment with one LLM summary


@dataclass(frozen=True)
class CompactionResult:
    compacted: bool
    strategy: CompactionStrategy
    messages_dropped: int
    removed_ids: tuple[str, ...]           # ids handed to RemoveMessage
    summary_message: BaseMessage | None    # set only when strategy == SUMMARIZE and compacted
    reason: str = ""                       # "" | "message_count" | "token_threshold"
```

**Acceptance:** `CompactionResult.compacted is False` carries `messages_dropped == 0`, `removed_ids == ()`, `summary_message is None` — a no-op result is unambiguous and cheap to check.

## SPEC-LH-CTX-002: `ContextCompactor` (`agents/context_compaction.py`)

```python
SummarizerFn = Callable[[list[BaseMessage]], Awaitable[str]]   # (messages) -> summary text


class ContextCompactor:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        summarizer_fn: SummarizerFn | None = None,   # default wires ProviderRegistry().get_llm() lazily
        budget_guard: BudgetGuard | None = None,      # optional token-threshold source + metering sink
    ) -> None: ...

    def should_compact(self, messages: Sequence[BaseMessage]) -> tuple[bool, str]:
        """Return (should_compact, reason). reason is 'message_count' when
        len(messages) > settings.context_compaction_max_messages; 'token_threshold'
        when budget_guard is set, settings.context_compaction_token_threshold > 0,
        and budget_guard.meter.usage.total_tokens exceeds it. Never raises."""

    async def compact(self, messages: Sequence[BaseMessage]) -> CompactionResult:
        """Split messages into [older-middle-segment | last context_compaction_keep_recent
        verbatim]. TRUNCATE: removed_ids covers the whole older segment, summary_message
        is None. SUMMARIZE: calls summarizer_fn on the older segment (metered via
        budget_guard.meter.record_response when set), builds one summary AIMessage,
        removed_ids covers the same segment. On any summarizer failure, falls back to
        TRUNCATE for this call and increments the summarize-error counter. Never raises."""

    def to_state_update(self, result: CompactionResult) -> dict[str, list[BaseMessage]]:
        """Return {'messages': [RemoveMessage(id=i) for i in result.removed_ids] +
        ([result.summary_message] if result.summary_message else [])}, or {} when
        result.compacted is False. This is the only shape ever merged into AgentState —
        callers must apply it through the add_messages reducer, never by direct mutation."""
```

**Acceptance:** for a 200-message history with `context_compaction_max_messages=60` and `keep_recent=10`, `compact()` on `TRUNCATE` returns a `CompactionResult` whose `removed_ids` covers exactly the 130 middle messages (200 − 60 head-of-window already excluded by `should_compact`'s trigger vs. the 10 kept tail — exact accounting defined in `TASKS.md`'s test inventory), and `to_state_update()` yields only `RemoveMessage` entries, no wholesale list replacement.

## SPEC-LH-CTX-003: Per-run seeding (`agents/context_compaction.py`)

```python
def maybe_seed_context_compaction_run(
    state: AgentState, settings: Settings, *, budget_guard: BudgetGuard | None = None
) -> None:
    """No-op unless settings.context_compaction_enabled. Idempotent per turn
    (same _turn_signature convention as budget/resolve.py). Writes only the
    serializable marker state['metadata']['loop']['compaction'] =
    {'enabled': True, 'strategy': settings.context_compaction_strategy};
    the live ContextCompactor + 'compacted_upto' watermark stay in the
    in-process registry keyed by session_id."""


def get_context_compactor(state: AgentState) -> ContextCompactor | None:
    """Return the per-run ContextCompactor, or None when disabled/unseeded."""


def clear_context_compaction_run(state: AgentState) -> None:
    """Release the per-run entry for state (idempotent)."""
```

- Integration: `supervisor_node` calls `maybe_seed_context_compaction_run(state, settings, budget_guard=get_budget_guard(state))` immediately after the existing `maybe_seed_hardening_run` call (`prismal/agents/supervisor.py:806`); then, when a compactor is present, `ContextCompactor.should_compact(state["messages"])` is checked and — if due — `compact()` + `to_state_update()` are folded into the state update the supervisor node already returns for that turn.
- The "already compacted up to message N" watermark (preventing the same middle segment from being re-summarized every subsequent turn) lives in the same in-process dict as the compactor instance, keyed by `session_id`, exactly like `_RUN_ENGINES` in `budget/resolve.py` / `security/hardening_run.py`.

**Acceptance:** two consecutive supervisor turns within the same session, with no new messages added between them, do not trigger a second compaction of the same segment (`prismal.context_compactions_total` increments once, not twice).

## SPEC-LH-PHS-001: Phase resolution (`agents/loop_phase.py`)

```python
Phase = Literal["planning", "executing", "finishing"]


def resolve_phase(state: AgentState) -> Phase | None:
    """Deterministic, LLM-free phase hint for tool gating (SPEC-LH-GAT-001 consumer).

    Precedence:
      1. state['metadata'].get('loop', {}).get('phase') — explicit hint set by
         the supervisor or a planner node; returned verbatim if it is one of
         the three known Phase literals, else ignored (falls through to #2).
      2. Derived from AgentState.task_plan / pending_tasks / completed_tasks:
         - no task_plan (empty list)                      -> None
         - task_plan set, pending_tasks non-empty          -> 'planning' when
           completed_tasks is also empty (nothing started yet), else 'executing'
         - task_plan set, pending_tasks empty              -> 'finishing'
      3. No plan and no explicit hint                      -> None (no gating)

    Never raises; never calls an LLM.
    """
```

**Acceptance:** a state with `task_plan=["a","b","c"]`, `completed_tasks=[]`, `pending_tasks=["a","b","c"]` resolves to `"planning"`; the same state after one task moves from `pending_tasks` to `completed_tasks` resolves to `"executing"`; `pending_tasks=[]` with a non-empty `task_plan` resolves to `"finishing"`; an explicit `metadata["loop"]["phase"] = "executing"` overrides any task_plan-derived value.

## SPEC-LH-GAT-001: `ToolProviderPort.get_tools()` optional `phase` (`agents/extension/ports.py`)

```python
@runtime_checkable
class ToolProviderPort(Protocol):
    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,      # NEW — optional, default None (SPEC-LH-GAT-001)
    ) -> list[BaseTool]:
        """... (unchanged docstring) ... `phase` is the Fase LH dynamic tool-gating
        hint (SPEC-LH-GAT-001) — None (default) means no phase-based narrowing,
        reproducing pre-LH behavior exactly. Conforming implementations that do not
        support phase-based narrowing MAY ignore the argument; core call sites never
        assume a provider honours it and fail open (see DD-LH-006) when a provider's
        get_tools does not accept the keyword at all."""
```

- This is a **widening** of an existing Protocol method's signature (new keyword, default value) — not a new method. `isinstance(obj, ToolProviderPort)` (`conforms_to`) continues to pass for any object exposing a callable `get_tools` attribute; `runtime_checkable` Protocols do not check keyword signatures, only attribute presence, so pre-LH conforming objects still structurally satisfy the Protocol. Call-compatibility (whether the object *accepts* `phase=`) is handled at the call site (DD-LH-006), not by the Protocol.

**Acceptance:** an existing `FakeToolProvider`/`StubToolProvider`/`McpToolProvider`/`SkillToolProvider` instance (none of which will initially implement `phase`) still satisfies `isinstance(obj, ToolProviderPort)`; calling `tool_registry.get_tools_for_agent(name)` with no `phase` argument produces byte-for-byte the same result as before LH2.

## SPEC-LH-GAT-002: Composite phase narrowing (`agents/extension/providers.py`)

```python
class CompositeToolProvider:
    def __init__(
        self,
        providers: list[ToolProviderPort],
        *,
        max_total: int = _MAX_TOTAL_TOOLS,
        fixed_tool_agents: frozenset[str] = _FIXED_TOOL_AGENTS,
        phase_capability_map: Mapping[str, Mapping[str, list[str]]] | None = None,  # NEW
    ) -> None: ...

    def get_tools(
        self,
        *,
        agent_name: str,
        capabilities: list[str] | None = None,
        phase: str | None = None,      # NEW
    ) -> list[BaseTool]:
        """Unchanged merge/dedupe/cap/fixed-tool-agent behavior. NEW: when phase is
        not None and phase_capability_map has an entry for (agent_name, phase), the
        effective capabilities passed to each live sub-provider is the intersection
        of *capabilities* (or 'all' when None) with that entry's list — never a
        superset of what capabilities alone would have allowed. Each live
        sub-provider is invoked through a fail-open shim: if calling it with
        phase=phase raises TypeError (a provider that has not adopted the phase
        keyword), the call is retried once without phase (DD-LH-006); the shim never
        raises past this method."""


def load_phase_capability_map(path: str | None = None) -> dict[str, dict[str, list[str]]]:
    """Load + validate config/tool_gating_phases.yaml (or *path*). Bad YAML shape
    raises ToolGatingConfigError at load time (fail loud at startup, not at
    request time — mirrors load_tool_policies from Phase H)."""
```

**Acceptance:** with `phase_capability_map={"coder": {"planning": ["general", "file_management"]}}`, `capabilities=None`, `phase="planning"`, the effective sub-provider call narrows to `["general", "file_management"]`; the same call with `phase="executing"` (no map entry) passes `capabilities` through unchanged. A fake sub-provider whose `get_tools` signature has no `phase` parameter still contributes its tools (via the fallback call) and does not raise.

## SPEC-LH-GAT-003: `tool_registry` threading (`agents/tool_registry.py`)

```python
def get_tools_for_agent(
    agent_name: str,
    required_capabilities: list[str] | None = None,
    *,
    phase: str | None = None,      # NEW — default None, SPEC-LH-GAT-001
) -> list[BaseTool]: ...

def get_tools_for_agent_ctx(
    agent_name: str,
    config: RunnableConfig | None = None,
    required_capabilities: list[str] | None = None,
    *,
    phase: str | None = None,      # NEW
) -> list[BaseTool]: ...
```

- `phase` defaults to `None` in both signatures; existing call sites (every current agent node) are unaffected until they are explicitly updated to pass `phase=resolve_phase(state)`.
- `_observed_get_tools` gains the same optional `phase` parameter and applies the DD-LH-006 fail-open shim once, at this single choke point, so `CompositeToolProvider` and any custom top-level provider are both covered without duplicating the shim.

**Acceptance:** `get_tools_for_agent("coder")` (no `phase`) and `get_tools_for_agent("coder", phase=None)` are byte-for-byte identical to the pre-LH2 call; `tool_gating_enabled=False` at the settings level means no node is updated to pass a non-`None` phase in the shipped agent nodes for `3.7.0` (rollout is opt-in per DD-LH-009 and the PLAN §5 scope).

## SPEC-LH-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `context_compaction_enabled` | `bool` | `False` | Master opt-in toggle for LH1 |
| `context_compaction_strategy` | `str` | `"truncate"` | `truncate`\|`summarize` |
| `context_compaction_max_messages` | `int` | `60` | Raw message-count trigger threshold |
| `context_compaction_token_threshold` | `int` | `0` | Cumulative-token trigger threshold via Budget's `CostMeter` (`0` = disabled; only effective when `budget_enabled`) |
| `context_compaction_keep_recent` | `int` | `10` | Number of most-recent messages always kept verbatim |
| `context_compaction_summarizer_model` | `str \| None` | `None` | Model id for the `summarize` strategy; `None` uses `ProviderRegistry().get_llm()`'s default |
| `context_compaction_min_interval_messages` | `int` | `20` | Minimum new messages since the last compaction before compacting again (avoids re-compacting every turn) |
| `tool_gating_enabled` | `bool` | `False` | Master opt-in toggle for LH2 |
| `tool_gating_phase_map_path` | `str` | `"config/tool_gating_phases.yaml"` | Phase → capability-override table path |

Env prefix `PRISMAL_` (e.g. `PRISMAL_CONTEXT_COMPACTION_ENABLED`, `PRISMAL_TOOL_GATING_ENABLED`). `_validate_loop_hardening` rejects an unknown `context_compaction_strategy` and a negative `*_max_messages`/`*_keep_recent`/`*_token_threshold` at load time.

## SPEC-LH-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class LoopHardeningError(PrismalError): ...
class ContextCompactionError(LoopHardeningError): ...   # internal use / tests; compact() itself never raises this outward
class ToolGatingConfigError(LoopHardeningError): ...     # bad tool_gating_phases.yaml — raised at load time, not per-request
```

## SPEC-LH-OTEL-001: Counters (`monitoring/otel.py` extension)

`prismal.context_compactions_total{strategy}`, `prismal.context_compaction_messages_dropped_total`, `prismal.context_compaction_summarize_errors_total`, `prismal.tool_gate_narrowed_total{agent}`, `prismal.tool_gate_phase_resolved_total{agent,phase}`.

## Acceptance Criteria (per requirement)

| Requirement | Acceptance criterion |
|---|---|
| RF-LH-001 | `ContextCompactor.to_state_update()` never returns a `"messages"` value that is anything other than `RemoveMessage`/message-append entries — no wholesale list replacement |
| RF-LH-002 | The last `context_compaction_keep_recent` messages are never present in `removed_ids` |
| RF-LH-003 | `strategy="truncate"` produces `summary_message is None`; `strategy="summarize"` produces exactly one summary message and records one metered call when a `BudgetGuard` is present |
| RF-LH-004 | With `budget_enabled=False`, only the message-count trigger can fire; with it `True` and `context_compaction_token_threshold>0`, the token trigger can also fire |
| RF-LH-005 | `isinstance(provider, ToolProviderPort)` is unaffected by the new keyword; providers with the old two-keyword signature keep working when `phase` is omitted |
| RF-LH-006 | `CompositeToolProvider` intersects `phase_capability_map[agent][phase]` with `capabilities` only when both are present; otherwise identical to pre-LH2 |
| RF-LH-007 | `resolve_phase()` matches all four documented derivation branches exactly |
| RF-LH-008 | A fake sub-provider with a two-keyword `get_tools` does not raise when `CompositeToolProvider.get_tools(..., phase="planning")` is called |
| RF-LH-009 | With both flags `False`, the compiled supervisor graph snapshot and `get_tools_for_agent()` output are byte-for-byte unchanged versus a pre-LH baseline |
| RF-LH-010 | Each of the five counters increments on its triggering event exactly once |
| RF-LH-011 | AST guard tests (`test_no_mcp_skills_imports.py` and the provider-import guard) keep passing unmodified |
