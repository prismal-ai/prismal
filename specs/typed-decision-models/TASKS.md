# Prismal Typed Decision Models — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `READY` (full SDD, not implemented) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | DEC |
| **Target package version** | `3.15.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/typed-decision-models/PLAN.md` |
| **SPEC** | `specs/typed-decision-models/SPEC.md` |
| **Architecture** | `specs/typed-decision-models/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Six build phases (DEC1–DEC6) plus a verify-before-build gate (DEC0), each independently testable
and gated by `decisions_enabled` plus one flag per integration (all default `False`); every
integration lands in `shadow` first. Reuses `AuditLogger`, `TaintRegistry`, `InputSanitizer`,
`GuardrailsEngine`, `SecurePromptBuilder`, `ProviderRegistry`, `CostMeter`, `OTelManager`,
`ConfigSourcePort`/`CredentialVault`. TDD throughout. Status: `TODO` · `WIP` · `DONE` · `BLOCKED`.

## 2. Prerequisites

- Phases H, V, IDN shipped; GW optional (Jev via OpenRouter); TKE1 usage fields present. Resolve
  ARCHITECTURE §10 open question 1 before DEC4-02.
- **Verify before build** — one task per **[verify]** fact; findings recorded in
  `docs/decisions.md#verified-facts` with date and source; settings defaults updated, never code.

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC0-01 | Verify Jev wire format (request `{model,state,questions}`, response `{model,answers,usage}`), endpoint URL, pinned id `jev-1.13.0`, Choice ≤ 255 / Score 2–10 / Noul semantics | 0.2 d | — | ADP-001, TYP-001 | TODO |
| DEC0-02 | Verify `typesafe-sdk` names (`AsyncTypeSafeClient.system_one` vs alternatives) and pin version | 0.1 d | — | ADP-003 | TODO |
| DEC0-03 | Verify `confidence` semantics (distribution shape) and vendor bands > 0.9 / 0.5–0.9 / < 0.5 | 0.1 d | — | TYP-002 | TODO |
| DEC0-04 | Verify price ($0.042/1M input, output free), latency 70–650 ms, limits (64k; state + longest question ≤ 32k), rate limits (sources conflict) | 0.2 d | — | ADP-001, SEC-001 | TODO |
| DEC0-05 | Verify ZDR availability (enterprise-only) and data terms; OpenRouter `/api/v1/systemone` pass-through and billing | 0.2 d | — | ADP-001 | TODO |
| DEC0-06 | Verify Laya API: `Router().predict`, `predict_batch(batch_size=64)`, `predict_long(max_len=8192)`, `Agent.predict(min_confidence)` fields, `fit_abstention_thresholds`, `save_calibration`, `LayaRouter`; extras `[serve]`/`[onnx]`; Python 3.13 wheels; checkpoints + context lengths | 0.3 d | — | ADP-002, CAL-001 | TODO |
| DEC0-07 | Verify `laya-serve`: `/v1/systemone`, `/batch` ≤ 64 states, `MAX_CHOICE_OPTIONS=100`, `LAYA_API_KEY` bearer | 0.1 d | — | ADP-001 | TODO |
| DEC0-08 | Reproduce Laya benchmarks on our hardware (T4 33 ms, batched 7.2 ms/q, CPU 193–464 ms, bf16 ±0.073 vs fp16, raw ECE 0.466, > 20 options degradation); confirm RLCD fine-tune cost (~4 h on 2×T4) | 0.5 d | DEC0-06 | ADP-002, CAL-001 | TODO |
| DEC0-09 | Re-read Octomind (0.76 → 0.48), CMU cascade (0.9 pt / 41 %) and LangSmith (narrow, early) sources; capture attack strings for the DEC6 corpus | 0.2 d | — | SEC-001, INT-002 | TODO |

**Done when:** every **[verify]** fact is confirmed or corrected in `docs/decisions.md` and settings defaults.

## 3. Implementation Phases

### PHASE DEC1 — Types, port, fake, cascade

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC1-01 | `core/config.py`: all `decision_*`/`jev_*`/`laya_*` settings + `_validate_decisions` (pinned model, raise-only τ, modes) | 0.5 d | — | CFG-001 | TODO |
| DEC1-02 | `core/exceptions.py`: `DecisionError` hierarchy | 0.1 d | — | ERR-001 | TODO |
| DEC1-03 | `decisions/types.py`: enums, `OptionSpec`, `QuestionSpec` (+ `schema_hash`), `Decision`, `GatePolicy` | 0.6 d | — | TYP-001/002/003 | TODO |
| DEC1-04 | `decisions/ports.py`: `DecisionPort` + parametrized contract test suite | 0.4 d | DEC1-03 | PRT-001 | TODO |
| DEC1-05 | `adapters/fake.py` | 0.2 d | DEC1-04 | ADP-005 | TODO |
| DEC1-06 | `decisions/gate.py`: `evaluate_gate`, `shuffle_options` | 0.5 d | DEC1-03 | CAS-002/003 | TODO |
| DEC1-07 | `decisions/validation.py`: `validate_question`, forbidden lint (EN/ES) | 0.5 d | DEC1-03 | SEC-002 | TODO |
| DEC1-08 | `decisions/state.py`: `DecisionStateBuilder` (sanitize, fence, taint, authority claims, language) | 0.8 d | DEC1-03 | SEC-001/003 | TODO |
| DEC1-09 | `decisions/cascade.py`: tiers, timeout budget, escalation, `from_settings` | 1.0 d | DEC1-05..08 | CAS-001 | TODO |
| DEC1-10 | `monitoring/otel.py`: register DEC metrics | 0.2 d | — | OTEL-001 | TODO |
| DEC1-11 | Graph snapshot test: all flags off ⇒ byte-for-byte vs 3.14.0 | 0.3 d | DEC1-01 | CMP-001 | TODO |

**Done when:** fake passes the contract suite; no ACCEPTED for CRITICAL/tainted/uncalibrated/abstained; snapshot unchanged.

### PHASE DEC2 — Backends

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC2-01 | `adapters/systemone_http.py` (+ batch, model-mismatch, usage → `CostMeter`) with `respx` fixtures | 1.0 d | DEC0-01/04/07, DEC1-04 | ADP-001 | TODO |
| DEC2-02 | `adapters/laya_local.py` (deferred import, `to_thread`, preload, max_loaded, fp16 check, language support) | 1.0 d | DEC0-06, DEC1-04 | ADP-002 | TODO |
| DEC2-03 | `adapters/jev_sdk.py` behind `_call()` shim | 0.4 d | DEC0-02 | ADP-003 | TODO |
| DEC2-04 | `adapters/llm_fallback.py` via `ProviderRegistry` + `SecurePromptBuilder` + metering | 0.6 d | DEC1-04 | ADP-004 | TODO |
| DEC2-05 | `pyproject.toml` extras `[laya]`, `[jev]`; extend provider-import AST guard with the adapters exemption | 0.3 d | DEC2-02/03 | SEC-002 | TODO |
| DEC2-06 | Secret-leak test: keys absent from logs, audit, URLs, `os.environ` | 0.3 d | DEC2-01/03 | CFG-001 | TODO |

**Done when:** every adapter passes the contract suite incl. never-raise on error/timeout/over-limit.

### PHASE DEC3 — Calibration, shadow, audit

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC3-01 | `calibration.py`: loader, temperature (Laya) / isotonic (Jev), `fit_thresholds`, `apply_calibration` | 1.2 d | DEC1-03 | CAL-001 | TODO |
| DEC3-02 | `CalibrationStore` + startup enforcement in `_validate_decisions` | 0.5 d | DEC3-01 | CAL-002 | TODO |
| DEC3-03 | Spanish slice requirement + per-language mode split (`es` shadow while `en` enforces) | 0.5 d | DEC3-02 | CAL-003 | TODO |
| DEC3-04 | `shadow.py`: `ShadowRunner`, `AgreementReport`, `promotion_ready`, auto-demote | 1.0 d | DEC1-09 | SHD-001/002 | TODO |
| DEC3-05 | `audit.py`: `DecisionAuditRecord`, `audit_decision` (hash-only) | 0.4 d | DEC1-09 | AUD-001 | TODO |
| DEC3-06 | `scripts/calibrate_decision.py` CLI (labeled JSONL → calibration file) | 0.4 d | DEC3-01 | CAL-001 | TODO |

**Done when:** synthetic set reaches ECE ≤ 0.05; enforce w/o file fails at startup; shadow inert; audit hash-only.

### PHASE DEC4 — Routing and eval-judge integrations

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC4-01 | `integrations/route.py` + supervisor wiring after `match_intent` (off/shadow/enforce) | 1.0 d | DEC3-04 | INT-001 | TODO |
| DEC4-02 | Option-count routing (≤ 20 Laya / > 20 Jev) + benchmark of two-level Choice (open question 1) | 0.6 d | DEC4-01 | INT-001 | TODO |
| DEC4-03 | `integrations/eval_judge.py`: rubric tagging, LLM-only for math/code/logic, 5 % audit, drift alarm | 1.0 d | DEC3-04 | INT-002 | TODO |
| DEC4-04 | Integration tests: shadow parity, enforce escalation, llm_calls_avoided accounting | 0.6 d | DEC4-01/03 | INT-001/002 | TODO |

**Done when:** with fakes, enforce routes accepted turns without an LLM call and escalates the rest.

### PHASE DEC5 — HITL, tool, RAG, compaction, swarm, safety

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC5-01 | `integrations/hitl.py` bands + `hitl_gate` wiring; irreversible/critical ⇒ human | 0.8 d | DEC3-04 | INT-003 | TODO |
| DEC5-02 | `integrations/tool.py` proposal within phase-allowed set; policy still authorizes | 0.5 d | DEC3-04 | INT-004 | TODO |
| DEC5-03 | `integrations/rag.py` CRAG chunk grading with per-chunk fallback | 0.5 d | DEC3-04 | INT-005 | TODO |
| DEC5-04 | `integrations/ctx.py` relevance scorer for TKE4 | 0.3 d | DEC3-04 | INT-006 | TODO |
| DEC5-05 | `integrations/swarm.py` Skynet text evaluator | 0.4 d | DEC3-04 | INT-007 | TODO |
| DEC5-06 | `integrations/safety.py` + `security/decision_bridge.py` escalate-only | 0.5 d | DEC3-04 | INT-008 | TODO |

**Done when:** each integration passes off/shadow/enforce tests; HITL/SAFE monotonicity proven.

### PHASE DEC6 — Security suite, docs, release

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC6-01 | Injection corpus (EN/ES; Octomind "pre-approved", double negatives, irrelevant padding) — zero forbidden transitions | 1.0 d | DEC0-09, DEC5 | SEC-001/003 | TODO |
| DEC6-02 | Option-order shuffle test (first-option bias fixture) | 0.3 d | DEC1-06 | CAS-003 | TODO |
| DEC6-03 | Forbidden-uses tests: AST import graph + behavior (lint, critical never auto, injection flag untouched, RunawayGuard untouched) | 0.6 d | DEC1-07 | SEC-002 | TODO |
| DEC6-04 | `scripts/bench_decisions.py` cost/latency per backend (live, opt-in) | 0.4 d | DEC2 | OTEL-001 | TODO |
| DEC6-05 | `docs/decisions.md` (fit table, rollout, calibration, verified facts), `examples/typed_decisions.py`, notebook in `prisma-notebooks` | 1.0 d | DEC5 | — | TODO |
| DEC6-06 | README + CHANGELOG + CLAUDE.md section; release 3.15.0 | 0.3 d | DEC6-05 | — | TODO |

**Done when:** full quality gate green (ruff, mypy --strict, bandit); coverage ≥ 80 % on new modules.

## 4. Test Inventory (planned)

| Test module | Covers |
|---|---|
| `tests/unit/core/test_config_decisions.py`, `tests/unit/agents/test_graph_snapshot_decisions.py` | DEC1-01, DEC3-02, DEC1-11 |
| `tests/unit/decisions/test_{types,gate,port_contract}.py` | DEC1-03/04/06, DEC2-01..04 |
| `tests/unit/decisions/test_{validation_forbidden,state_builder,cascade}.py` | DEC1-07..09, DEC6-03 |
| `tests/unit/decisions/adapters/test_{systemone_http,laya_local,jev_sdk,llm_fallback}.py` | DEC2-01..04 |
| `tests/unit/decisions/test_no_sdk_imports.py`, `test_secrets_not_leaked.py` | DEC2-05, DEC2-06 |
| `tests/unit/decisions/test_{calibration,calibration_spanish,shadow,audit}.py` | DEC3-01..05 |
| `tests/integration/decisions/test_{route,eval_judge,hitl,tool,rag,ctx,swarm,safety}_integration.py` | DEC4, DEC5 |
| `tests/security/decisions/test_{injection_corpus,shuffle}.py`; `tests/live/decisions/test_live_backends.py` (`live_api`) | DEC6-01/02; DEC0-08, DEC6-04 |

## 5. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| Vendor facts change; Laya zero-shot below majority baseline | DEC0 gate, values in settings; fine-tune out-of-band, enforce blocked until calibrated |
| Shadow agreement inflated by easy turns | Report coverage + agreement on accepted subset + per-language |
| Spanish labeled data scarce | `es` stays in shadow; disagreement mining from shadow logs |
| Injection bypass via new phrasing / bf16 drift | Append-only corpus, taint + proposal-only; fp16 + calibration on serving precision |

## 6. Definition of Done

- [ ] All MUST requirements implemented with ≥ 1 test each (traceability §8).
- [ ] All flags off ⇒ snapshot byte-for-byte; no new graph node. Zero forbidden transitions on the injection corpus; forbidden-use AST/behavior tests green.
- [ ] Enforce refuses to start without valid (incl. Spanish) calibration; audit hash-only; no secret leaks.
- [ ] DEC0 findings recorded; docs, example, notebook, CHANGELOG done.

## 7. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| DEC0 | Verify before build | ~1.9 d |
| DEC1 | Types, port, cascade | ~5.1 d |
| DEC2 | Backends | ~3.6 d |
| DEC3 | Calibration, shadow, audit | ~4.0 d |
| DEC4 | Routing + eval judge | ~3.2 d |
| DEC5 | Other integrations | ~3.0 d |
| DEC6 | Security, docs, release | ~3.6 d |
| **Total** | | **~24.4 d** |

## 8. Traceability Matrix

| SPEC | Tasks | Tests |
|---|---|---|
| SPEC-DEC-TYP-001/002/003 | DEC0-01, DEC0-03, DEC1-03 | `test_types.py`, `test_gate.py` |
| SPEC-DEC-PRT-001 | DEC1-04 | `test_port_contract.py` |
| SPEC-DEC-ADP-001 | DEC0-01/04/05/07, DEC2-01 | `test_systemone_http.py`, `test_port_contract.py` |
| SPEC-DEC-ADP-002 | DEC0-06/08, DEC2-02 | `test_laya_local.py`, `test_live_backends.py` |
| SPEC-DEC-ADP-003..005 | DEC0-02, DEC2-03/04, DEC1-05 | `test_jev_sdk.py`, `test_llm_fallback.py`, `test_port_contract.py` |
| SPEC-DEC-CAS-001..003 | DEC1-06, DEC1-09, DEC6-02 | `test_cascade.py`, `test_gate.py`, `test_shuffle.py` |
| SPEC-DEC-CAL-001..003 | DEC3-01..03, DEC3-06 | `test_calibration.py`, `test_calibration_spanish.py`, `test_config_decisions.py` |
| SPEC-DEC-SHD-001/002, AUD-001 | DEC3-04, DEC3-05 | `test_shadow.py`, `test_audit.py` |
| SPEC-DEC-SEC-001/003 | DEC1-08, DEC6-01 | `test_state_builder.py`, `test_injection_corpus.py` |
| SPEC-DEC-SEC-002 | DEC1-07, DEC2-05, DEC6-03 | `test_validation_forbidden.py`, `test_no_sdk_imports.py` |
| SPEC-DEC-INT-001..008 | DEC4-01..04, DEC5-01..06 | `test_{route,eval_judge,hitl,tool,rag,ctx,swarm,safety}_integration.py` |
| SPEC-DEC-CFG-001 / ERR-001 | DEC1-01, DEC1-02, DEC2-06 | `test_config_decisions.py`, `test_secrets_not_leaked.py` |
| SPEC-DEC-OTEL-001 | DEC1-10, DEC6-04 | `test_cascade.py` (counter asserts) |
| SPEC-DEC-CMP-001 | DEC1-11 | `test_graph_snapshot_decisions.py` |
