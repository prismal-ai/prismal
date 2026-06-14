# Prismal Agent Evaluation & Reliability Harness — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Target package version** | `3.3.0` (SemVer minor) |
| **PLAN** | `specs/agent-eval-harness/PLAN.md` |
| **SPEC** | `specs/agent-eval-harness/SPEC.md` |
| **Architecture** | `specs/agent-eval-harness/ARCHITECTURE.md` |

---

## 1. Implementation Summary

The evaluation harness lands in six phases (V1–V6) as a **new sibling package** `prismal/eval/` that observes the runtime via the public graph entry point and the public ports. It is **additive** — it changes no agent code, adds no runtime settings that affect agents, and runs deterministically with fakes by default. The adversarial suite (V5) is the executable proof for `specs/runtime-hardening/` (Phase H).

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. (All rows `TODO` — spec is `READY`, not implemented.)

## 2. Prerequisites

- Reuse, do not modify: `agents/graph.py::get_async_compiled_graph` (public entry), `composition/runtime.py::build_test_runtime`, `FakeToolProvider`/`FakeVectorStore`/`FakeConfigSource`, `providers/` (judge), `monitoring/` (cost/latency/Langfuse), `budget/` (metering), `security/` + Phase H controls (asserted by red-team).
- Confirm `get_async_compiled_graph().astream(...)` yields enough to reconstruct messages + tool-calls + visited nodes.
- Optional dependency: `agent-eval-harness` benefits from but does **not** require `runtime-hardening` to be shipped — without Phase H the red-team suite asserts L1–L5 containment only.

## 3. Implementation Phases

### PHASE V1 — Types + eval-set format + settings

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| V1-01 | `eval/types.py`: `EvalCase`/`EvalSet`/`Assertion`/`Trajectory`/`CaseResult`/`Scorecard` (SPEC-EVL-TYP-001) | 0.6 d | — | TODO |
| V1-02 | `EvalSet.from_yaml` + schema validation; `tests/eval/sets/` layout | 0.4 d | V1-01 | TODO |
| V1-03 | `core/config.py`: `eval_*` settings (SPEC-EVL-CFG-001) + `core/exceptions.py` `EvalError` | 0.3 d | — | TODO |

**Done when:** eval-sets parse; malformed set → `EvalSetError`; settings parse from `PRISMAL_*`.

### PHASE V2 — Trajectory capture + runner

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| V2-01 | `eval/trajectory.py`: `capture_trajectory` from `astream` (+ cost/latency via monitoring) | 0.8 d | V1 | TODO |
| V2-02 | `eval/runner.py`: `EvalRunner.run_case` (per-case `build_test_runtime`, seed) | 0.7 d | V2-01 | TODO |
| V2-03 | `EvalRunner.run_set` → `Scorecard` aggregation | 0.3 d | V2-02 | TODO |

**Done when:** a case runs through the real graph with fakes and yields a populated `Trajectory`; runs are deterministic with a fixed seed.

### PHASE V3 — Assertions + judges

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| V3-01 | `eval/assertions.py`: exact / semantic (embeddings port) / tool_usage | 0.6 d | V2 | TODO |
| V3-02 | `eval/judges.py`: `Judge` (LLM-as-judge via `providers/`, metered by Budget) | 0.5 d | V2 | TODO |
| V3-03 | `assert_llm_judge` + `assert_groundedness` (answer ⊂ retrieved context) | 0.5 d | V3-02 | TODO |

**Done when:** each assertion type passes and fails on crafted cases; judge runs only in `live_api`/`slow` by default.

### PHASE V4 — Regression + report + CLI

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| V4-01 | `eval/regression.py`: `compare()` + tolerance; `tests/eval/baselines/` | 0.5 d | V3 | TODO |
| V4-02 | `eval/report.py`: `to_json` / `to_markdown` (+ optional `to_langfuse`) | 0.4 d | V3 | TODO |
| V4-03 | `eval/__main__.py`: `run` / `redteam` / `gate` CLI (SPEC-EVL-CLI-001) | 0.5 d | V4-01,V4-02 | TODO |

**Done when:** a seeded regression fails the gate; scorecard renders to JSON+MD; CLI smoke passes.

### PHASE V5 — Adversarial / red-team suite

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| V5-01 | `eval/redteam/`: `load_redteam_corpus` + `assert_security` (containment via audit + OTel) | 0.6 d | V3 | TODO |
| V5-02 | Author corpus: injection (direct+indirect), tool_abuse, exfiltration, jailbreak, prompt_leak (ship `redteam-corpus.example.yaml`) | 0.7 d | V5-01 | TODO |
| V5-03 | Assert containment against L1–L5 (+ Phase H taint/injection/policy when present) | 0.5 d | V5-01 | TODO |

**Done when:** each red-team case proves the malicious tool was not executed and a security signal fired; suite runs with fakes.

### PHASE V6 — CI integration, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| V6-01 | `pyproject.toml`: `eval` + `redteam` pytest markers; CI job (fakes) | 0.3 d | V4 | TODO |
| V6-02 | CI gate: fail on regression or any passed attack | 0.3 d | V4,V5 | TODO |
| V6-03 | AST guard: `prismal/eval/**` imports only public graph entry + ports (no `agents.*` internals, no `mcp`/`skills`) | 0.3 d | V2 | TODO |
| V6-04 | `docs/eval.md` + `examples/agent_eval.py` + per-release scorecard | 0.5 d | V4 | TODO |
| V6-05 | `README.md` + `CHANGELOG.md`; mark PLAN/SPEC/ARCHITECTURE `IMPLEMENTED` | 0.2 d | V4 | TODO |

**Done when:** `uv run pytest -m eval` and `-m redteam` green with fakes; `ruff` + `mypy --strict` + `bandit` clean; coverage ≥ project target on `prismal/eval/`.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| LLM non-determinism falsifies regression | Seeds + fakes + per-metric tolerance; stable judge rubric |
| Eval cost with real models | Fakes default; `live_api` opt-in; Budget-metered judge; sampling |
| Evals not representative (scaffold gap) | Always run the **real graph**, never the direct API |
| Stale eval-sets | Version eval-sets with code; CI gate keeps them live |
| Harness coupling to agent internals | AST guard restricts imports to public entry + ports (V6-03) |

## 5. Definition of Done (feature)

- [ ] All MUST requirements (RF-EVL-001…007) implemented and tested.
- [ ] Eval-set runs against the real graph; trajectory + metrics captured.
- [ ] Five assertion types + security containment assertion working.
- [ ] Regression gate fails on seeded regressions; scorecard exported.
- [ ] Red-team suite proves containment of injection/tool-abuse/exfiltration/jailbreak/prompt-leak.
- [ ] `prismal/eval/**` imports only public entry + ports (AST-guarded); zero agent-runtime change.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; `-m eval`/`-m redteam` green with fakes.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| V1 | Types + eval-set + settings | ~1.3 d |
| V2 | Trajectory capture + runner | ~1.8 d |
| V3 | Assertions + judges | ~1.6 d |
| V4 | Regression + report + CLI | ~1.4 d |
| V5 | Adversarial / red-team | ~1.8 d |
| V6 | CI + docs + packaging | ~1.6 d |
| **Total** | | **~9.5 d** |
