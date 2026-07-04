# Prismal Observability Integration — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | OBS |
| **Target package version** | `3.9.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/observability-integration/PLAN.md` |
| **SPEC** | `specs/observability-integration/SPEC.md` |
| **Architecture** | `specs/observability-integration/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Observability Integration lands in six phases (OBS1–OBS6), each independently testable and gated behind `settings.observability_enabled` (default `False`) so `main` stays green and every existing OTel/Langfuse call site is unaffected until a caller explicitly opts in. Reuses existing primitives throughout (`OTelManager`, `LangfuseManager`, `budget/resolve.py`'s per-run-registry pattern, `budget/types.Usage`, the `agents/extension/ports.py` `Protocol` family, `build_runtime()`'s opt-in-triple composition pattern). No new LangGraph capability.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. **All rows are `TODO` — this feature has not been started.**

## 2. Prerequisites

- Reuse, do not fork: `monitoring/otel.py::OTelManager`, `monitoring/langfuse_client.py::LangfuseManager`, `budget/resolve.py` (per-run registry pattern), `budget/types.py::Usage`, `agents/extension/ports.py` (Protocol family + `conforms_to`), `composition/runtime.py::build_runtime`/`RuntimeContext` (opt-in port composition pattern from `identity_enabled`/`a2a_enabled`).
- Confirm `eval/types.py::Trajectory`/`eval/trajectory.py::capture_trajectory` remain untouched (DD-OBS-001 — parallel, not shared, types).
- Confirm `RuntimeContext` can accept one more optional dataclass field without breaking `build_test_runtime`'s existing fake-construction call sites (it constructs `RuntimeContext` with keyword args only, so a new defaulted field is additive).

## 3. Implementation Phases

### PHASE OBS1 — Settings + Exceptions + Value objects

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| OBS1-01 | `core/config.py`: `observability_*` settings (SPEC-OBS-CFG-001) + `_validate_observability` | 0.3 d | — | TODO |
| OBS1-02 | `core/exceptions.py`: `ObservabilityError` hierarchy (SPEC-OBS-ERR-001) | 0.2 d | — | TODO |
| OBS1-03 | `monitoring/observability_types.py`: `RunSummary`, `SpanRecord`, `ToolCallRecord`, `ScoreAnnotation`, `DatasetFormat` (SPEC-OBS-TYP-001) | 0.5 d | — | TODO |

**Done when:** settings parse from `PRISMAL_*`; value objects are frozen dataclasses; `RunSummary.usage` composes via the reused `Usage.__add__`.

### PHASE OBS2 — `ObservabilityPort` + default/fake adapters

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| OBS2-01 | `agents/extension/ports.py`: `ObservabilityPort` `Protocol` (SPEC-OBS-PRT-001) | 0.4 d | OBS1 | TODO |
| OBS2-02 | `monitoring/observability.py`: `DefaultObservabilityProvider` wrapping `OTelManager` + `LangfuseManager`, with a bounded ring buffer per run | 0.8 d | OBS1, OBS2-01 | TODO |
| OBS2-03 | `monitoring/observability.py`: `FakeObservabilityProvider` (deterministic, I/O-free) | 0.3 d | OBS2-01 | TODO |
| OBS2-04 | `monitoring/observability_resolve.py`: per-run registry (`seed_observability_run`/`get_observability_provider`/`clear_observability_run`, SPEC-OBS-RES-001) | 0.5 d | OBS1, OBS2-01 | TODO |

**Done when:** any object satisfying the `ObservabilityPort` shape passes `conforms_to()`; `DefaultObservabilityProvider.record_node`/`.record_score` never raise even with OTel/Langfuse disabled; the registry is idempotent per `(session_id, turn)`.

### PHASE OBS3 — LangSmith/Langfuse parity (naming, scoring, dataset export)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| OBS3-01 | `monitoring/observability.py`: `run_name_for`, `trace_tags_for` (SPEC-OBS-PAR-001) | 0.4 d | OBS1 | TODO |
| OBS3-02 | Wire `DefaultObservabilityProvider` + `LangfuseManager.create_trace` call sites touched by this phase through `run_name_for`/`trace_tags_for` exclusively | 0.4 d | OBS3-01, OBS2-02 | TODO |
| OBS3-03 | `record_score` end-to-end: default adapter forwards to `LangfuseManager.score_trace` **and** stores a `ScoreAnnotation` in the run's local `RunSummary` (SPEC-OBS-PAR-002) | 0.5 d | OBS2-02 | TODO |
| OBS3-04 | `export_dataset`: per-format record shape for `langsmith` and `langfuse` (SPEC-OBS-PAR-003) | 0.6 d | OBS2-02 | TODO |

**Done when:** a run's name/tags are derived solely from the two naming functions; a recorded score appears in both the Langfuse backend (mocked in tests) and the local `RunSummary.scores`; `export_dataset` output matches the documented per-format shape for both formats.

### PHASE OBS4 — `build_runtime()` composition

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| OBS4-01 | `composition/runtime.py`: `RuntimeContext.observability` field (SPEC-OBS-CMP-001) | 0.2 d | OBS2 | TODO |
| OBS4-02 | `composition/runtime.py`: `build_runtime()` opt-in composition step (`if eff.observability_enabled: ...`), mirroring the `identity_enabled`/`a2a_enabled` blocks | 0.4 d | OBS4-01, OBS2-02 | TODO |
| OBS4-03 | `build_test_runtime(...)`: optional `observability` fake-injection parameter, defaulting to `None` (parity with the other fakes) | 0.2 d | OBS4-01, OBS2-03 | TODO |

**Done when:** `build_runtime(settings)` with the flag off yields `observability is None` and imports nothing new; with the flag on it yields a working `DefaultObservabilityProvider`; a composition failure raises `RuntimeCompositionError("observability", ...)` after tearing down whatever was already built (matching the existing failure-handling shape in `build_runtime`).

### PHASE OBS5 — OTel counters + eval-harness integration doc pattern

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| OBS5-01 | `monitoring/otel.py`: register the 3 OBS counters (SPEC-OBS-OTEL-001) | 0.3 d | OBS2, OBS3 | TODO |
| OBS5-02 | Document (not implement) the eval-harness LLM-judge → `record_score` integration pattern for `docs/observability-integration.md` (SPEC-OBS-PAR-002); no changes to `prismal/eval/` | 0.3 d | OBS3-03 | TODO |

**Done when:** the 3 counters are registered and incremented by `DefaultObservabilityProvider`/`export_dataset`; the eval-harness integration pattern is documented with a runnable snippet, without modifying any `prismal/eval/*.py` file.

### PHASE OBS6 — Tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| OBS6-01 | Unit: value objects + ring-buffer eviction policy | 0.3 d | OBS1 | TODO |
| OBS6-02 | Unit: `ObservabilityPort` conformance (`DefaultObservabilityProvider`, `FakeObservabilityProvider`) via `conforms_to()` | 0.3 d | OBS2 | TODO |
| OBS6-03 | Unit: naming convention determinism (`run_name_for`/`trace_tags_for`) | 0.3 d | OBS3 | TODO |
| OBS6-04 | Unit: `record_score` + `export_dataset` per-format shape (both `langsmith` and `langfuse`) | 0.5 d | OBS3 | TODO |
| OBS6-05 | Integration: `observability_enabled=False` — assert `RuntimeContext.observability is None` **and** zero new calls into `OTelManager`/`LangfuseManager` versus the pre-OBS baseline | 0.4 d | OBS4 | TODO |
| OBS6-06 | Integration: `observability_enabled=True` with fakes — a simulated run's `get_run_summary()` reflects its node visits/tool calls end-to-end | 0.5 d | OBS4 | TODO |
| OBS6-07 | AST guards: no provider import outside `providers/`; no `mcp`/`skills` import in `agents/**` (reuse existing AST tests; confirm no new exemption needed) | 0.2 d | OBS2 | TODO |
| OBS6-08 | `docs/observability-integration.md` — explicit framework/host split section naming `prismal-dashboard`; `examples/observability_integration.py` | 0.6 d | OBS5 | TODO |
| OBS6-09 | `README.md` + `CHANGELOG.md` entries recorded as **planned** (not shipped); mark this spec's PLAN/ARCHITECTURE/SPEC status when actually implemented (still `DRAFT` as of this TASKS revision) | 0.2 d | OBS6-08 | TODO |

**Done when:** `uv run pytest -m unit` green; `ruff`, `mypy --strict`, `bandit` clean; coverage ≥ project target (80%) on new modules.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Scope creep toward a dashboard UI | Hard boundary restated in every doc's header; code review checks no HTML/templating/web-framework import lands in `prismal/` |
| In-memory ring buffer grows unbounded under load | `observability_run_buffer_size`/`observability_max_runs` caps + LRU eviction (OBS2-02) |
| Duplicating `prismal/eval/`'s `Trajectory` | DD-OBS-001 — new, parallel types; explicitly not shared; reviewed at OBS1-03 |
| Behavior leak when disabled | Gate every wiring point on `observability_enabled`; dedicated test (OBS6-05) |
| Naming convention diverges from actual LangSmith/Langfuse dashboard expectations | Validate `run_name_for`/`trace_tags_for` output against a real Langfuse project in a dev environment before marking OBS3 done (rollout step 2 in `ARCHITECTURE.md`) |
| Coupling with a non-existent `prismal-dashboard` | Feature lives in the core; the (future) dashboard only calls the port; contract documented the same way Phase R documented the `prismal-server` lifespan before that repo existed |

## 5. Definition of Done (feature)

- [ ] All MUST requirements (RF-OBS-001…003, 005, 006, 008, 009) implemented and tested; SHOULD requirements (RF-OBS-004, 007, 010) implemented.
- [ ] `ObservabilityPort` exposes a queryable `RunSummary` (spans, tool-call history, node-visit sequence, cost/latency).
- [ ] Run/trace naming + tags follow the one documented convention; `record_score` attaches feedback to a specific run; `export_dataset` produces LangSmith- and Langfuse-compatible records.
- [ ] `build_runtime()` composes `RuntimeContext.observability` exactly like the other opt-in port triples (identity, A2A).
- [ ] With `observability_enabled=False`, zero behavior change (dedicated test proves no new OTel/Langfuse call site is touched).
- [ ] No provider SDK / `prismal.mcp` / `prismal.skills` import in the wrong layer.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit suite green; coverage ≥ 80% on new modules.
- [ ] `docs/observability-integration.md` states the framework/host split and the `prismal-dashboard` plug-in point explicitly.
- [ ] README/CHANGELOG entries added as **planned**, not shipped (this TASKS revision ships zero code).

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| OBS1 | Settings + exceptions + value objects | ~1.0 d |
| OBS2 | `ObservabilityPort` + default/fake adapters | ~2.0 d |
| OBS3 | LangSmith/Langfuse parity (naming, scoring, dataset export) | ~1.9 d |
| OBS4 | `build_runtime()` composition | ~0.8 d |
| OBS5 | OTel counters + eval-harness integration doc pattern | ~0.6 d |
| OBS6 | Tests + docs + packaging | ~3.3 d |
| **Total** | | **~9.6 d** |

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #6) and README Roadmap item 8 |
