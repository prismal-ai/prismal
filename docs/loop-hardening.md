# Loop Hardening (Phase LH)

Closes two gaps in the agentic-loop mechanics underneath Prismal's pattern
catalogue: **no context/message-window compaction** (`state["messages"]`
grows unbounded) and **no dynamic tool provisioning by task phase** (the tool
catalogue is static for the whole run). Both land as **opt-in, additive**
controls — with every flag at its default (`False`), the compiled supervisor
graph and `get_tools_for_agent()`'s output are byte-for-byte unchanged.

| Control | Module | Flag |
|---|---|---|
| Context compaction | `agents/context_compaction.py` | `context_compaction_enabled` |
| Dynamic tool gating by phase | `agents/loop_phase.py`, `agents/extension/providers.py` | `tool_gating_enabled` |

## LH1 — Context compaction

`ContextCompactor` trims/summarizes `state["messages"]` once a configurable
message-count (or, with `budget_enabled`, cumulative-token) threshold is
exceeded, always keeping the most recent `context_compaction_keep_recent`
messages verbatim. Compaction is expressed via LangGraph `RemoveMessage`
entries — never in-place mutation — seeded once per turn from
`supervisor_node` (mirrors `maybe_seed_budget_run`/`maybe_seed_hardening_run`).

```bash
export PRISMAL_CONTEXT_COMPACTION_ENABLED=true
export PRISMAL_CONTEXT_COMPACTION_STRATEGY=truncate   # truncate (default) | summarize
export PRISMAL_CONTEXT_COMPACTION_MAX_MESSAGES=60
export PRISMAL_CONTEXT_COMPACTION_KEEP_RECENT=10
```

An optional second hook, `react_loop(..., context_compactor=...)`, compacts a
single node's local `loop_messages` accumulator (position-based, since those
messages haven't round-tripped through the `add_messages` reducer and may
lack a stable `id`) — protects one very-long in-node ReAct session even
before the supervisor is revisited. `context_compaction_react_kwargs(state)`
mirrors `hardening_react_kwargs` for opting individual agent nodes in.

**Reducer ordering caveat:** LangGraph's `add_messages` only ever *appends*
new (non-removal) entries to the end of the surviving history — in
`summarize` mode the summary message lands *after* the kept-recent tail in
`state["messages"]`, not spliced in where the removed segment was. Nodes
that build their own prompt from `state["messages"]` already window/reorder
it themselves, so this never surfaces as an out-of-order LLM prompt.

## LH2 — Dynamic tool gating by phase

`resolve_phase(state)` is a deterministic, LLM-free phase hint
(`"planning"`/`"executing"`/`"finishing"`/`None`), derived from an explicit
`state["metadata"]["loop"]["phase"]` or from `task_plan`/`pending_tasks`/
`completed_tasks`. `ToolProviderPort.get_tools()` gains an optional `phase`
keyword (a non-breaking widening — `runtime_checkable` Protocols don't check
keyword signatures); `CompositeToolProvider(phase_capability_map=...)`
intersects the phase's capability override with the caller's capabilities
before delegating to live sub-providers — never a superset.

```bash
export PRISMAL_TOOL_GATING_ENABLED=true
export PRISMAL_TOOL_GATING_PHASE_MAP_PATH=config/tool_gating_phases.yaml
```

```yaml
# config/tool_gating_phases.yaml
coder:
  planning:
    - general
    - file_management
```

A sub-provider whose `get_tools` doesn't accept `phase` is never broken —
`tool_registry._observed_get_tools` (the single choke point) and
`CompositeToolProvider` both fall open to a phase-less call on `TypeError`.
No shipped agent node passes a non-`None` phase in this release; rollout is
opt-in per flow.

## Observability

- `prismal.context_compactions_total{strategy}`
- `prismal.context_compaction_messages_dropped_total`
- `prismal.context_compaction_summarize_errors_total`
- `prismal.tool_gate_narrowed_total{agent}`
- `prismal.tool_gate_phase_resolved_total{agent,phase}`

## Example

See `examples/loop_hardening.py`.

## Related

- `specs/loop-hardening/` — SPEC/ARCHITECTURE/PLAN/TASKS.
- `docs/security/runtime-hardening.md` / `docs/budget.md` — the per-run
  registry pattern and `BudgetGuard`/`CostMeter` reuse this phase imitates.
- `docs/tool-providers.md` — `ToolProviderPort` this phase extends.
