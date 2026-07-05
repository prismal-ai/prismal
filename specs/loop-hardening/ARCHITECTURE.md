# Prismal Loop Hardening — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | LH |
| **Target package version** | `3.7.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/loop-hardening/PLAN.md` |
| **SPEC** | `specs/loop-hardening/SPEC.md` |
| **TASKS** | `specs/loop-hardening/TASKS.md` |

---

## 1. Context

Two loop-mechanics gaps were confirmed against the real code (not just the gap-analysis prose):

- `grep -rniE "compact|trim_messages|summariz" prismal/memory prismal/agents/graph.py` → zero hits. `prismal/memory/short_term.py::ShortTermMemory` is a bounded FIFO deque used by the conversation-history layer, unrelated to `AgentState.messages`; it evicts silently and is not wired into the LangGraph loop at all. The only thing bounding `AgentState.messages` growth today is the Budget/RunawayGuard *stop* mechanisms (Phase C/H) — neither trims what is already accumulated.
- `prismal/agents/extension/ports.py::ToolProviderPort.get_tools(self, *, agent_name: str, capabilities: list[str] | None = None) -> list[BaseTool]` has exactly two keyword parameters today; there is no notion of "phase". `tool_registry.get_tools_for_agent()` and `CompositeToolProvider.get_tools()` mirror the same two-parameter shape throughout.

Two existing precedents shape this design directly:

- **`budget/resolve.py`** — the per-run, in-process registry keyed by `session_id`, seeded once per turn from `supervisor_node` via `maybe_seed_budget_run(state, settings)`, storing only a serializable marker (`state["metadata"]["budget"] = {...}`) while the live engine (`CostMeter`/`BudgetGuard`) stays out of checkpointed state.
- **`security/hardening_run.py`** — the identical pattern for `IndirectInjectionDetector` / `RunawayGuard` / `RunToolPolicy` / `TaintRegistry`, seeded from the same `supervisor_node` call site immediately after the Budget seeding (`prismal/agents/supervisor.py:797-806`).

Loop Hardening reuses this exact shape for LH1 and deliberately does **not** need it for LH2 (phase resolution is a cheap, stateless read on every call — no per-run live object to keep alive).

## 2. Feasibility with the existing core (confirmed)

- `AgentState.messages` uses LangGraph's `add_messages` reducer (`langgraph.graph.message.add_messages`), which supports `langchain_core.messages.RemoveMessage` entries to delete a message by `id` from the merged history — this is the standard, reducer-safe way to shrink persisted state without breaking `add_messages` semantics. `langgraph>=0.2.66` / `langchain-core>=0.3.28` (both already pinned in `pyproject.toml`) support this; the repo does not use `RemoveMessage` anywhere yet, so LH1 introduces the pattern for the first time — no new dependency, no version bump required.
- The system prompt for every agent node (`researcher.py`, `coder.py`, `data_analyst.py`, …) is constructed **fresh on every call** as a local `SystemMessage` and prepended to the LLM input — it is never stored in `state["messages"]`. This *simplifies* LH1: the compactor never has to special-case "preserve message 0" — it operates purely on the persisted human/ai/tool history, and every node keeps re-injecting its own system prompt regardless of what LH1 does to state.
- `supervisor_node` (`prismal/agents/supervisor.py:788-806`) already runs `maybe_seed_budget_run` and `maybe_seed_hardening_run` once per turn, before routing. This is the natural third call in that block for `maybe_seed_context_compaction_run`.
- `budget/usage.py::extract_token_usage()` and the `CostMeter.usage` snapshot already give a cumulative token count for the run when `budget_enabled` — LH1's token-based trigger reads this via `get_budget_guard(state)` instead of adding a tokenizer dependency.
- `ToolProviderPort` is a `@runtime_checkable` `Protocol` (structural typing) — Python does not enforce exact keyword signatures at the type level, only attribute/method presence. This means adding an optional `phase` keyword is *type-compatible* but not *call-compatible*: a third-party class whose `get_tools` method does not accept `phase=` will raise `TypeError` if the core calls it with that keyword. §3.3 below designs the fail-open shim this requires.
- `CompositeToolProvider` already has a fail-open pattern for sub-provider errors (`try/except Exception` around each sub-provider's `get_tools`, `OTelManager().increment_counter("tool_provider_subprovider_errors", ...)`) — LH2's phase-narrowing and its `TypeError` fallback reuse the same shape.

No new LangGraph capability beyond `RemoveMessage` (already available in the pinned version) is required.

## 3. Proposed Architecture

### 3.1 New / extended modules

| Module | Purpose |
|---|---|
| `prismal/agents/context_compaction.py` | `ContextCompactor`, `CompactionStrategy`, `CompactionResult`; the per-run seeding trio (`maybe_seed_context_compaction_run`, `get_context_compactor`, `clear_context_compaction_run`) mirroring `budget/resolve.py` |
| `prismal/agents/loop_phase.py` | `resolve_phase(state) -> str \| None` — deterministic phase derivation (no LLM call) |
| `prismal/agents/extension/ports.py` | *(extend)* `ToolProviderPort.get_tools()` gains optional `phase: str \| None = None` |
| `prismal/agents/extension/providers.py` | *(extend)* `CompositeToolProvider` accepts an optional `phase_capability_map`; narrows `capabilities` per `(agent_name, phase)` before delegating; fail-open `TypeError` shim around each sub-provider call when `phase` is set |
| `prismal/agents/tool_registry.py` | *(extend)* `get_tools_for_agent()` / `get_tools_for_agent_ctx()` gain an optional `phase` parameter, threaded to the provider through the same fail-open shim |
| `prismal/core/config.py` | `context_compaction_*` / `tool_gating_*` settings |
| `prismal/core/exceptions.py` | `LoopHardeningError` hierarchy |
| `prismal/monitoring/otel.py` | loop-hardening counters |
| `config/tool_gating_phases.yaml` | default phase → capability-override table (example shipped in this spec dir) |

All new persisted state lives under `state["metadata"]["loop"]` (e.g. `state["metadata"]["loop"]["phase"]`, `state["metadata"]["loop"]["compaction"] = {"enabled": True, "strategy": ...}`), mirroring the `metadata["budget"]` / `metadata["hardening"]` / `metadata["skynet"]` / `metadata["kokoro"]` isolation convention. **No live objects** (the `ContextCompactor`, any bound summarizer LLM) are ever placed in checkpointed state — LH1's per-run bookkeeping (the "already compacted up to message N" watermark) lives in the same kind of in-process registry keyed by `session_id` as Budget/Hardening.

### 3.2 LH1 data flow (context compaction, `context_compaction_enabled=True`)

```
supervisor_node (per turn)
   │
   ├─► maybe_seed_budget_run(state, settings)         (existing, Phase C)
   ├─► maybe_seed_hardening_run(state, settings)       (existing, Phase H)
   └─► maybe_seed_context_compaction_run(state, settings)   (new, LH1)
              │
              ▼
       ContextCompactor.maybe_compact(state)
              │
     should_compact? ── no ──► state unchanged, return {}
              │ yes
              ▼
   split: [ ...older-middle-segment... | keep_recent verbatim tail ]
              │
     strategy = truncate ──────────────► RemoveMessage(id) for each dropped message
              │
     strategy = summarize (opt-in) ────► one LLM call (metered via Budget when
              │                          budget_enabled) → summary AIMessage
              │                          + RemoveMessage(id) for the segment it replaces
              ▼
   state update: {"messages": [RemoveMessage(...), ..., <summary message>?]}
              │
              ▼
   add_messages reducer merges the update into the checkpointed history
   (LangGraph's documented RemoveMessage-by-id semantics — no in-place mutation)
```

A secondary, **optional** hook exists inside a single node's local ReAct loop: `react_loop(..., context_compactor=None)` can apply the same trim/summarize logic to its own local `loop_messages` accumulator (the list that grows within one node invocation across tool-call iterations, before that node returns and the result round-trips through `add_messages`). This protects a single very-long ReAct session (many tool iterations in one node call) even before the supervisor is revisited. It is optional and off unless a compactor is explicitly passed — the supervisor-seam integration (above) is the primary, always-available mechanism when `context_compaction_enabled=True`.

> **Open question:** should `3.7.0` ship both hooks, or only the supervisor-seam one (cross-turn, persisted-state compaction) as the MVP, deferring the `react_loop` local-loop hook to a follow-up? The gap analysis and the task framing both cite `react_loop` as *the* integration point, but `react_loop` does not receive `AgentState` today (it only sees a plain `messages: Sequence[object]` and returns a bare message, not a state update) — wiring persisted-state compaction there would require a signature change from "returns a message" to "returns a state update", which is a larger, more invasive change than the supervisor-seam approach. This document specs both but flags the local-loop hook as the item most likely to slip to a later minor if `TASKS.md` needs to shrink.

### 3.3 LH2 data flow (dynamic tool gating, `tool_gating_enabled=True`)

```
agent node (e.g. coder_node)
   │
   ├─► phase = resolve_phase(state)     (new, LH2 — pure function, no LLM call)
   │        │
   │        ├─ state["metadata"]["loop"]["phase"] present? → use it verbatim
   │        └─ else derive from task_plan/pending_tasks/completed_tasks:
   │              no task_plan yet                → "planning"
   │              pending_tasks non-empty          → "executing"
   │              pending_tasks empty, plan existed → "finishing"
   │              (no plan at all, e.g. non-planner flows) → None (no gating)
   │
   └─► get_tools_for_agent(agent_name, required_capabilities, phase=phase)
              │
              ▼
     tool_registry._observed_get_tools(provider, agent_name, capabilities, phase=phase)
              │
              ▼
     provider.get_tools(agent_name=..., capabilities=..., phase=phase)   ── try
              │                                                             │ TypeError
              ▼                                                             ▼
   CompositeToolProvider.get_tools(...)                     provider.get_tools(agent_name=..., capabilities=...)
              │                                              (phase omitted — fail-open, logged once)
              ▼
   if phase and phase_capability_map has an override for (agent_name, phase):
       effective_capabilities = intersect(capabilities, override)
   else:
       effective_capabilities = capabilities   (identical to today)
              │
              ▼
   delegate to live sub-providers with effective_capabilities (unchanged merge/
   dedupe/cap logic); stub fallback and fixed-tool-agent exemption unaffected
```

Phase gating is deliberately layered as **an additional narrowing of the existing Fase E `capabilities` filter**, not a new tool source or a second selection axis — it reuses `DEFAULT_CAPABILITY_MAP`'s existing mechanics end to end.

## 4. Design Decisions

### DD-LH-001: Compaction via `RemoveMessage`, never in-place mutation
`ContextCompactor` never constructs a new `list[BaseMessage]` and reassigns `state["messages"]`. It always returns a state update containing `RemoveMessage(id=...)` entries (and, in `summarize` mode, one new message). This is the only mechanism compatible with the `add_messages` reducer, and is why LH1 must run at a node boundary that returns a state update (the `supervisor_node` seam) rather than by mutating history inside a helper function.

### DD-LH-002: The system prompt is not `state["messages"]`'s problem
Every agent node builds its own `SystemMessage` fresh, per call, and prepends it to the LLM input outside of `state["messages"]`. `ContextCompactor` therefore never special-cases "message 0" — it only ever looks at the persisted human/ai/tool history. (This is a deliberate simplification versus the PLAN's original framing of "preserve the system prompt", which does not apply literally to this codebase's node structure.)

### DD-LH-003: Dual seam, primary + optional
The primary integration point is the `supervisor_node` per-turn seam (cross-turn, persisted-state compaction, mirroring `maybe_seed_budget_run`/`maybe_seed_hardening_run`). An optional `react_loop(..., context_compactor=None)` parameter additionally bounds a single node's local `loop_messages` accumulator during a long in-node ReAct session. See the `> Open question` in §3.2 — the local-loop hook may ship in a later minor if scope needs to shrink.

### DD-LH-004: Token threshold reuses Budget's `CostMeter`; no new tokenizer dependency
When `budget_enabled=True`, `ContextCompactor` reads `get_budget_guard(state).meter.usage.total_tokens` (cumulative, already metered) as an optional trigger alongside the default raw message-count threshold. When Budget is disabled, only the message-count threshold applies. No new token-counting library is introduced.

### DD-LH-005: Summarization is opt-in, off by default, metered like the Phase H injection classifier
`context_compaction_strategy` defaults to `"truncate"` (zero extra LLM calls). The `"summarize"` strategy costs exactly one LLM call per compaction event; when a `BudgetGuard` is present for the run, that call is recorded via `CostMeter.record_response()`, exactly mirroring how Phase H's optional `classifier_fn` is metered (DD-HRD, `runtime-hardening/ARCHITECTURE.md` §H2-02).

### DD-LH-006: `phase` is optional and call-compatibility is fail-open
`ToolProviderPort.get_tools()`'s new `phase` keyword defaults to `None`. Because `Protocol` structural typing does not enforce exact keyword signatures, the core (`tool_registry._observed_get_tools`, `CompositeToolProvider.get_tools`) wraps every call to a sub-provider in a `try: provider.get_tools(..., phase=phase) except TypeError: provider.get_tools(...)` shim whenever `phase is not None`, so a third-party provider that has not been updated for LH2 keeps working exactly as before (with gating silently skipped for that provider, logged once at `debug` level — never raised to the caller, matching `ToolProviderPort`'s existing "must not raise" contract).

### DD-LH-007: Phase narrowing is a capability-filter layer, not a new tool source
`CompositeToolProvider`'s `phase_capability_map: Mapping[str, Mapping[str, list[str]]] | None` (keyed `agent_name -> phase -> capabilities`) intersects with the caller-supplied `capabilities` before delegating to live sub-providers. It never adds tools that capability-filtering would otherwise exclude, and it never touches the stub fallback or the fixed-tool-agent (`cron_manager`, `critic`) exemption — those keep receiving their unfiltered static set exactly as today.

### DD-LH-008: Phase resolution is deterministic and free
`resolve_phase(state)` never calls an LLM. Precedence: (1) an explicit `state["metadata"]["loop"]["phase"]` string, settable by the supervisor or a planner node; (2) a deterministic fallback derived from `task_plan` / `pending_tasks` / `completed_tasks` (`"planning"` when no plan exists yet, `"executing"` while `pending_tasks` is non-empty, `"finishing"` once a plan existed and `pending_tasks` has drained); (3) `None` for flows with no plan at all (e.g. a bare researcher/coder turn with no `planner` involvement) — `None` means "no gating", identical to today's behavior.

### DD-LH-009: Opt-in, snapshot-guaranteed
Every wiring point is gated on its own flag (`context_compaction_enabled`, `tool_gating_enabled`). A snapshot test asserts the compiled supervisor graph is byte-for-byte identical with both off, and a tool-resolution contract test asserts `get_tools_for_agent(name)` (no `phase` argument, or `tool_gating_enabled=False`) returns the exact same list as before LH2 existed — mirroring the Skynet/Kokoro/Budget/Hardening precedent.

## 5. Security & cost

- Compaction summaries pass through the same `SecurePromptBuilder` discipline as any other LLM call built from prior conversation content — the summarized segment is prior *trusted* conversation history (not fresh untrusted input), but the summarizer prompt template itself must not be built by naive string concatenation of message contents (Critical Rule #1 applies to the summarizer prompt construction, not just to a single external input).
- The optional summarizer LLM call is metered through Budget and disabled by default (`truncate` strategy).
- Tool-gating denials are not security *denials* in the Phase H sense (no `ToolPolicyEngine`/`ActionInterceptor` bypass) — a narrowed tool set can still be widened back to the full set by disabling `tool_gating_enabled`; LH2 is a context/attack-surface hygiene measure, not a substitute for Phase H's declarative policy.
- Compaction/gating events are not currently specified as audit-logged (unlike Phase H's hash-first audit trail) because they do not gate an action — only observability counters are required (§6). `TASKS.md` leaves an explicit test task to confirm this is the right call before `enforce`-style semantics are ever considered for a future phase.

## 6. Observability

### 6.1 OTel counters (registered in `OTelManager`)
- `prismal.context_compactions_total{strategy}` (`strategy` ∈ `truncate|summarize`)
- `prismal.context_compaction_messages_dropped_total`
- `prismal.context_compaction_summarize_errors_total` (summarizer LLM failure — fails open to `truncate` for that event)
- `prismal.tool_gate_narrowed_total{agent}`
- `prismal.tool_gate_phase_resolved_total{agent,phase}`

### 6.2 Spans
- `prismal.loop.context_compact`, `prismal.loop.phase_resolve` (attached as attributes on the existing `prismal.tools.resolve` span rather than a new span, to avoid span-count inflation on the hot tool-resolution path).

## 7. Relationship to existing specs

- **`runtime-hardening/` (Phase H)** — `RunawayGuard` stops a thrashing loop; LH1/LH2 manage a *healthy* loop's context size and tool surface. Both share the "per-run registry + `state["metadata"]` marker" shape; LH1 additionally *reads* the Budget per-run `CostMeter` (via `get_budget_guard`) for its optional token trigger.
- **`cost-budget-governance/` (Phase C)** — LH1's optional `summarize` strategy is metered exactly like Phase H's optional injection classifier; LH1's token-based trigger is a *consumer* of Budget's `CostMeter`, not a new metering path.
- **`tool-provider-injection/` (Fase Y)** — LH2 extends `ToolProviderPort` and `CompositeToolProvider` in place; it does not introduce a new provider type or bypass the existing MCP→Skills→stubs priority, cap, or fixed-tool-agent exemption.
- **`agent-eval-harness/`** — the regression suite is the natural place to add a long-loop scenario proving compaction keeps a multi-hundred-turn Skynet/Debate run within its token ceiling without losing task-relevant context.

## 8. Testing strategy (summary; detail in `TASKS.md`)

- Unit: `ContextCompactor` threshold logic (message-count and token-count triggers); `RemoveMessage` state-update shape round-trips through `add_messages`; `keep_recent` verbatim boundary; `truncate` vs `summarize` strategy selection; summarizer-failure fail-open path.
- Unit: `resolve_phase()` precedence and each derivation branch; `CompositeToolProvider` phase-narrowing intersection logic; the `TypeError` fail-open shim against a deliberately non-conforming fake provider.
- Integration: `context_compaction_enabled=False` **and** `tool_gating_enabled=False` ⇒ compiled supervisor graph snapshot unchanged; a long simulated Skynet/Debate run stays under a configured message ceiling with compaction on; an agent's resolved tool list visibly narrows between a `"planning"`-phase call and an `"executing"`-phase call with a phase map configured.
- Guards: no provider import outside `providers/`; no `mcp`/`skills` import in `agents/**` (reuse existing AST tests — LH2 touches `tool_registry.py`/`extension/providers.py`, both already exempted or compliant).

## 9. Rollout

1. Ship modules behind `context_compaction_enabled=False` / `tool_gating_enabled=False` (no wiring change observable).
2. Enable `context_compaction_enabled` with `strategy="truncate"` in staging first (zero extra LLM cost); tune `context_compaction_max_messages` / `keep_recent` from real long-run traces.
3. Opt into `strategy="summarize"` per-deployment once truncation's information loss is judged unacceptable for a given flow.
4. Enable `tool_gating_enabled` with a minimal `tool_gating_phases.yaml` covering only `planner`-driven flows first (where `task_plan` gives a reliable phase signal); expand to phase-hint-driven flows (explicit `metadata["loop"]["phase"]`) once the supervisor/planner nodes are updated to set it.
