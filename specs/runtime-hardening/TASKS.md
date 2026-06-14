# Prismal Runtime Hardening — Implementation Plan (TASKS)

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Target package version** | `3.2.0` (SemVer minor) |
| **PLAN** | `specs/runtime-hardening/PLAN.md` |
| **SPEC** | `specs/runtime-hardening/SPEC.md` |
| **Architecture** | `specs/runtime-hardening/ARCHITECTURE.md` |

---

## 1. Implementation Summary

Runtime Hardening lands in six phases (H1–H6), each independently testable and gated behind `settings.hardening_enabled` (default `False`) so `main` stays green and the 26 agents are unaffected until the final wiring phase. Every control honours `mode ∈ {off, warn, enforce}` and reuses existing primitives (`GuardrailsEngine`, `ActionInterceptor`, `AuditLogger`, `pii_sanitizer`, `filesystem_guard`, the Budget per-run registry, `hitl_gate`). No new LangGraph capability.

Status legend: `TODO` · `WIP` · `DONE` · `BLOCKED`. (All rows `DONE` — shipped in v3.2.0.)

## 2. Prerequisites

- Reuse, do not fork: `security/guardrails.py`, `security/action_interceptor.py`, `security/audit.py`, `security/pii_sanitizer.py`, `security/filesystem_guard.py`, `agents/subgraphs/gates.py::hitl_gate`, `budget/resolve.py` (per-run registry), `monitoring/otel.py`, `agents/extension/_middleware.py`.
- Confirm the `react_loop` seam that meters Budget can also carry taint-check + runaway tick.
- Confirm `ActionInterceptor._tool_call_checker` is the integration point for `ToolPolicyEngine`.

## 3. Implementation Phases

### PHASE H1 — Settings + Exceptions + Taint

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H1-01 | `core/config.py`: `hardening_*` settings (SPEC-HRD-CFG-001) + `_validate_hardening` | 0.3 d | — | DONE |
| H1-02 | `core/exceptions.py`: `HardeningError` hierarchy (SPEC-HRD-ERR-001) | 0.2 d | — | DONE |
| H1-03 | `security/taint.py`: `Provenance`, `TaintTag`, `TaintRegistry` (SPEC-HRD-TNT-001) | 0.5 d | — | DONE |
| H1-04 | Tag untrusted content at loaders (`rag/loaders/*`, MCP results, multimodal STT/OCR/caption, `souls/`) | 0.6 d | H1-03 | DONE |

**Done when:** settings parse from `PRISMAL_*`; external content is tagged with the right `Provenance`; registry round-trips and is serializable.

### PHASE H2 — Indirect injection detector

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H2-01 | `security/indirect_injection.py`: `IndirectInjectionDetector` reusing `GuardrailsEngine` + heuristic pack | 0.7 d | H1 | DONE |
| H2-02 | Optional LLM `classifier_fn` default wiring `ProviderRegistry().get_llm()` (metered via Budget) | 0.4 d | H2-01 | DONE |
| H2-03 | Integrate in `react_loop`: untrusted tool/RAG/media results checked before re-injection | 0.6 d | H2-01 | DONE |

**Done when:** an injected RAG/tool payload is blocked (`enforce`) / flagged+sanitized (`warn`), audited with its `vector`; classifier off by default.

### PHASE H3 — Output validator + PII-out

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H3-01 | `security/output_validator.py`: `validate_tool_args` (Pydantic) + `validate_freeform` (path/command/html) | 0.7 d | H1 | DONE |
| H3-02 | Path outputs delegate to `filesystem_guard`; integrate before tool dispatch | 0.4 d | H3-01 | DONE |
| H3-03 | `security/pii_sanitizer.py`: `redact_output` filter (SPEC-HRD-PII-001) | 0.3 d | H1 | DONE |

**Done when:** invalid tool args are rejected + audited; workspace-escaping paths blocked; PII redacted from output when enabled.

### PHASE H4 — Tool policy engine

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H4-01 | `security/tool_policy.py`: `ToolPolicy`, `ToolPolicyEngine.evaluate`, `load_tool_policies` | 0.7 d | H1 | DONE |
| H4-02 | `config/tool_policies.yaml` default (ship example from this spec dir) | 0.2 d | H4-01 | DONE |
| H4-03 | Integrate with `ActionInterceptor.check()` via `_tool_call_checker`; `REQUIRE_HITL` → `hitl_gate()` | 0.5 d | H4-01 | DONE |

**Done when:** deny/allow/HITL decisions are honoured pre-action; rate limit denies the (N+1)th call; all denials audited.

### PHASE H5 — Runaway guard + integration (the only behavior-changing phase)

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H5-01 | `security/runaway.py`: `RunawayGuard.tick` (step cap + stagnation), shared per-run registry | 0.5 d | H1 | DONE |
| H5-02 | Wire `RunawayGuard` into `react_loop` next to the Budget check (graceful partial on stop) | 0.4 d | H5-01 | DONE |
| H5-03 | Extend `@prismal_node` middleware chain (taint-in / output-validator / pii) gated on `hardening_enabled` | 0.6 d | H2,H3,H4 | DONE |
| H5-04 | `monitoring/otel.py`: register the 5 security counters (SPEC-HRD-OTEL-001) | 0.3 d | H2,H3,H4,H5-01 | DONE |

**Done when:** with `hardening_enabled=False` the compiled-graph snapshot is unchanged; with `True`+`enforce` an injected flow is contained and a high-risk tool routes through HITL end-to-end.

### PHASE H6 — Tests, docs, packaging

| ID | Task | Estimate | Dependency | Status |
|---|---|---|---|---|
| H6-01 | Unit: taint registry + loader tagging | 0.4 d | H1 | DONE |
| H6-02 | Unit: injection detector over a curated corpus (direct/tool/rag/media) | 0.6 d | H2 | DONE |
| H6-03 | Unit: output validator (valid/invalid args, path escape) | 0.4 d | H3 | DONE |
| H6-04 | Unit: tool policy (allow/deny/HITL/rate-limit, glob precedence) | 0.5 d | H4 | DONE |
| H6-05 | Unit: runaway (step cap + stagnation, graceful partial) | 0.4 d | H5 | DONE |
| H6-06 | Integration: `hardening_enabled=False` graph snapshot unchanged | 0.3 d | H5 | DONE |
| H6-07 | Integration: end-to-end contained-injection + HITL-on-high-risk with fakes | 0.5 d | H5 | DONE |
| H6-08 | AST guards: no provider import outside `providers/`; no `mcp`/`skills` in `agents/**` | 0.2 d | H5 | DONE |
| H6-09 | `docs/security/runtime-hardening.md` + `examples/runtime_hardening.py` | 0.5 d | H5 | DONE |
| H6-10 | `README.md` + `CHANGELOG.md` entries; mark PLAN/SPEC/ARCHITECTURE `IMPLEMENTED` | 0.2 d | H5 | DONE |

**Done when:** `uv run pytest -m unit` green; `ruff`, `mypy --strict`, `bandit` clean; coverage ≥ project target on new modules.

## 4. Risk Register (implementation)

| Risk | Mitigation |
|---|---|
| False positives block legitimate flows | `warn` before `enforce`; tunable threshold; per-tool override |
| Classifier latency/cost | Off by default; metered via Budget; heuristic-only fast path |
| Behavior leak when disabled | Gate every wiring point; snapshot test (H6-06) |
| Non-terminating loop within budget | `RunawayGuard` step + stagnation independent of token budget |
| Policy overlaps identity governance | Identity-agnostic keys; defer DID/OAuth to identity spec |

## 5. Definition of Done (feature)

- [ ] All MUST requirements (RF-HRD-001…010) implemented and tested.
- [ ] Untrusted tool/RAG/media content is scored before re-injection (enforce contains it).
- [ ] Outputs validated/escaped before use; high-risk tools gated by policy/HITL.
- [ ] Explicit step + stagnation bound proven by tests.
- [ ] With `hardening_enabled=False`, zero behavior change (snapshot proven).
- [ ] No provider SDK / `prismal.mcp` / `prismal.skills` import in the wrong layer.
- [ ] `ruff` + `mypy --strict` + `bandit` clean; unit suite green.
- [ ] Controls mapped to OWASP LLM01/05/06/10 in `docs/security/`.

## 6. Effort Summary

| Phase | Focus | Est. |
|---|---|---|
| H1 | Settings + exceptions + taint | ~1.6 d |
| H2 | Indirect injection detector | ~1.7 d |
| H3 | Output validator + PII-out | ~1.4 d |
| H4 | Tool policy engine | ~1.4 d |
| H5 | Runaway + integration | ~1.8 d |
| H6 | Tests + docs + packaging | ~4.0 d |
| **Total** | | **~11.9 d** |
