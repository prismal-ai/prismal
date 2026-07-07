# Prismal Node I/O Type-Safety — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | NTS |
| **Target package version** | `3.8.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/node-io-typesafety/PLAN.md` |
| **SPEC** | `specs/node-io-typesafety/SPEC.md` |
| **Architecture** | `specs/node-io-typesafety/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Node I/O Type-Safety lands in four phases (NTS1–NTS4), each independently testable and gated behind `settings.node_typesafety_enabled` (default `False`) so `main` stays green and none of the 26+ existing agents are affected until an operator opts a specific node in. Every control honours `mode ∈ {off, warn, enforce}` and reuses existing primitives (`@prismal_node`'s `DEFAULT_MIDDLEWARE_STACK`, `PrismalStateGraphBuilder`, the existing `NodeValidationError` stub, `OTelManager`). No new LangGraph capability and no change to `AgentState` itself.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. **IMPLEMENTED 2026-07-05 (v3.8.0) — all NTS1–NTS4 rows are `DONE`.** Resolved open questions: `NodeIOValidatorPort` **deferred** (DD-NTS-004), **global-only** mode (no per-node override) for v1, and the **3** mandatory pilots only (`file_manager`, `cron_manager`, `skill_manager`).

**Implementation deviations from the SPEC text (keep in mind when editing):**
- The pilot nodes were **bare** (added to a raw `StateGraph`, not `@prismal_node`-wrapped). Decorating them uses `security="off", audit=False` and no `capabilities` to keep behaviour as close to the undecorated version as possible (only the opt-in, default-off validation layer is genuinely new). Their real return dicts are `{current_agent, messages}`, so `FileManagerOutput` etc. declare exactly those two keys (the SPEC's illustrative example added a `metadata` key the real nodes never return).
- `enforce`-mode raises `NodeValidationError` with a **non-`None` cause** (`ValueError` of the joined schema errors), not `cause=None` as the SPEC pseudocode showed: `error_mapping_middleware` maps `type(cause).__name__`/`str(cause)`, so a `None` cause would produce a useless `"NoneType"`/`"None"` payload. The class still *accepts* `None` (SPEC-NTS-ERR-001); the middleware just supplies a useful one. Reconciles the SPEC's internally-inconsistent Flow NTS-B.
- The graph-snapshot test lives in **`tests/unit/agents/test_graph_snapshot_node_typesafety.py`** (not `tests/integration/`): `tests/integration/conftest.py` stubs `prismal.agents.graph`, so a real-graph snapshot cannot run there — every sibling snapshot test is a unit test. The end-to-end warn/enforce test is `tests/unit/agents/extension/test_node_io_validation_e2e.py` for the same reason (the integration conftest stubs the agents layer).

## 2. Prerequisites

- Reuse, do not fork: `agents/extension/decorators.py::prismal_node`/`NodeMetadata`, `agents/extension/_middleware.py::DEFAULT_MIDDLEWARE_STACK`, `agents/extension/builder.py::PrismalStateGraphBuilder`, `core/exceptions.py::NodeValidationError` (existing stub), `core/config.py`'s `hardening_mode`/`_validate_hardening` as the settings idiom to copy, `monitoring/otel.py::_register_standard_metrics`.
- Confirm the exact current order of `DEFAULT_MIDDLEWARE_STACK` (`error_mapping → otel → logger → security → audit → retry → timeout → hardening`) before appending — `node_io_validation_middleware` MUST land as the new innermost entry, one layer inside `hardening_middleware`.
- Confirm `NodeValidationError` has no existing call sites (it is currently an unraised stub) before extending its `__init__` signature, to avoid breaking a hidden caller.
- Read `prismal/agents/file_manager.py`, `prismal/agents/cron_manager.py`, `prismal/agents/skill_manager.py` in full (the NTS3 pilot set) before drafting their `input_model`/`output_model`.

## 3. Implementation Phases

### PHASE NTS1 — `NodeIOSchema` contract types

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| NTS1-01 | `agents/extension/node_schema.py`: `NodeIOMode`, `NodeIODirection`, `NodeIOValidationResult` (SPEC-NTS-TYP-001) | 0.3 d | — | TODO |
| NTS1-02 | `agents/extension/node_schema.py`: `validate_node_input()` — narrow-projection `model_validate`, never raises, `None`-model shortcut | 0.4 d | NTS1-01 | TODO |
| NTS1-03 | `agents/extension/node_schema.py`: `validate_node_output()` — mirrors NTS1-02 for `state_update` dicts | 0.3 d | NTS1-01 | TODO |
| NTS1-04 | `agents/extension/decorators.py`: `NodeMetadata` gains `input_model`/`output_model: type[BaseModel] \| None = None` (SPEC-NTS-TYP-002) | 0.2 d | — | TODO |
| NTS1-05 | `agents/extension/decorators.py`: `prismal_node()` gains `input_model=`/`output_model=` kwargs, threaded into `NodeMetadata` construction | 0.3 d | NTS1-04 | TODO |

**Done when:** `validate_node_input`/`validate_node_output` round-trip a valid payload with `ok=True, errors=[]`; a malformed payload returns `ok=False` with field-name-only messages (never values); `NodeMetadata`/`prismal_node()` accept the two new parameters with `None` defaults and every pre-existing `@prismal_node(...)` call site in the repo still type-checks and behaves identically.

### PHASE NTS2 — Middleware + builder wiring

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| NTS2-01 | `core/config.py`: `node_typesafety_enabled`/`node_typesafety_mode` settings + `_validate_node_typesafety` (SPEC-NTS-CFG-001) | 0.3 d | — | TODO |
| NTS2-02 | `core/exceptions.py`: extend the existing `NodeValidationError` stub with `direction`/`schema_errors` + custom `__init__` (SPEC-NTS-ERR-001) | 0.3 d | — | TODO |
| NTS2-03 | `agents/extension/_middleware.py`: `node_io_validation_middleware` — input check, `next_fn` call, output check, `_observe()` helper (SPEC-NTS-MDW-001) | 0.6 d | NTS1, NTS2-01, NTS2-02 | TODO |
| NTS2-04 | Append `node_io_validation_middleware` as the new innermost entry of `DEFAULT_MIDDLEWARE_STACK`, one layer inside `hardening_middleware` | 0.2 d | NTS2-03 | TODO |
| NTS2-05 | `agents/extension/builder.py`: `PrismalStateGraphBuilder.add_node()` gains `input_model=`/`output_model=` kwargs, forwarded on auto-wrap only (SPEC-NTS-BLD-001) | 0.3 d | NTS1-05 | TODO |

**Done when:** with `node_typesafety_enabled=False`, `node_io_validation_middleware` is provably a no-op (unit test asserts `next_fn` is called with the unmodified `state` and its return value is passed through unmodified); with `True` + `warn`, a malformed output logs + increments a counter and still returns the (malformed) `state_update` unchanged; with `True` + `enforce`, the same malformed output raises `NodeValidationError`, caught by the existing `error_mapping_middleware` with zero changes to that middleware.

### PHASE NTS3 — Incremental adoption pilot + `AgentState` design decision

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| NTS3-01 | Draft and document `DD-NTS-003` (`AgentState` stays unmodified; models are boundary projections) — already captured in `ARCHITECTURE.md`, confirm no code changes needed to `agents/state.py` | 0.1 d | — | TODO |
| NTS3-02 | Annotate `prismal/agents/file_manager.py::file_manager_node` with `FileManagerInput`/`FileManagerOutput` | 0.4 d | NTS2 | TODO |
| NTS3-03 | Annotate `prismal/agents/cron_manager.py`'s node function with `CronManagerInput`/`CronManagerOutput` | 0.4 d | NTS2 | TODO |
| NTS3-04 | Annotate `prismal/agents/skill_manager.py`'s node function with `SkillManagerInput`/`SkillManagerOutput` | 0.4 d | NTS2 | TODO |
| NTS3-05 | `tests/unit/core/test_node_io_schema_field_names.py`: assert every pilot model's declared fields are a subset of `AgentState`'s `TypedDict` keys via `get_type_hints()` | 0.4 d | NTS3-02, NTS3-03, NTS3-04 | TODO |

**Done when:** the 3 pilot nodes carry real `input_model`/`output_model` declarations that pass `NTS3-05`'s drift guard; `agents/state.py` is untouched; `DD-NTS-003`'s rationale is captured in `ARCHITECTURE.md` (already drafted) with no follow-up code required for v1.

### PHASE NTS4 — Settings integration, OTel, tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| NTS4-01 | `monitoring/otel.py`: register `node_io_validation_failures_total` + `node_io_validated_total` (SPEC-NTS-OTEL-001) | 0.2 d | NTS2-03 | TODO |
| NTS4-02 | Unit: `test_node_schema.py` — `validate_node_input`/`validate_node_output`, `None`-model shortcut, error-shape (no leaked values) | 0.5 d | NTS1 | TODO |
| NTS4-03 | Unit: `test_decorators_io_models.py` + `test_builder_io_models.py` — kwarg plumbing, forwarding-only-on-auto-wrap behavior | 0.4 d | NTS1, NTS2-05 | TODO |
| NTS4-04 | Unit: `test_middleware_node_io_validation.py` — off/warn/enforce dispatch, `_observe()` counter/log calls | 0.5 d | NTS2 | TODO |
| NTS4-05 | Unit: `test_node_typesafety_settings.py` — `_validate_node_typesafety` rejects an unknown mode with the exact `PRISMAL_NODE_TYPESAFETY_MODE=...` message | 0.2 d | NTS2-01 | TODO |
| NTS4-06 | Integration: `test_node_typesafety_disabled_snapshot.py` — `node_typesafety_enabled=False` ⇒ compiled supervisor graph structurally unchanged (mirrors Runtime Hardening's `H6-06`) | 0.4 d | NTS2 | TODO |
| NTS4-07 | Integration: `test_node_typesafety_e2e.py` — `warn` passes through a malformed pilot-node output; `enforce` raises + is mapped by `error_mapping_middleware` end to end | 0.5 d | NTS3 | TODO |
| NTS4-08 | AST/consistency guard: confirm no new provider-SDK import and no new `prismal.mcp`/`prismal.skills` import introduced by `node_schema.py` (existing repo-wide guards should already cover this — verify, do not duplicate) | 0.2 d | NTS1 | TODO |
| NTS4-09 | `docs/node-typesafety.md` — quickstart, adoption path (warn→enforce), worked pilot-node example | 0.5 d | NTS3 | TODO |
| NTS4-10 | `examples/node_typesafety.py` — runnable, annotates a toy node end to end (not one of the 3 pilots, to keep the example self-contained) | 0.3 d | NTS1, NTS2 | TODO |
| NTS4-11 | `README.md` Roadmap item 8 + `CHANGELOG.md` — add entries describing this as **planned for `3.8.0`**, not shipped; do **not** mark PLAN/SPEC/ARCHITECTURE `IMPLEMENTED` | 0.2 d | — | TODO |

**Done when:** `uv run pytest -m unit` is green for all new tests; `uv run pytest -m integration` is green for the two new integration tests; `ruff` + `mypy --strict` + `bandit` clean on every new/modified module; coverage on `node_schema.py` ≥ 85%, project-wide `fail_under=80` still satisfied; docs and example are runnable as written.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| `node_io_validation_middleware` inserted at the wrong position in `DEFAULT_MIDDLEWARE_STACK`, breaking the "innermost" contract | NTS2-04 is a single, isolated task with an explicit unit assertion on stack order; code review checklist item |
| Extending `NodeValidationError.__init__` breaks a hidden, undiscovered caller | NTS2-02 starts with a repo-wide grep for `NodeValidationError(` before changing the signature; if any call site exists, adapt it in the same task |
| Pilot node annotations (NTS3) drift from `AgentState` after an unrelated future refactor | NTS3-05's `get_type_hints()`-based test runs in the standard unit suite, catching drift immediately, not just at pilot-authoring time |
| `warn`-mode log/metric volume becomes noisy in CI or local dev once pilots are enabled by default in a downstream test fixture | Default `node_typesafety_enabled=False` in `Settings()`; tests that want the feature on construct `Settings(node_typesafety_enabled=True, ...)` explicitly, never globally |
| Scope creep into building `NodeIOValidatorPort` (DD-NTS-004) during NTS1 "just in case" | Explicitly deferred in `ARCHITECTURE.md`/`SPEC.md`; NTS1-NTS4 tasks above contain no `ports.py` changes — any such work requires a new task added after the Open Question is resolved |

## 5. Definition of Done (feature)

- [ ] All MUST requirements (RF-NTS-001…008) implemented and tested.
- [ ] `node_typesafety_enabled=False` ⇒ compiled supervisor graph structurally unchanged (NTS4-06 passing).
- [ ] `warn` mode never raises and never mutates state on a validation failure; `enforce` mode raises `NodeValidationError`, mapped by the existing `error_mapping_middleware` with zero changes to that middleware's `except` clauses.
- [ ] ≥ 3 pilot nodes annotated (`file_manager`, `cron_manager`, `skill_manager`) and passing the `AgentState`-field-name drift guard.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit and integration suites green.
- [ ] `docs/node-typesafety.md` + `examples/node_typesafety.py` published and runnable.
- [ ] `README.md`/`CHANGELOG.md` updated to reflect **planned, not shipped** status for `3.8.0`.
- [ ] No behavior change to any of the 26+ existing agents that do not opt in.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| NTS1 | `NodeIOSchema` contract types | ~1.5 d |
| NTS2 | Middleware + builder wiring | ~1.7 d |
| NTS3 | Incremental adoption pilot + `AgentState` decision | ~1.7 d |
| NTS4 | Settings, OTel, tests, docs, packaging | ~3.9 d |
| **Total** | | **~8.8 d** |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #5) and README Roadmap item 8 |
