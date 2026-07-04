# Prismal Guardrails Modernization — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.1 |
| **Date** | 2026-07-04 |
| **Phase** | GRD |
| **Target package version** | `3.6.0` (SemVer minor — shipped) |
| **PLAN** | `specs/guardrails-modernization/PLAN.md` |
| **SPEC** | `specs/guardrails-modernization/SPEC.md` |
| **Architecture** | `specs/guardrails-modernization/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Guardrails Modernization lands in three phases (GRD1–GRD3), each independently testable and gated behind its own opt-in flag (`nemo_classifier_enabled`, `structured_output_guard_enabled`, both default `False`) so `main` stays green and the existing L1–L5 stack is unaffected until an operator opts in. GRD1 ships the NeMo config artifact the repo has always assumed but never shipped, plus a reasoning-capable classifier action. GRD2 adds `StructuredOutputGuard` behind a new `[guardrails-ai]` extra. GRD3 wires both into settings/OTel/tests/docs/packaging. No new LangGraph capability; no change to the `runtime-hardening` middleware ordering.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. **Implemented 2026-07-04 with TDD** — all 26 tasks `DONE`, both open design questions (DD-GRD-003, DD-GRD-004) resolved (see below), full quality gate green.

## 2. Prerequisites

- Reuse, do not fork: `security/nemo_rails.py` (`NemoRailsLayer`, `_parse_block_response`, `get_nemo_layer`), `security/guardrails.py` (`GuardrailsEngine._nemo_layer`), `security/output_validator.py` (`OutputValidator.validate_tool_args`), `security/audit.py` (`AuditLogger`), `security/prompt_builder.py` (`SecurePromptBuilder`), `budget/guard.py` (`BudgetGuard`, `make_budget_guard_fn`), `budget/resolve.py` (per-run registry pattern), `monitoring/otel.py` (`OTelManager._register_standard_metrics`), `core/exceptions.py` (`MissingDependencyError`).
- Confirm `nemoguardrails` (0.21.0, already a base dependency) supports `rails.register_action()` for custom actions — verify against the installed version before writing `nemo_actions.py` (API surface may differ slightly across 0.10.x → 0.21.x; the repo already jumped versions once without a code change, per the gap analysis).
- Confirm the exact `guardrails-ai` version to pin in `[project.optional-dependencies]` (SPEC-GRD-PKG-001 floor `>=0.6.0` is provisional — confirm against current PyPI/its `Guard.for_pydantic` API shape at implementation time).
- Resolve the two `> Open question:` callouts in `ARCHITECTURE.md` (DD-GRD-003 classifier timeout tuning, DD-GRD-004 guardrails-ai re-ask LLM wiring) before or during GRD1-02/GRD2-01 — do not silently pick a default without reviewer sign-off.

## 3. Implementation Phases

### PHASE GRD1 — NeMo config + reasoning safety-classifier rail

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| GRD1-01 | `config/nemo_rails/config.yml`: model + rails wiring (SPEC-GRD-NEMO-CFG-001) | 0.4 d | — | DONE |
| GRD1-02 | `config/nemo_rails/main.co`: dialog/topical flows for the 5(+1) existing sentinel categories | 0.6 d | GRD1-01 | DONE |
| GRD1-03 | `prismal/security/nemo_actions.py`: `content_safety_reasoning()` + `register()` (SPEC-GRD-NEMO-CLS-001) | 0.8 d | GRD1-01 | DONE |
| GRD1-04 | `config/nemo_rails/safety_classifier.co`: flow invoking the custom action, sentinel-wrapped verdict | 0.5 d | GRD1-03 | DONE |
| GRD1-05 | `nemo_rails.py`: conditional `nemo_actions.register(rails, settings=...)` call in `NemoRailsLayer.__init__`, gated on `nemo_classifier_enabled` | 0.4 d | GRD1-03 | DONE |
| GRD1-06 | Independent classifier timeout (SPEC-GRD-NEMO-TIMEOUT-001) — separate `wait_for` inside `content_safety_reasoning`, never touching `_NEMO_TIMEOUT_SECONDS` | 0.3 d | GRD1-03 | DONE |
| GRD1-07 | Default classifier judgment call wiring via `ProviderRegistry().get_llm()` (Rule #4) | 0.4 d | GRD1-03 | DONE |

**Done when:** `config/nemo_rails/` exists with `config.yml` + `main.co` (+ `safety_classifier.co`); `NemoRailsLayer(Path("config/nemo_rails")).available is True`; `tests/integration/security/test_nemo_pipeline.py::test_nemo_rails_config_dir_exists` passes unmodified; a curated corpus of harmful prompts across the 5 default categories is classified correctly when `nemo_classifier_enabled=True`; classifier timeout/error fails open and is audited.

### PHASE GRD2 — Structured-output guardrails via `guardrails-ai`

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| GRD2-01 | `[project.optional-dependencies].guardrails-ai` extra (SPEC-GRD-PKG-001); confirm floor version | 0.2 d | — | DONE |
| GRD2-02 | `prismal/security/structured_output_guard.py`: `StructuredOutputVerdict`, `StructuredOutputGuard.__init__` (lazy `import guardrails`, `MissingDependencyError` on absence) | 0.6 d | GRD2-01 | DONE |
| GRD2-03 | `StructuredOutputGuard.validate()`: schema validation via `Guard.for_pydantic(schema)` (or equivalent current API) | 0.7 d | GRD2-02 | DONE |
| GRD2-04 | Bounded, metered re-ask loop: `reask_fn` default wiring to `ProviderRegistry().get_llm()`; `budget_guard_fn` consulted before each attempt (SPEC-GRD-SOG-001) | 0.8 d | GRD2-03 | DONE |
| GRD2-05 | Opt-in Guardrails Hub validators (`hub_validators` param + `structured_output_guard_hub_validators_enabled` master gate) | 0.6 d | GRD2-03 | DONE |
| GRD2-06 | Composition point: `.coerced` value still flows through `OutputValidator.validate_tool_args()` unchanged (DD-GRD-006) | 0.3 d | GRD2-03 | DONE |

**Done when:** valid output passes with `reask_count=0`; invalid-then-fixed output passes with `reask_count>0`; output that never resolves within the bound returns `ok=False, reason="reask_exhausted"`; a `budget_guard_fn` denial stops re-asking immediately; missing `[guardrails-ai]` raises `MissingDependencyError` at construction, not mid-call; `OutputValidator` still runs on the coerced value.

### PHASE GRD3 — Integration, settings, OTel, tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| GRD3-01 | `core/config.py`: `nemo_classifier_*` + `structured_output_guard_*` settings + `_validate_guardrails_modernization` (SPEC-GRD-CFG-001) | 0.4 d | — | DONE |
| GRD3-02 | `core/exceptions.py`: `GuardrailsModernizationError` hierarchy (SPEC-GRD-ERR-001) | 0.2 d | — | DONE |
| GRD3-03 | `monitoring/otel.py`: register the 4 new counters/histograms (SPEC-GRD-OTEL-001) | 0.3 d | GRD1,GRD2 | DONE |
| GRD3-04 | `prismal/security/__init__.py`: re-export `StructuredOutputGuard`, `StructuredOutputVerdict` | 0.1 d | GRD2 | DONE |
| GRD3-05 | Unit: classifier action over a curated corpus (5 categories + safe); timeout/error fail-open | 0.6 d | GRD1 | DONE |
| GRD3-06 | Unit: `StructuredOutputGuard` valid/invalid/re-ask/exhausted/budget-denied/missing-extra paths | 0.6 d | GRD2 | DONE |
| GRD3-07 | Integration: `config/nemo_rails/` loads with a mocked `LLMRails`; classifier-off latency regression test (P99 unaffected) | 0.5 d | GRD1 | DONE |
| GRD3-08 | Integration: both flags `False` ⇒ compiled-graph snapshot unchanged (mirrors `hardening_enabled=False` precedent) | 0.3 d | GRD1,GRD2 | DONE |
| GRD3-09 | AST guard: `nemoguardrails`/`guardrails` imports confined to `prismal/security/`; extend existing no-provider-outside-`providers/` and no-`mcp`/`skills`-in-`agents/**` guards to cover the new modules | 0.3 d | GRD1,GRD2 | DONE |
| GRD3-10 | `pyproject.toml`: `guardrails-ai` extra + `all` aggregate + `[tool.mypy.overrides]` entry (SPEC-GRD-PKG-001) | 0.2 d | GRD2-01 | DONE |
| GRD3-11 | `docs/security/guardrails-modernization.md` | 0.5 d | GRD1,GRD2 | DONE |
| GRD3-12 | `examples/guardrails_modernization.py` | 0.4 d | GRD1,GRD2 | DONE |
| GRD3-13 | `README.md` + `CHANGELOG.md` entries (written as **planned**, target `3.6.0` — not marked shipped) | 0.2 d | GRD3-11 | DONE |

**Done when:** `uv run pytest -m unit` green; `ruff`, `mypy --strict`, `bandit` clean; coverage ≥ project target (80%) on new modules; both new flags default `False`; snapshot test proves zero behavior change when disabled.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Classifier LLM call blows the existing 450 ms P99 dialog-rail contract | Independent timeout (GRD1-06); regression test (GRD3-07) proves the default path is untouched |
| `nemoguardrails` 0.21.0's custom-action API differs from what GRD1-03 assumes | Verify against the installed version before writing the module (Prerequisites); keep `register()` a thin, isolated adapter so an API mismatch is a one-file fix |
| `guardrails-ai`'s `Guard` re-ask mechanism requires its own LLM call shape that resists clean `providers/` injection | Flagged as an open design question (DD-GRD-004) for reviewer sign-off before GRD2-04 lands; worst case, document the isolation compromise explicitly rather than silently violating Rule #4 |
| Unbounded re-ask cost | Hard cap `structured_output_guard_max_reasks`; `budget_guard_fn` can veto earlier |
| False positives from the classifier block legitimate flows | `nemo_classifier_threshold` tunable; default categories mirror only what tests already assert (no scope creep) |
| Behavior leak when disabled | Gate every wiring point on its own flag; snapshot test (GRD3-08) |
| Missing `[guardrails-ai]` crashes a graph in production | Constructor-time `MissingDependencyError`, caught once by the caller, never mid-call (GRD2-02) |

## 5. Definition of Done (feature)

- [x] All MUST requirements (RF-GRD-001…008, 010, 012, 013) implemented and tested.
- [x] `config/nemo_rails/` ships and `NemoRailsLayer.available=True` under `nemo_guardrails_enabled=True` (verified against the real installed `nemoguardrails==0.21.0`, no API key required — main LLM is injected via `providers/registry.py`).
- [x] The existing ≤ 500 ms P99 / fail-open contract for the classifier-off dialog-rail path is proven unchanged by a regression test (`test_check_input_timeout_budget_unaffected_when_classifier_enabled`, `test_nemo_timeout_constant_unaffected_by_classifier_flag`).
- [x] The reasoning safety-classifier scores a curated corpus correctly and fails open on timeout/error.
- [x] `StructuredOutputGuard` resolves schema violations via bounded, Budget-metered re-ask, and composes with (never bypasses) `OutputValidator` (verified end-to-end with the real `guardrails-ai==0.10.2` package).
- [x] Both `nemo_classifier_enabled=False` and `structured_output_guard_enabled=False` ⇒ zero behavior change (snapshot proven — `test_graph_snapshot_guardrails_modernization.py`).
- [x] No provider SDK import outside `providers/`; `nemoguardrails`/`guardrails` imports confined to `security/` (module top-level AST-guarded); no `prismal.mcp`/`prismal.skills` import in `agents/**` (untouched by this phase).
- [x] `ruff` + `mypy --strict` + `bandit` clean; unit suite green (3688 passed / 9 skipped, tests/unit full tree).
- [x] Both open design questions resolved (reviewer sign-off obtained 2026-07-04):
  - **DD-GRD-003**: single global `nemo_classifier_timeout_seconds` setting, default `3.0`s. Per-provider tuning deferred.
  - **DD-GRD-004**: `StructuredOutputGuard` uses only `Guard.for_pydantic(schema).validate()` (pure schema check, no LLM call inside guardrails-ai). The bounded re-ask loop is driven entirely by Prismal via an injected `reask_fn` resolved from `providers/` — `guardrails-ai`'s own `Guard.__call__(llm_api=...)` re-ask mechanism is never invoked. Zero provider-isolation compromise.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| GRD1 | NeMo config + reasoning safety-classifier rail | ~3.4 d |
| GRD2 | Structured-output guardrails via `guardrails-ai` | ~3.2 d |
| GRD3 | Integration, settings, OTel, tests, docs, packaging | ~4.6 d |
| **Total** | | **~11.2 d** |
