# Prismal Loop Hardening — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | LH |
| **Target package version** | `3.7.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/loop-hardening/PLAN.md` |
| **SPEC** | `specs/loop-hardening/SPEC.md` |
| **Architecture** | `specs/loop-hardening/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Loop Hardening is planned in three phases (LH1–LH3), each independently testable and gated behind its own flag (`context_compaction_enabled`, `tool_gating_enabled`, both default `False`) so `main` stays green and the 26 agents are unaffected until the wiring phase. Reuses existing primitives wherever possible: the `budget/resolve.py` / `security/hardening_run.py` per-run-registry pattern, `BudgetGuard`/`CostMeter` for optional metering, `CompositeToolProvider`'s existing fail-open shape, and `OTelManager` for counters. No new LangGraph capability beyond `RemoveMessage` (already available in the pinned `langgraph`/`langchain-core` versions).

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. **All rows below are `TODO` — this is a proposal, nothing has been implemented yet.**

## 2. Prerequisites

- Reuse, do not fork: `budget/resolve.py` (per-run registry shape), `security/hardening_run.py` (second reference for the same shape), `security/runaway.py` (closest existing "loop mechanics" analog), `agents/tool_registry.py`, `agents/extension/providers.py::CompositeToolProvider`, `agents/extension/ports.py::ToolProviderPort`, `monitoring/otel.py`.
- Confirm (already verified during spec drafting, re-confirm at implementation start): `grep -rniE "compact|trim_messages|summariz" prismal/memory prismal/agents/graph.py` still returns zero hits (no upstream change quietly added compaction already).
- Confirm `langgraph`/`langchain-core` pinned versions (`>=0.2.66` / `>=0.3.28`) still support `RemoveMessage`-based deletion through `add_messages` (re-check on any dependency bump between now and implementation).
- Resolve the `> Open question` in `ARCHITECTURE.md` §3.2 (supervisor-seam-only vs. supervisor-seam + `react_loop` local-loop hook) **before** starting LH1-04 — it changes that task's scope.

## 3. Implementation Phases

### PHASE LH1 — Context compaction

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| LH1-01 | `core/config.py`: `context_compaction_*` settings (SPEC-LH-CFG-001) + `_validate_loop_hardening` | 0.3 d | — | TODO |
| LH1-02 | `core/exceptions.py`: `LoopHardeningError` hierarchy (`ContextCompactionError`, `ToolGatingConfigError`) (SPEC-LH-ERR-001) | 0.2 d | — | TODO |
| LH1-03 | `agents/context_compaction.py`: `CompactionStrategy`, `CompactionResult`, `ContextCompactor.should_compact`/`compact`/`to_state_update` (SPEC-LH-CTX-001/002) | 1.0 d | LH1-01 | TODO |
| LH1-04 | Per-run seeding trio: `maybe_seed_context_compaction_run`, `get_context_compactor`, `clear_context_compaction_run` + watermark bookkeeping (SPEC-LH-CTX-003) | 0.5 d | LH1-03 | TODO |
| LH1-05 | Wire into `supervisor_node` next to `maybe_seed_budget_run`/`maybe_seed_hardening_run`; fold the compaction state update into the turn's return value | 0.5 d | LH1-04 | TODO |
| LH1-06 | *(conditional on the open question)* Optional `react_loop(..., context_compactor=None)` local-loop hook | 0.6 d | LH1-03 | TODO |
| LH1-07 | Optional `summarize` strategy: default `summarizer_fn` wiring `ProviderRegistry().get_llm()`, metered via `BudgetGuard.meter.record_response` when present | 0.5 d | LH1-03 | TODO |

**Done when:** settings parse from `PRISMAL_*`; a synthetic 200-message history compacts to the expected shape under `truncate`; `add_messages` reducer round-trips a `RemoveMessage`-based state update without error; re-running the same turn does not re-compact the same segment (watermark honoured); `summarize` records exactly one metered call when a guard is present.

### PHASE LH2 — Dynamic tool gating by phase

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| LH2-01 | `agents/loop_phase.py`: `resolve_phase()` (SPEC-LH-PHS-001) | 0.4 d | — | TODO |
| LH2-02 | `agents/extension/ports.py`: add optional `phase` keyword to `ToolProviderPort.get_tools()` (SPEC-LH-GAT-001); update the Protocol docstring | 0.2 d | — | TODO |
| LH2-03 | `agents/extension/providers.py`: `CompositeToolProvider(..., phase_capability_map=...)` narrowing + fail-open `TypeError` shim per sub-provider (SPEC-LH-GAT-002) | 0.7 d | LH2-02 | TODO |
| LH2-04 | `load_phase_capability_map()` + `config/tool_gating_phases.yaml` default (ship example from this spec dir) | 0.3 d | LH2-03 | TODO |
| LH2-05 | `agents/tool_registry.py`: thread optional `phase` through `get_tools_for_agent()`, `get_tools_for_agent_ctx()`, `_observed_get_tools()`, with the DD-LH-006 fail-open shim centralized at this choke point (SPEC-LH-GAT-003) | 0.6 d | LH2-02 | TODO |
| LH2-06 | `core/config.py`: `tool_gating_*` settings (part of SPEC-LH-CFG-001) | 0.2 d | — | TODO |

**Done when:** `isinstance(FakeToolProvider(), ToolProviderPort)` still holds; `get_tools_for_agent("coder")` (no `phase`) is byte-for-byte unchanged; a phase-mapped agent's resolved tool list visibly narrows for a configured `(agent, phase)` pair; a fake two-keyword sub-provider does not raise when called with `phase` set.

### PHASE LH3 — Integration, observability, tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| LH3-01 | `monitoring/otel.py`: register the 5 loop-hardening counters (SPEC-LH-OTEL-001) | 0.3 d | LH1,LH2 | TODO |
| LH3-02 | Unit: `ContextCompactor` threshold logic (message-count + token-count triggers), `to_state_update()` shape, watermark idempotency | 0.6 d | LH1 | TODO |
| LH3-03 | Unit: `RemoveMessage` state update round-trips through `add_messages` without breaking reducer semantics | 0.4 d | LH1 | TODO |
| LH3-04 | Unit: `resolve_phase()` — all derivation branches + explicit-hint precedence | 0.4 d | LH2 | TODO |
| LH3-05 | Unit: `CompositeToolProvider` phase-narrowing intersection + `TypeError` fail-open shim against a deliberately non-conforming fake provider | 0.5 d | LH2 | TODO |
| LH3-06 | Integration: `context_compaction_enabled=False` **and** `tool_gating_enabled=False` ⇒ compiled supervisor graph snapshot unchanged | 0.4 d | LH1,LH2 | TODO |
| LH3-07 | Integration: `get_tools_for_agent()` output unchanged (contract test) when `tool_gating_enabled=False` or `phase` omitted | 0.3 d | LH2 | TODO |
| LH3-08 | Integration: simulated long Skynet/Debate run stays under a configured message ceiling with compaction on (fakes only, no live LLM) | 0.6 d | LH1 | TODO |
| LH3-09 | Integration: an agent's resolved tool list narrows between a `"planning"`-phase call and an `"executing"`-phase call with a phase map configured (fakes only) | 0.4 d | LH2 | TODO |
| LH3-10 | AST guards: confirm `test_no_mcp_skills_imports.py` and the provider-import guard still pass unmodified after LH2's edits to `tool_registry.py`/`extension/providers.py` | 0.2 d | LH2 | TODO |
| LH3-11 | `docs/loop-hardening.md` user guide | 0.4 d | LH1,LH2 | TODO |
| LH3-12 | `examples/loop_hardening.py` runnable example | 0.3 d | LH1,LH2 | TODO |
| LH3-13 | `README.md` + `CHANGELOG.md` entries (as **planned**, not shipped — do not mark `IMPLEMENTED`) | 0.2 d | LH3-11 | TODO |

**Done when:** `uv run pytest -m unit` green for all new modules; `ruff`, `mypy --strict`, `bandit` clean; coverage ≥ project target (80%) on new modules; both snapshot/contract tests (LH3-06/07) pass with both flags off.

## 4. Test Inventory (planned)

| Test module | Covers |
|---|---|
| `tests/unit/core/test_config_loop_hardening.py` | LH1-01, LH2-06 |
| `tests/unit/core/test_exceptions_loop_hardening.py` | LH1-02 |
| `tests/unit/agents/test_context_compaction.py` | LH1-03, LH3-02, LH3-03 |
| `tests/unit/agents/test_context_compaction_run.py` | LH1-04, LH1-05 |
| `tests/unit/agents/test_react_loop_context_compaction.py` | LH1-06 (conditional) |
| `tests/unit/agents/test_context_compaction_summarize.py` | LH1-07 |
| `tests/unit/agents/test_loop_phase.py` | LH2-01, LH3-04 |
| `tests/unit/agents/extension/test_tool_provider_port_phase.py` | LH2-02 |
| `tests/unit/agents/extension/test_composite_provider_phase_gating.py` | LH2-03, LH2-04, LH3-05 |
| `tests/unit/agents/test_tool_registry_phase.py` | LH2-05, LH3-07 |
| `tests/unit/agents/test_graph_snapshot_loop_hardening.py` | LH3-06 |
| `tests/integration/agents/test_loop_hardening_long_run.py` | LH3-08, LH3-09 |
| `tests/unit/agents/extension/test_no_mcp_skills_imports.py` | LH3-10 (existing test, re-run for regression) |

## 5. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Compaction silently drops task-relevant context | `keep_recent` verbatim tail; `summarize` opt-in preserves gist; long-run integration test (LH3-08) checks task completion, not just message count |
| `RemoveMessage` semantics change in a future `langgraph` upgrade | Pin-tested at LH3-03; re-verify on every `langgraph`/`langchain-core` version bump |
| `phase` keyword breaks an undiscovered internal caller of a `ToolProviderPort` implementation | Centralize the fail-open shim at the single `_observed_get_tools` choke point (LH2-05) rather than duplicating it per call site |
| Scope creep on the `react_loop` local-loop hook (LH1-06) | Explicitly conditional on resolving the `ARCHITECTURE.md` open question first; may slip to a later minor without blocking LH1-01..05/LH2 |
| Behavior leak when both flags are off | Gate every wiring point on its own flag; snapshot + contract tests (LH3-06/07) |

## 6. Definition of Done (feature)

- [ ] All MUST requirements (RF-LH-001…009, 011) implemented and tested; RF-LH-010 (SHOULD) implemented.
- [ ] `state["messages"]` compaction is proven reducer-safe (`RemoveMessage`-only deletions) by a dedicated test.
- [ ] Tool-gating narrowing is proven both to narrow when configured and to no-op when a provider doesn't support `phase`.
- [ ] With `context_compaction_enabled=False` and `tool_gating_enabled=False`, zero behavior change (snapshot + contract tests proven).
- [ ] No provider SDK / `prismal.mcp` / `prismal.skills` import in the wrong layer (existing AST guards still pass).
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit suite green.
- [ ] `docs/loop-hardening.md` cross-references the gap-analysis motivation and the `runtime-hardening`/`cost-budget-governance` precedents this phase imitates.

## 7. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| LH1 | Context compaction | ~3.6 d |
| LH2 | Dynamic tool gating by phase | ~2.4 d |
| LH3 | Integration, observability, tests, docs, packaging | ~4.6 d |
| **Total** | | **~10.6 d** |
