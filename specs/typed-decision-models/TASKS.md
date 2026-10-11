# Prismal Typed Decision Models — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-05, v3.15.0); Ollama delta (DEC7) implemented 2026-10-11 for v3.19.0 |
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
| DEC0-01 | Verify Jev wire format (request `{model,state,questions}`, response `{model,answers,usage}`), endpoint URL, pinned id `jev-1.13.0`, Choice ≤ 255 / Score 2–10 / Noul semantics | 0.2 d | — | ADP-001, TYP-001 | DONE |
| DEC0-02 | Verify `typesafe-sdk` names (`AsyncTypeSafeClient.system_one` vs alternatives) and pin version | 0.1 d | — | ADP-003 | DONE |
| DEC0-03 | Verify `confidence` semantics (distribution shape) and vendor bands > 0.9 / 0.5–0.9 / < 0.5 | 0.1 d | — | TYP-002 | DONE |
| DEC0-04 | Verify price ($0.042/1M input, output free), latency 70–650 ms, limits (64k; state + longest question ≤ 32k), rate limits (sources conflict) | 0.2 d | — | ADP-001, SEC-001 | DONE |
| DEC0-05 | Verify ZDR availability (enterprise-only) and data terms; OpenRouter `/api/v1/systemone` pass-through and billing | 0.2 d | — | ADP-001 | DONE |
| DEC0-06 | Verify Laya API: `Router().predict`, `predict_batch(batch_size=64)`, `predict_long(max_len=8192)`, `Agent.predict(min_confidence)` fields, `fit_abstention_thresholds`, `save_calibration`, `LayaRouter`; extras `[serve]`/`[onnx]`; Python 3.13 wheels; checkpoints + context lengths | 0.3 d | — | ADP-002, CAL-001 | DONE |
| DEC0-07 | Verify `laya-serve`: `/v1/systemone`, `/batch` ≤ 64 states, `MAX_CHOICE_OPTIONS=100`, `LAYA_API_KEY` bearer | 0.1 d | — | ADP-001 | DONE |
| DEC0-08 | Reproduce Laya benchmarks on our hardware (T4 33 ms, batched 7.2 ms/q, CPU 193–464 ms, bf16 ±0.073 vs fp16, raw ECE 0.466, > 20 options degradation); confirm RLCD fine-tune cost (~4 h on 2×T4) | 0.5 d | DEC0-06 | ADP-002, CAL-001 | BLOCKED (vendor numbers recorded in `docs/decisions.md`; local reproduction needs a GPU / live run — `tests/live/decisions`, opt-in) |
| DEC0-09 | Re-read Octomind (0.76 → 0.48), CMU cascade (0.9 pt / 41 %) and LangSmith (narrow, early) sources; capture attack strings for the DEC6 corpus | 0.2 d | — | SEC-001, INT-002 | DONE |

**Done when:** every **[verify]** fact is confirmed or corrected in `docs/decisions.md` and settings defaults.

## 3. Implementation Phases

### PHASE DEC1 — Types, port, fake, cascade

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC1-01 | `core/config.py`: all `decision_*`/`jev_*`/`laya_*` settings + `_validate_decisions` (pinned model, raise-only τ, modes) | 0.5 d | — | CFG-001 | DONE |
| DEC1-02 | `core/exceptions.py`: `DecisionError` hierarchy | 0.1 d | — | ERR-001 | DONE |
| DEC1-03 | `decisions/types.py`: enums, `OptionSpec`, `QuestionSpec` (+ `schema_hash`), `Decision`, `GatePolicy` | 0.6 d | — | TYP-001/002/003 | DONE |
| DEC1-04 | `decisions/ports.py`: `DecisionPort` + parametrized contract test suite | 0.4 d | DEC1-03 | PRT-001 | DONE |
| DEC1-05 | `adapters/fake.py` | 0.2 d | DEC1-04 | ADP-005 | DONE |
| DEC1-06 | `decisions/gate.py`: `evaluate_gate`, `shuffle_options` | 0.5 d | DEC1-03 | CAS-002/003 | DONE |
| DEC1-07 | `decisions/validation.py`: `validate_question`, forbidden lint (EN/ES) | 0.5 d | DEC1-03 | SEC-002 | DONE |
| DEC1-08 | `decisions/state.py`: `DecisionStateBuilder` (sanitize, fence, taint, authority claims, language) | 0.8 d | DEC1-03 | SEC-001/003 | DONE |
| DEC1-09 | `decisions/cascade.py`: tiers, timeout budget, escalation, `from_settings` | 1.0 d | DEC1-05..08 | CAS-001 | DONE |
| DEC1-10 | `monitoring/otel.py`: register DEC metrics | 0.2 d | — | OTEL-001 | DONE |
| DEC1-11 | Graph snapshot test: all flags off ⇒ byte-for-byte vs 3.14.0 | 0.3 d | DEC1-01 | CMP-001 | DONE |

**Done when:** fake passes the contract suite; no ACCEPTED for CRITICAL/tainted/uncalibrated/abstained; snapshot unchanged.

### PHASE DEC2 — Backends

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC2-01 | `adapters/systemone_http.py` (+ batch, model-mismatch, usage → `CostMeter`) with `respx` fixtures | 1.0 d | DEC0-01/04/07, DEC1-04 | ADP-001 | DONE |
| DEC2-02 | `adapters/laya_local.py` (deferred import, `to_thread`, preload, max_loaded, fp16 check, language support) | 1.0 d | DEC0-06, DEC1-04 | ADP-002 | DONE |
| DEC2-03 | `adapters/jev_sdk.py` behind `_call()` shim | 0.4 d | DEC0-02 | ADP-003 | DONE |
| DEC2-04 | `adapters/llm_fallback.py` via `ProviderRegistry` + `SecurePromptBuilder` + metering | 0.6 d | DEC1-04 | ADP-004 | DONE |
| DEC2-05 | `pyproject.toml` extras `[laya]`, `[jev]`; extend provider-import AST guard with the adapters exemption | 0.3 d | DEC2-02/03 | SEC-002 | DONE |
| DEC2-06 | Secret-leak test: keys absent from logs, audit, URLs, `os.environ` | 0.3 d | DEC2-01/03 | CFG-001 | DONE |

**Done when:** every adapter passes the contract suite incl. never-raise on error/timeout/over-limit.

### PHASE DEC3 — Calibration, shadow, audit

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC3-01 | `calibration.py`: loader, temperature (Laya) / isotonic (Jev), `fit_thresholds`, `apply_calibration` | 1.2 d | DEC1-03 | CAL-001 | DONE |
| DEC3-02 | `CalibrationStore` + startup enforcement in `_validate_decisions` | 0.5 d | DEC3-01 | CAL-002 | DONE |
| DEC3-03 | Spanish slice requirement + per-language mode split (`es` shadow while `en` enforces) | 0.5 d | DEC3-02 | CAL-003 | DONE |
| DEC3-04 | `shadow.py`: `ShadowRunner`, `AgreementReport`, `promotion_ready`, auto-demote | 1.0 d | DEC1-09 | SHD-001/002 | DONE |
| DEC3-05 | `audit.py`: `DecisionAuditRecord`, `audit_decision` (hash-only) | 0.4 d | DEC1-09 | AUD-001 | DONE |
| DEC3-06 | `scripts/calibrate_decision.py` CLI (labeled JSONL → calibration file) | 0.4 d | DEC3-01 | CAL-001 | DONE (`python -m prismal.decisions calibrate`) |

**Done when:** synthetic set reaches ECE ≤ 0.05; enforce w/o file fails at startup; shadow inert; audit hash-only.

### PHASE DEC4 — Routing and eval-judge integrations

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC4-01 | `integrations/route.py` + supervisor wiring after `match_intent` (off/shadow/enforce) | 1.0 d | DEC3-04 | INT-001 | DONE |
| DEC4-02 | Option-count routing (≤ 20 Laya / > 20 Jev) + benchmark of two-level Choice (open question 1) | 0.6 d | DEC4-01 | INT-001 | DONE (≤ 20 ⇒ Laya via its `supports()` cap, > 20 ⇒ Jev); two-level Choice benchmark open (live) |
| DEC4-03 | `integrations/eval_judge.py`: rubric tagging, LLM-only for math/code/logic, 5 % audit, drift alarm | 1.0 d | DEC3-04 | INT-002 | DONE |
| DEC4-04 | Integration tests: shadow parity, enforce escalation, llm_calls_avoided accounting | 0.6 d | DEC4-01/03 | INT-001/002 | DONE |

**Done when:** with fakes, enforce routes accepted turns without an LLM call and escalates the rest.

### PHASE DEC5 — HITL, tool, RAG, compaction, swarm, safety

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC5-01 | `integrations/hitl.py` bands + `hitl_gate` wiring; irreversible/critical ⇒ human | 0.8 d | DEC3-04 | INT-003 | DONE (`hitl_band`; library function — `hitl_gate`'s bypass seam is sync, no in-graph wiring) |
| DEC5-02 | `integrations/tool.py` proposal within phase-allowed set; policy still authorizes | 0.5 d | DEC3-04 | INT-004 | DONE (`propose_tool`; library function, proposal only) |
| DEC5-03 | `integrations/rag.py` CRAG chunk grading with per-chunk fallback | 0.5 d | DEC3-04 | INT-005 | DONE |
| DEC5-04 | `integrations/ctx.py` relevance scorer for TKE4 | 0.3 d | DEC3-04 | INT-006 | DONE (`make_relevance_scorer`; the compactor's `RelevanceFn` is sync — host wires precomputed scores) |
| DEC5-05 | `integrations/swarm.py` Skynet text evaluator | 0.4 d | DEC3-04 | INT-007 | DONE (`score_worker_output`; advisory library function) |
| DEC5-06 | `integrations/safety.py` + `security/decision_bridge.py` escalate-only | 0.5 d | DEC3-04 | INT-008 | DONE |

**Done when:** each integration passes off/shadow/enforce tests; HITL/SAFE monotonicity proven.

### PHASE DEC6 — Security suite, docs, release

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC6-01 | Injection corpus (EN/ES; Octomind "pre-approved", double negatives, irrelevant padding) — zero forbidden transitions | 1.0 d | DEC0-09, DEC5 | SEC-001/003 | DONE |
| DEC6-02 | Option-order shuffle test (first-option bias fixture) | 0.3 d | DEC1-06 | CAS-003 | DONE |
| DEC6-03 | Forbidden-uses tests: AST import graph + behavior (lint, critical never auto, injection flag untouched, RunawayGuard untouched) | 0.6 d | DEC1-07 | SEC-002 | DONE |
| DEC6-04 | `scripts/bench_decisions.py` cost/latency per backend (live, opt-in) | 0.4 d | DEC2 | OTEL-001 | DONE (`python -m prismal.decisions bench`, live opt-in) |
| DEC6-05 | `docs/decisions.md` (fit table, rollout, calibration, verified facts), `examples/typed_decisions.py`, notebook in `prisma-notebooks` | 1.0 d | DEC5 | — | DONE (docs + example); notebook BLOCKED (other repo, `prisma-notebooks`) |
| DEC6-06 | README + CHANGELOG + CLAUDE.md section; release 3.15.0 | 0.3 d | DEC6-05 | — | DONE |

**Done when:** full quality gate green (ruff, mypy --strict, bandit); coverage ≥ 80 % on new modules.

### PHASE DEC7 — Ollama `/v1/systemone` backend (delta `DELTA-2026-10-ollama-backend.md`, v3.19.0)

Task IDs keep the delta's numbering (its draft "DEC1-07" is **DEC1-12** here — DEC1-07 is the SEC-002 lint task above).

| ID | Task | Estimate | Dependency | SPEC | Status |
|---|---|---|---|---|---|
| DEC0-10 | Ollama endpoint check: version, `decision` capability, `num_ctx`, digest, wire shape, oversize 400 | 0.1 d | — | OLL-002 | DONE (reference host 2026-10-07; dev host re-checked 2026-10-10 + live contract test `-m ollama`) |
| DEC2-07 | `adapters/ollama.py`: sub-tiers `ollama:fast`/`ollama:accurate`, settings, preflight (`/api/version`, `/api/show`, `/api/tags`), `name@digest12`, `keep_alive`, warm-up (`DecisionRuntime.warmup`, `build_runtime`) | 0.6 d | DEC2-01 | OLL-001/002/003/006, CFG-001 | DONE |
| DEC2-08 | Limits guard (options/levels 2–26, ≤ 64 questions, ≤ 64 KiB, tokens ≤ 90 % `num_ctx`; never truncate); 400/timeout ⇒ abstain, no retry | 0.4 d | DEC2-07 | OLL-004/005 | DONE |
| DEC2-09 | Tier routing by integration + oversize fallback to `accurate` + rolling-p95 gate | 0.3 d | DEC2-07 | OLL-007, CAS-001 | DONE |
| DEC2-10 | Option descriptions → `criteria`; gate on `probabilities` (max p + margin), `confidence` logged only | 0.3 d | DEC1-03 | OLL-008/009 | DONE (already in DEC2-01 `_wire.py` / `gate.py`) |
| DEC2-11 | Batching: one question per request on `fast`, ≤ `decision_ollama_max_batch` on `accurate`, auto-disable from `batch@<name>@<digest12>.json` (> 2 pt drop) | 0.3 d | DEC2-07 | OLL-011 | DONE (the batch-accuracy record is produced by DEC3-07 data work) |
| DEC1-12 | Concrete one-fact HITL questions + deterministic OR deriving review (`hitl.py`, `rule_flags`) | 0.4 d | DEC2-10 | OLL-012, OLL-014 | DONE (safety slice of the calibration set → DEC3-07) |
| DEC6-08 | One-way safety gate: models raise, never clear; `ollama:fast` / `llm` negatives at most `confirm`; §1.4 injection + chest-pain tests | 0.3 d | DEC6-01, DEC2-07 | OLL-012 | DONE |
| DEC3-08 | Calibration keyed by local digest; stale digest ⇒ uncalibrated (shadow-only) + `decision_calibration_stale_total`; enforce startup accepts any digest record per Ollama name | 0.3 d | DEC3-01 | OLL-003, CAL-002 | DONE |
| DEC6-07 | Non-loopback guard (`decision_ollama_allow_remote`); injection corpus re-run against Nimble/Tev1 | 0.3 d | DEC6-01 | OLL-010 | WIP (guard DONE; live corpus re-run against the real models pending) |
| DEC3-07 | Benchmark on target hardware + ≥ 300 labeled Spanish cases per decision (incl. the safety slice) | 0.6 d | DEC2-07 | OLL-007, CAL-002 | WIP (data work; smoke/quantization/batch/controlled runs done — delta §1.1–§1.9) |
| DEC3-09 | Per-question latency profile, `OLLAMA_NUM_PARALLEL`, label review of the double-charge cases | 0.3 d | DEC3-07 | PLAN-LAT, CAL-002 | TODO (research / data, not code) |

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

- [x] All MUST requirements implemented with ≥ 1 test each (traceability §8).
- [x] All flags off ⇒ snapshot byte-for-byte; no new graph node. Zero forbidden transitions on the injection corpus; forbidden-use AST/behavior tests green.
- [x] Enforce refuses to start without a valid calibration record (ECE ≤ max); a failing Spanish slice keeps `es` traffic out of enforce (CAL-003); audit hash-only; no secret leaks.
- [ ] DEC0 findings recorded; docs, example, CHANGELOG done — notebook pending (other repo); DEC0-08 benchmarks not reproduced locally.

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
| DEC7 | Ollama backend delta | ~4.2 d |
| **Total** | | **~28.6 d** |

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
| SPEC-DEC-OLL-001/002/003/006 | DEC0-10, DEC2-07, DEC3-08 | `adapters/test_ollama.py`, `adapters/test_ollama_factory.py`, `adapters/test_ollama_warmup.py`, `test_calibration_digest.py`, `live/decisions/test_ollama_contract.py` |
| SPEC-DEC-OLL-004/005 | DEC2-08 | `adapters/test_ollama.py`, `test_port_contract.py` |
| SPEC-DEC-OLL-007 / CAS-001 | DEC2-09 | `adapters/test_ollama.py`, `adapters/test_ollama_factory.py`, `test_catalog_ollama.py` |
| SPEC-DEC-OLL-008/009 | DEC2-10 | `test_systemone_http.py`, `test_gate.py` |
| SPEC-DEC-OLL-010 / CFG-001 | DEC6-07 | `test_config_decisions_ollama.py` |
| SPEC-DEC-OLL-011 | DEC2-11 | `adapters/test_ollama.py` |
| SPEC-DEC-OLL-012/014 | DEC1-12, DEC6-08 | `test_hitl_concrete.py`, `test_injection_corpus.py`, `test_review_regressions.py` |
| SPEC-DEC-CAL-002 (Ollama) | DEC3-08 | `test_calibration_digest.py` |
