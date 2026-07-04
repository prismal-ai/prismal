# Prismal — Loop Hardening (Context Compaction, Dynamic Tool Gating)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | LH (Loop Hardening) |
| **Target package version** | `3.7.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, AI Architect, Security Lead |
| **Priority** | P2 (reliability / cost predictability on long-running loops) |
| **Related** | `docs/gap-analysis-loops-harness-guardrails-2026-07.md` (§2, item #4 and item #7 of the §5 table), `specs/runtime-hardening/` (`RunawayGuard`, the per-run registry pattern this phase imitates), `specs/cost-budget-governance/` (`budget/resolve.py::seed_budget_run`, the second style/pattern reference), `specs/tool-provider-injection/` (`ToolProviderPort`, the seam LH2 extends), `specs/agent-eval-harness/` (regression coverage for long-loop scenarios) |

---

## 1. Executive Summary

`docs/gap-analysis-loops-harness-guardrails-2026-07.md` (2026-07-04) compared Prismal's agentic-loop mechanics against 2026 harness-engineering practice and found Prismal's *pattern catalogue* (ReAct, Plan-Execute, Reflection, ToT, LATS, Debate, Mixture-of-Agents, LLM-Compiler, Swarm) unusually complete, but flagged two concrete gaps in the loop **mechanics** underneath those patterns (§2, and rows #4 and #7 of the §5 priority table):

1. **No context/message-window compaction.** `state["messages"]` grows without bound across a long-running task (Skynet rounds, Debate rounds, a long ReAct session) — nothing trims, truncates, or summarizes it. Verified directly against the code: `grep -rniE "compact|trim_messages|summariz" prismal/memory prismal/agents/graph.py` returns zero hits. The only existing mitigation is a **per-call, per-agent** windowing trick (`_HISTORY_WINDOW` slicing in `researcher.py`, `supervisor.py`, `domain_supervisor.py`, `codeact_agent.py`) that limits what is *sent to the LLM on one call* — it never trims or replaces what is *persisted* in `AgentState.messages`, so the checkpointed history and its token/cost footprint keep growing turn over turn.
2. **No dynamic tool provisioning by task phase.** `ToolProviderPort.get_tools(*, agent_name, capabilities)` (`prismal/agents/extension/ports.py`) and `tool_registry.get_tools_for_agent()` resolve a tool set **statically** for the whole run — by agent identity and a fixed capability list — with no notion of "where we are in the task" (planning vs. executing vs. finishing). The 2026 harness-engineering literature calls this class of technique "dynamic tool provisioning" (a coarser, tool-catalogue-level cousin of logits masking): narrowing the tool surface mid-task to cut context noise and reduce the blast radius of a bad tool call.

This feature — **Loop Hardening (Phase LH)** — closes both gaps as **opt-in, additive** extensions of the existing loop seams, following the exact precedent set by `RunawayGuard`/`BudgetGuard`: a per-run in-process registry keyed by `session_id`, a serializable marker under `state["metadata"]`, and a master `<feature>_enabled` flag defaulting to `False` so the compiled supervisor graph stays byte-for-byte unchanged until an operator opts in.

---

## 2. Context and Problem

- **Unbounded persisted history.** `AgentState.messages` (`prismal/agents/state.py`) uses the `add_messages` reducer, which only ever *appends*. Nothing in the codebase removes or condenses old entries. Long Skynet/Debate/ReAct sessions accumulate tool results, intermediate reasoning, and retries indefinitely; the only bound today is `budget_max_tokens`/`budget_max_calls` (Phase C, stops the *run*) and `hardening_runaway_max_steps` (Phase H, stops a *thrashing* loop) — neither trims what is already in state, so a healthy-but-long task still degrades context quality (older, more-relevant instructions get diluted by an ever-larger tail) and inflates every subsequent LLM call's prompt size and cost.
- **Ad-hoc, per-call windowing is not compaction.** The existing `_HISTORY_WINDOW` (6–8 messages) slices are a *local, ephemeral* view built fresh inside a handful of node functions immediately before an LLM call; they do not touch `state["messages"]`, are not shared across agents, and are not configurable via settings. They solve "don't blow the per-call token budget for this one node" but not "keep the run's persisted history bounded and coherent".
- **Tool surface is static for the life of a run.** `CompositeToolProvider` (`prismal/agents/extension/providers.py`) merges MCP → Skills → stubs once per `get_tools_for_agent()` call, filtered only by the Fase E `capabilities` list from `DEFAULT_CAPABILITY_MAP`. A `coder` agent mid-way through "write tests" still sees the same tool catalogue it saw during "scaffold the module" — more tools in context than the current sub-task needs, more surface for an errant or injected tool call, and no mechanism to narrow it without changing `agent_name` or shipping a new capability list per phase.
- **The building blocks already exist; they are just not wired for this.** `task_plan` / `pending_tasks` / `completed_tasks` (`AgentState`) already carry enough signal to derive "planning vs. executing vs. finishing" deterministically, and `state["metadata"]["loop"]` is an obvious, so-far-unused home for an explicit phase hint (mirroring `metadata["budget"]`, `metadata["hardening"]`, `metadata["skynet"]`, `metadata["kokoro"]`). `budget/usage.py::extract_token_usage()` and the Budget per-run `CostMeter` already track cumulative token usage per run when `budget_enabled` — a natural, zero-new-dependency signal for a compaction threshold.

> **Scope boundary vs. `runtime-hardening` (Phase H).** `RunawayGuard` stops a **thrashing** loop (step cap / stagnation) — it never touches message content. Loop Hardening (LH) is complementary: it manages the **size and shape** of a *healthy*, still-progressing loop's context, and narrows *which tools* are visible at a given moment. The two guards can both be active on the same run without overlap: RunawayGuard answers "should this loop keep going at all?"; LH1 answers "what does the model see once we decide it should?"; LH2 answers "which tools can it call right now?".

---

## 3. Target Users

- **AI Engineer / Flow Author:** wants long Skynet/Debate/ReAct/dev-pipeline runs to stay within a predictable prompt size and cost envelope without manually re-tuning `_HISTORY_WINDOW` constants per agent.
- **Security Lead:** wants a narrower tool surface available to an agent mid-task, reducing the blast radius of a compromised or hallucinated tool call (complements Phase H's `ToolPolicyEngine`, which is identity/args-based, not phase-based).
- **Platform Host (`prismal-server`):** wants the compaction/gating behavior to be fully opt-in and zero-risk to enable — the existing 26 agents must not change behavior unless the host explicitly turns the flags on.
- **FinOps / Operator:** wants a lever against unbounded prompt growth that is independent of (and composes with) the existing Budget hard/soft caps.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Bound persisted context growth | `state["messages"]` length/estimated tokens stay under a configurable ceiling across long runs | Configurable, enforced when enabled |
| Preserve loop correctness | Compaction never breaks the `add_messages` reducer contract or drops the most recent N turns | 100% (unit + integration tested) |
| Narrow tool surface by phase | An agent's resolved tool list shrinks (or reorders) when a phase hint narrows its capability filter | Observable via `prismal.tool_gate_narrowed_total{agent}` |
| Backward compatibility | `ToolProviderPort.get_tools()` gains an optional keyword only; third-party providers written against the current Protocol keep working unmodified | 100% (contract test) |
| Observability | OTel counters for compactions and tool-gate narrowing | Emitted |
| Opt-in safety | `context_compaction_enabled=False` and `tool_gating_enabled=False` (both default) ⇒ compiled supervisor graph and tool-resolution output are byte-for-byte unchanged | 100% (snapshot test) |

---

## 5. Scope (proposed)

### In Scope
- **LH1 — `ContextCompactor`** (`prismal/agents/context_compaction.py`): trims/summarizes `state["messages"]` when a configurable message-count or (when `budget_enabled`) cumulative-token threshold is exceeded, keeping the most recent N messages verbatim and either truncating (default) or LLM-summarizing (opt-in, metered via Budget) the older middle segment. Compaction is expressed as a state update using LangGraph `RemoveMessage` entries (plus, in `summarize` mode, one replacement message) — never in-place mutation of the history list.
- **LH2 — Dynamic tool gating by phase**: an optional `phase` keyword on `ToolProviderPort.get_tools()`, threaded through `tool_registry.get_tools_for_agent()` and `CompositeToolProvider`, that narrows the effective capability filter for the current phase (derived from an explicit `state["metadata"]["loop"]["phase"]` hint or, failing that, deterministically from `task_plan`/`pending_tasks`/`completed_tasks`). Framed as "dynamic tool provisioning" per the 2026 harness-engineering literature — a tool-catalogue-level technique, **not** literal token-logits masking.
- **LH3 — Integration, settings, tests, docs, examples**: wiring into the `supervisor_node` per-turn seam (LH1) and `tool_registry`/`CompositeToolProvider` (LH2), following the exact per-run-registry pattern of `budget/resolve.py` and `security/hardening_run.py`; new `context_compaction_*` / `tool_gating_*` settings; new `LoopHardeningError` exception hierarchy; new OTel counters; unit + integration tests including a snapshot test proving the disabled path is byte-for-byte unchanged; `docs/loop-hardening.md`; `examples/loop_hardening.py`; planned README/CHANGELOG entries.

### Out of Scope
- Literal token-level logits masking / constrained decoding (out of reach without provider-side support; LH2 only narrows the tool *catalogue* presented to the model).
- A general-purpose "verification step" primitive decoupled from reflection (gap-analysis §2, item #3 — PRAR-style explicit verifier) — tracked separately, not part of this phase.
- Crash-level sub-task resumability beyond the existing `AsyncSqliteSaver`/Postgres checkpointing (gap-analysis §2, item #4 in the prose — resumable harness) — tracked separately.
- Changing `_HISTORY_WINDOW` per-agent constants or removing the existing ad-hoc windowing — LH1 is additive; the ad-hoc windowing continues to bound a single LLM call's prompt regardless of whether persisted-history compaction is enabled.
- Re-implementing or replacing `RunawayGuard`, `BudgetGuard`, or `ToolPolicyEngine` — LH composes with all three at their existing seams.
- A tokenizer dependency for exact token counts — LH1's token-based trigger reuses the Budget layer's already-metered cumulative usage; a raw message-count trigger is the default, dependency-free path.

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-LH-001 | `ContextCompactor` trims `state["messages"]` via a reducer-safe state update (`RemoveMessage`), never in-place mutation | `MUST` |
| RF-LH-002 | Compaction preserves the most recent `context_compaction_keep_recent` messages verbatim | `MUST` |
| RF-LH-003 | `truncate` strategy (default) drops the older middle segment with no LLM call; `summarize` strategy (opt-in) replaces it with one LLM-generated summary message, metered via Budget when `budget_enabled` | `MUST` |
| RF-LH-004 | Compaction trigger is a configurable raw message-count threshold by default, or the Budget layer's cumulative token usage when `budget_enabled` | `MUST` |
| RF-LH-005 | `ToolProviderPort.get_tools()` gains an **optional** `phase: str \| None = None` keyword; omission or `None` reproduces today's behavior exactly | `MUST` |
| RF-LH-006 | `tool_registry.get_tools_for_agent()` / `get_tools_for_agent_ctx()` / `CompositeToolProvider.get_tools()` thread an optional phase hint into an additional capability-filter narrowing step | `MUST` |
| RF-LH-007 | Phase resolution: explicit `state["metadata"]["loop"]["phase"]` wins; else deterministic derivation from `task_plan`/`pending_tasks`/`completed_tasks`; no LLM call | `MUST` |
| RF-LH-008 | Calling a third-party `ToolProviderPort` implementation that does not declare `phase` in its signature never raises — the core fails open and omits the keyword | `MUST` |
| RF-LH-009 | `context_compaction_enabled=False` **and** `tool_gating_enabled=False` ⇒ compiled supervisor graph byte-for-byte unchanged; tool-resolution output byte-for-byte unchanged | `MUST` |
| RF-LH-010 | OTel counters for compactions and tool-gate narrowing | `SHOULD` |
| RF-LH-011 | No provider SDK import outside `providers/`; no `prismal.mcp`/`prismal.skills` import in `agents/**` (existing AST guards keep passing) | `MUST` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Compaction drops information the model still needs | `keep_recent` verbatim window; `summarize` strategy (opt-in) preserves gist instead of hard-dropping; `truncate` is the low-risk default |
| Compaction breaks the `add_messages` reducer / LangGraph message-id invariants | Use `RemoveMessage` exclusively for deletions (LangGraph's documented mechanism); never construct a new list and reassign in place; covered by a dedicated reducer-safety unit test |
| Summarizer LLM call adds latency/cost | Off by default (`truncate` is the default strategy); metered via Budget when enabled, mirroring the Phase H injection-classifier pattern |
| Re-summarizing the same segment every turn wastes calls | Per-run "compacted watermark" in the in-process registry (mirrors Budget/Hardening's per-run state) so a segment is only compacted once |
| `phase` keyword breaks third-party `ToolProviderPort` implementations | Keyword is optional with a `None` default; core wraps the call in a fail-open shim that omits `phase` entirely on `TypeError` from a non-conforming provider |
| Phase-based narrowing hides a tool the agent legitimately needs mid-phase | Narrowing is a capability *filter*, not a hard removal from the provider; misconfigured phase maps degrade to the unfiltered set via the same fail-open path used elsewhere in `tool_registry` |
| Behavior leak when both flags are off | Gate every wiring point on the respective `_enabled` flag; snapshot test (mirrors Phase C/H/S/K precedent) |

---

## 8. Dependencies

- `prismal/agents/state.py` (`AgentState.messages`, `task_plan`, `completed_tasks`, `metadata`).
- `prismal/agents/supervisor.py` (the `maybe_seed_budget_run` / `maybe_seed_hardening_run` seam — LH1's `maybe_seed_context_compaction_run` lands next to them).
- `prismal/agents/tool_registry.py` / `prismal/agents/extension/providers.py` / `prismal/agents/extension/ports.py::ToolProviderPort` (LH2's integration seam).
- `prismal/budget/resolve.py`, `prismal/budget/usage.py` (the per-run registry pattern and token-usage convention LH1 reuses).
- `prismal/security/runaway.py` (the closest existing analog for "loop mechanics hardening" and its per-run registry).
- `prismal/monitoring/otel.py` (counters).
- Proven by `specs/agent-eval-harness/` regression suite (long-loop scenarios).

---

## 9. Next Steps

Expand to `ARCHITECTURE.md` / `SPEC.md` / `TASKS.md` (this same directory). No implementation until the four SDD documents are reviewed — this PLAN and its siblings are a **proposal**, not a build order.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #4/#7) |
