# Prismal Blind Review Pipeline — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-10 |
| **Phase** | BRP |
| **Target package version** | `3.11.0` |
| **PLAN** | `specs/blind-review-pipeline/PLAN.md` |
| **SPEC** | `specs/blind-review-pipeline/SPEC.md` |
| **Architecture** | `specs/blind-review-pipeline/ARCHITECTURE.md` |

---

## 1. Implementation Summary

BRP is delivered in six phases (BRP1–BRP6), each independently testable and
landing behind `settings.blind_review_pipeline_enabled` (default `False`) so
`main` stays green and the existing 26+ agents are unaffected until the
final wiring phase (BRP5). Every component uses callable injection, so all
unit tests run without an LLM backend. BRP composes existing primitives
(`SubgraphFactory`, `gates.py`, `ProviderRegistry`, `ToolProviderPort`,
`code_review/types.py`) — no new LangGraph capability.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. **Nothing implemented
yet — this document is the TDD implementation plan requested alongside the
PLAN/ARCHITECTURE/SPEC.**

## 2. TDD Workflow (applies to every task below)

Every task in this document follows strict **red → green → refactor**,
matching the repo's existing test conventions
(`uv run pytest` with `asyncio_mode=auto`, `filterwarnings=error`,
`--cov=prismal --cov-report=term-missing`, `fail_under=80`):

1. **Red.** Write the unit test(s) named in the task's `Test(s) first`
   column against the not-yet-existing (or not-yet-correct) code. Run
   `uv run pytest <path> -m unit` and confirm it fails for the *expected*
   reason (import error / assertion failure), not a typo.
2. **Green.** Write the minimal implementation to make that test pass
   without breaking any previously-green test in this phase or earlier
   phases. Run the full `tests/unit/agents/subgraphs/blind_review_pipeline/`
   directory, not just the new file.
3. **Refactor.** Clean up under green tests; run `uv run ruff check --fix .`,
   `uv run ruff format .`, `uv run mypy prismal` (strict), and
   `uv run bandit -r prismal -c pyproject.toml` before marking the task
   `DONE`.

No task is marked `DONE` until its test file exists, is green, and the
quality gate (ruff/mypy --strict/bandit) is clean for the files it touches.

## 3. Prerequisites

- Branch off `main` (or the repo's current feature-branch convention).
- Reuse, do not modify: `agents/subgraphs/factory.py`,
  `agents/subgraphs/gates.py`, `agents/subgraphs/code_review/types.py`,
  `agents/extension/providers.py`, `providers/registry.py`,
  `security/prompt_builder.py`, `security/action_interceptor.py`,
  `security/audit.py`, `agents/subgraphs/registry.py`,
  `agents/intent_router.py`.
- Confirm `tests/unit/agents/extension/test_no_mcp_skills_imports.py`'s AST
  approach (the pattern BRP3-04 extends) still passes on current `main`
  before branching, so any later failure is attributable to BRP changes.
- Read `agents/subgraphs/dev_pipeline/builder.py` and
  `agents/subgraphs/code_review/builder.py` side by side before starting
  BRP4 — the new builder should read as a natural blend of both.

## 4. Implementation Phases

### PHASE BRP1 — Types, exceptions, settings

| ID | Task | Test(s) first | Estimate | Dependency | Status |
|---|---|---|---|---|---|
| BRP1-01 | `core/exceptions.py`: `BlindReviewPipelineError` hierarchy (SPEC-BRP-ERR-001) | `tests/unit/core/test_exceptions.py::test_blind_review_error_hierarchy` | 0.2 d | — | TODO |
| BRP1-02 | `core/config.py`: `blind_review_*` settings fields (SPEC-BRP-CFG-001) | `tests/unit/core/test_config.py::test_blind_review_settings_defaults` | 0.3 d | BRP1-01 | TODO |
| BRP1-03 | `_validate_blind_review`: threshold range, iteration floor, same-model `WARNING` | `tests/unit/core/test_config.py::test_blind_review_validation_{threshold,iterations,same_model_warns}` | 0.4 d | BRP1-02 | TODO |
| BRP1-04 | `blind_review_pipeline/synthesis.py`: `SynthesisResult` dataclass (SPEC-BRP-TYP-001) | `tests/unit/agents/subgraphs/blind_review_pipeline/test_synthesis.py::test_synthesis_result_roundtrip` | 0.2 d | — | TODO |

**Done when:** settings parse from `PRISMAL_BLIND_REVIEW_*` env vars;
out-of-range threshold/iterations raise `BlindReviewConfigError`; a
same-model reviewer pair logs a `WARNING` and does not raise;
`SynthesisResult` round-trips through equality/repr.

### PHASE BRP2 — `spec_agent` + `implementer_agent`

| ID | Task | Test(s) first | Estimate | Dependency | Status |
|---|---|---|---|---|---|
| BRP2-01 | `make_spec_agent_node()` with injected `spec_fn` (SPEC-BRP-SPEC-001) | `tests/unit/agents/subgraphs/blind_review_pipeline/test_spec_agent.py::test_spec_agent_writes_spec_artifact` | 0.4 d | BRP1 | TODO |
| BRP2-02 | Default `spec_fn` lazily wires `ProviderRegistry(settings).get_llm(blind_review_spec_model)` + `ToolProviderPort` | `test_spec_agent.py::test_default_spec_fn_resolves_configured_model_and_tools` (fake provider/registry) | 0.4 d | BRP2-01 | TODO |
| BRP2-03 | `make_implementer_agent_node()` reads only `spec_artifact` (+ prior issues) (SPEC-BRP-IMPL-001) | `tests/unit/agents/subgraphs/blind_review_pipeline/test_implementer_agent.py::test_implementer_reads_spec_only` | 0.5 d | BRP1 | TODO |
| BRP2-04 | `implementer_agent_node` calls `ActionInterceptor.check()` before file/code actions | `test_implementer_agent.py::test_implementer_calls_action_interceptor` (spy) | 0.3 d | BRP2-03 | TODO |
| BRP2-05 | Retry path: prior `synthesis.report.issues` passed to `implementer_fn`, not raw reviewer prose | `test_implementer_agent.py::test_implementer_retry_receives_structured_issues` | 0.3 d | BRP2-03 | TODO |

**Done when:** `spec_agent_node` output lands at
`state["metadata"]["blind_review"]["spec_artifact"]`; a test asserts
`implementer_agent_node`'s injected `implementer_fn` is called with exactly
`(spec_artifact, prior_issues_or_None)` — never with `state["messages"]` —
by constructing a fake `implementer_fn` that raises if it receives anything
else via a strict-signature spy.

### PHASE BRP3 — Blind reviewer nodes (the core invariant)

| ID | Task | Test(s) first | Estimate | Dependency | Status |
|---|---|---|---|---|---|
| BRP3-01 | `make_reviewer_node(role, model_id, capabilities, reviewer_fn)` (SPEC-BRP-REV-001) | `tests/unit/agents/subgraphs/blind_review_pipeline/test_reviewer_node.py::test_reviewer_node_reads_spec_and_artifact_only` | 0.5 d | BRP1 | TODO |
| BRP3-02 | `_extract_blind_context(state)` private helper — the only state accessor the node body uses | `test_reviewer_node.py::test_extract_blind_context_ignores_messages_key` (state fixture with populated `messages`) | 0.3 d | BRP3-01 | TODO |
| BRP3-03 | `BlindnessGuard.assert_no_message_leak()` runtime check | `tests/unit/agents/subgraphs/blind_review_pipeline/test_blindness_guard.py::test_guard_{raises_on_leak,passes_clean_text}` | 0.4 d | BRP3-01 | TODO |
| BRP3-04 | AST guard test: `reviewer_node.py` never references `state["messages"]` / `state.get("messages"` | `tests/unit/agents/subgraphs/blind_review_pipeline/test_reviewer_blindness_guard.py::test_reviewer_module_never_reads_messages` (write this test FIRST against the not-yet-written module; it must fail with "module not found", then pass once BRP3-01 lands clean) | 0.4 d | BRP3-01 | TODO |
| BRP3-05 | Default `reviewer_fn` resolves per-role `ProviderRegistry`/`ToolProviderPort` (`agent_name=role`) | `test_reviewer_node.py::test_default_reviewer_fn_uses_role_scoped_provider_and_tools` | 0.4 d | BRP3-01 | TODO |
| BRP3-06 | `reviewer_a` and `reviewer_b` nodes never read each other's verdict field | `test_reviewer_node.py::test_reviewer_a_does_not_read_reviewer_b_verdict` | 0.3 d | BRP3-01 | TODO |

**Done when:** BRP3-04's AST test is the load-bearing proof of RF-BRP-04 —
CI fails immediately if a future edit adds `state["messages"]` (or
`state.get("messages")`) anywhere inside `reviewer_node.py`. BRP3-03's
runtime guard is proven by a fixture that deliberately builds a "leaky"
prompt string (containing `"HumanMessage("`-shaped content) and asserts
`BlindReviewBlindnessViolationError` is raised before any LLM call happens
(assert the fake LLM's `ainvoke` was never called).

### PHASE BRP4 — Synthesis + subgraph topology

| ID | Task | Test(s) first | Estimate | Dependency | Status |
|---|---|---|---|---|---|
| BRP4-01 | `synthesize_verdicts()`: deterministic merge, no LLM call (SPEC-BRP-SYN-001) | `test_synthesis.py::test_synthesize_{min_score,union_issues_deduped,agreement_flag}` | 0.5 d | BRP1-04 | TODO |
| BRP4-02 | `score_gate(field="blind_review.synthesis.report.score", ...)` wiring (reused, unmodified) | `tests/unit/agents/subgraphs/blind_review_pipeline/test_builder.py::test_score_gate_routes_{pass,fail}` | 0.3 d | BRP4-01 | TODO |
| BRP4-03 | Static two-way fan-out edges `implementer -> {reviewer_a, reviewer_b}` (DD-BRP-005) | `test_builder.py::test_both_reviewers_run_before_synthesis` | 0.4 d | BRP2, BRP3 | TODO |
| BRP4-04 | HITL trio reused verbatim (`seed_hitl_metadata`/`human_approval_node`/`hitl_gate`) | `test_builder.py::test_hitl_bypassed_when_disabled` + `test_hitl_interrupt_raised_when_enabled` | 0.4 d | BRP4-02 | TODO |
| BRP4-05 | `build_blind_review_pipeline_subgraph()` (SPEC-BRP-SUB-001) | `test_builder.py::test_build_subgraph_topology_matches_spec` | 0.5 d | BRP4-01..04 | TODO |
| BRP4-06 | Idempotent `register_blind_review_pipeline()` | `test_builder.py::test_register_is_idempotent` | 0.3 d | BRP4-05 | TODO |

**Done when:** the subgraph runs end-to-end with injected fakes (no LLM
backend); a failing synthesis routes back to `implementer_agent` and
`iteration_count` increments; after `blind_review_max_iterations` the gate
force-passes (same guard behavior as `score_gate`'s existing
`max_iterations` semantics); no `prismal.mcp`/`prismal.skills` import
anywhere in `agents/subgraphs/blind_review_pipeline/` (extend the existing
AST guard's target-module list — see BRP6-04).

### PHASE BRP5 — Supervisor + intent integration (the only behavior-changing phase)

| ID | Task | Test(s) first | Estimate | Dependency | Status |
|---|---|---|---|---|---|
| BRP5-01 | `intent_router.match_intent()` returns `blind_review_pipeline` for review-panel intents, gated on the flag | `tests/unit/agents/test_intent_router.py::test_blind_review_intent_{matches_when_enabled,ignored_when_disabled}` | 0.3 d | BRP4 | TODO |
| BRP5-02 | `get_async_compiled_graph()` wires the route when `blind_review_pipeline_enabled` | `tests/unit/agents/test_graph.py::test_blind_review_route_wired_when_enabled` | 0.4 d | BRP4 | TODO |
| BRP5-03 | `effective_valid_routes` / `build_system_prompt` gate on the flag | `tests/unit/agents/test_graph.py::test_blind_review_absent_from_prompt_when_disabled` | 0.3 d | BRP5-02 | TODO |
| BRP5-04 | **Snapshot test: graph unchanged when disabled** | `tests/unit/agents/test_graph_snapshot.py::test_graph_snapshot_unchanged_with_blind_review_disabled` — write this FIRST against current `main`'s snapshot, confirm green pre-BRP, then re-run post-BRP5-02 to prove zero drift | 0.4 d | BRP5-02 | TODO |

**Done when:** with `blind_review_pipeline_enabled=False` the compiled-graph
snapshot is byte-for-byte identical to pre-BRP `main`; with `True`, a
review-panel intent routes to `blind_review_pipeline` end-to-end.

### PHASE BRP6 — Tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| BRP6-01 | Unit tests: settings validation edge cases (threshold bounds, same-model warning) | 0.3 d | BRP1 | TODO |
| BRP6-02 | Unit tests: spec/implementer input-contract isolation (strict-signature spies) | 0.4 d | BRP2 | TODO |
| BRP6-03 | Unit tests: reviewer blindness (structural + AST + runtime guard, all three layers) | 0.5 d | BRP3 | TODO |
| BRP6-04 | Extend `test_no_mcp_skills_imports.py`'s target modules to include `agents/subgraphs/blind_review_pipeline/**` | 0.2 d | BRP4 | TODO |
| BRP6-05 | Unit tests: synthesis merge (score/dedupe/agreement) | 0.3 d | BRP4 | TODO |
| BRP6-06 | Unit tests: subgraph end-to-end with fakes (pass path, fail-then-retry path, max-iterations force-pass) | 0.6 d | BRP4 | TODO |
| BRP6-07 | Integration test: graph snapshot unchanged when disabled (BRP5-04, promoted to the integration tier) | 0.3 d | BRP5 | TODO |
| BRP6-08 | `docs/blind-review-pipeline.md` + `examples/blind_review_pipeline.py` | 0.5 d | BRP5 | TODO |
| BRP6-09 | `README.md` + `CHANGELOG.md` entries; `specs/roadmap.md` status flip to `IMPLEMENTED` | 0.2 d | BRP5 | TODO |

**Done when:** `uv run pytest -m unit` green; `uv run ruff check .` and
`uv run ruff format --check .` clean; `uv run mypy prismal` (strict) clean;
`uv run bandit -r prismal -c pyproject.toml` clean; coverage on the new
`blind_review_pipeline/` package ≥ the project's 80% floor.

## 5. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| A future edit accidentally leaks `state["messages"]` into a reviewer prompt | BRP3-04 AST guard (CI-blocking) + BRP3-03 runtime `BlindnessGuard` (defense in depth) |
| Cost/latency multiplication (4 calls × up to `blind_review_max_iterations`) | Hard iteration cap (BRP1-03); optional `budget_guard_fn` wiring (BRP6 follow-up, mirrors `debate_round`/`MixtureOfAgents`) |
| Non-terminating correction loop | Reused `score_gate` `max_iterations` force-pass semantics (BRP4-02) |
| Behavior leak when disabled | Gate every wiring point on `blind_review_pipeline_enabled`; snapshot test (BRP5-04/BRP6-07) |
| Reviewer verdicts diverge with no resolution | Deterministic `synthesize_verdicts()` (BRP4-01); `agreement` flag surfaced for observability, not blocking |
| New package accidentally imports `prismal.mcp`/`prismal.skills` | BRP6-04 extends the existing AST guard's coverage before merge |

## 6. Definition of Done (feature)

- [ ] All MUST requirements (RF-BRP-01…RF-BRP-14) implemented and covered by
      a test written before its implementation.
- [ ] A goal run end-to-end produces a spec, an implementation, two
      independent blind verdicts, a deterministic synthesis, and either an
      approval (optionally HITL-gated) or a bounded correction loop.
- [ ] The AST guard (BRP3-04) and runtime guard (BRP3-03) both exist and are
      exercised by a failing-then-passing test pair.
- [ ] With `blind_review_pipeline_enabled=False`, zero behavior change
      (snapshot proven, BRP5-04).
- [ ] No provider SDK or `prismal.mcp`/`prismal.skills` import inside
      `agents/subgraphs/blind_review_pipeline/`.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit suite green;
      coverage ≥ 80% on the new package.
- [ ] `PLAN`/`SPEC`/`ARCHITECTURE`/`TASKS` flipped to `IMPLEMENTED`;
      `README`/`CHANGELOG`/`specs/roadmap.md` updated.

## 7. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| BRP1 | Types + exceptions + settings | ~1.1 d |
| BRP2 | `spec_agent` + `implementer_agent` | ~1.9 d |
| BRP3 | Blind reviewer nodes (core invariant) | ~2.3 d |
| BRP4 | Synthesis + subgraph topology | ~2.4 d |
| BRP5 | Supervisor + intent integration | ~1.4 d |
| BRP6 | Tests + docs + packaging | ~3.3 d |
| **Total** | | **~12.4 d** |
