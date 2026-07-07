# Prismal — Guardrails Modernization (NeMo Safety-Classifier Rail + Structured-Output Guardrails)

## Strategic Plan / Product Requirements Document (PLAN)

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | GRD (Guardrails) |
| **Target package version** | `3.6.0` (SemVer minor — new opt-in functionality, not yet started) |
| **Reviewers** | Tech Lead, Security Lead, AI Architect |
| **Priority** | P2 (security hardening — closes a concrete, verified gap; not as urgent as `runtime-hardening`'s runtime-injection surface) |
| **Related** | `docs/gap-analysis-loops-harness-guardrails-2026-07.md` (§4 "Guardrails — brecha más concreta y accionable", §5 items #2/#3), `prismal/security/`, `specs/runtime-hardening/` (owns `OutputValidator`/`ActionInterceptor`, this phase composes with it), `specs/cost-budget-governance/` (re-ask metering), `specs/agent-eval-harness/` (red-team corpus should grow to cover the new classifier + structured-output paths) |

---

## 1. Executive Summary

`docs/gap-analysis-loops-harness-guardrails-2026-07.md` (2026-07-04) identifies the sharpest, most actionable gap in Prismal's otherwise mature 5-layer security stack: **Layer 3 (NeMo Guardrails) is wired in code but ships no config, and Layer "output enforcement" has no schema-first, retry-capable framework.** Both claims were re-verified directly against the repository, not just the report prose:

1. **L3 is a code-complete no-op.** `prismal/security/nemo_rails.py::NemoRailsLayer` correctly loads `RailsConfig.from_path(config_path)` and falls back to `available=False` when the directory is missing (`prismal/security/nemo_rails.py:128-133`). A `find`/`Glob` over `config/` confirms it contains only `identity_policies.yaml`, `mcp_servers.yaml`, `tool_policies.yaml` — **there is no `config/nemo_rails/` directory anywhere in the repo.** `nemoguardrails>=0.10.1` (resolving to `0.21.0` in `uv.lock`) is a **base**, non-optional dependency in `pyproject.toml` — installed on every `prismal` install for a feature that, absent this shipped config, never activates. Concretely: `tests/integration/security/test_nemo_pipeline.py::test_nemo_rails_config_dir_exists` already **hard-asserts** `Path("config/nemo_rails").is_dir()` and that `config.yml` + `main.co` exist inside it — an assertion that is false against the current repo tree. This phase is what makes that assertion true.
2. **L3 is dialog/topical Colang only — no ML/reasoning safety classifier.** `tests/unit/security/test_nemo_rails.py` already exercises 5 sentinel categories on the input path (`violence`, `self_harm`, `illegal_activities`, `pii_request`, `competitor_disparagement`) plus one output-only category (`unsafe_output`), all via the `[NEMO_BLOCKED:<category>]` sentinel parsed by `_parse_block_response()`. Today nothing populates those categories with real judgment — there is no Colang config, hence no rails at all, classifier or otherwise. NeMo Guardrails 0.21.0 ships bundled examples (`content_safety_reasoning`, `llama_guard`, `jailbreak_detection`) that wire a real classifier model behind these categories; Prismal uses none of them.
3. **`guardrails-ai` is not a dependency — only a `pyproject.toml` keyword.** Confirmed by direct inspection: `"guardrails"` appears exactly once in `pyproject.toml`, as a PyPI `keywords` entry (near line 26), never under `[project.dependencies]` or `[project.optional-dependencies]`. `prismal/security/output_validator.py::OutputValidator` is real and useful (Pydantic schema check on tool args via `validate_tool_args()`, path/command/HTML escaping via `validate_freeform()`) but it is a **single-shot, no-retry** validator: on a schema mismatch it returns `ok=False` and the caller skips the tool. There is no automatic re-ask loop, and no access to the Guardrails Hub's community validators (PII, hallucination/provenance, toxicity).

This feature, **Guardrails Modernization**, closes both gaps as **opt-in, additive** work: (GRD1) ship the missing `config/nemo_rails/` artifacts plus a reasoning-capable safety-classifier rail wired as a NeMo custom action; (GRD2) add an optional `[guardrails-ai]` extra and a new `StructuredOutputGuard` that composes with — never replaces — `OutputValidator`, adding bounded/metered re-ask and opt-in Hub validators; (GRD3) wire both into settings, `GuardrailsEngine`/`ActionInterceptor`, OTel, tests, docs, and packaging. With every new flag at its default (`False`), the compiled graph and the existing L1–L5 pipeline are byte-for-byte unchanged.

---

## 2. Context and Problem

- **The "5-category" contract already exists in tests, not in reality.** `GuardrailsEngine._nemo_layer` (`prismal/security/guardrails.py:81-83`) calls `get_nemo_layer()`, which returns `None` unless `settings.nemo_guardrails_enabled=True` — and even then, `NemoRailsLayer.__init__` only proceeds past `config_path.is_dir()` if the directory exists. It never has, so every call to `check_input`/`check_output` today returns `(False, "")` whenever the flag is flipped on, silently. There is no operator-visible signal that "enabling NeMo did nothing" beyond a debug-level log line (`nemo_rails_config_dir_missing`).
- **L2 is entirely regex.** `security/patterns/injection_patterns.yaml` covers six pattern families (`override_instructions`, `persona_injection`, `jailbreak_keywords`, `template_injection`, `data_exfiltration`, `code_injection`) with a simple `min(100, 50 + 20*n)` risk formula. This is fast and dependency-free but has no semantic understanding — a rephrased jailbreak that avoids the literal regex vocabulary passes untouched. The industry's 2026 reference architecture layers a classifier-backed content-safety rail (Llama Guard / Nemotron reasoning-safety style) precisely to catch what regex cannot; NeMo's own bundled config examples already demonstrate the pattern Prismal should adopt, not re-invent.
- **Output enforcement has no re-ask.** `OutputValidator.validate_tool_args()` is a `schema.model_validate()` call with no loop: on failure the tool call is simply skipped. There is no way for the agent to "try again with corrective feedback," which is the entire value proposition of `guardrails-ai`'s `Guard` abstraction (schema-first structured output + automatic re-ask on violation) and its Hub of community validators. `runtime-hardening`'s `SPEC-HRD-OUT-001` deliberately scoped `OutputValidator` to escape/schema-check only — re-ask was out of scope there and remains unbuilt.
- **A P99 latency contract exists for the *current* (dialog-only, LLM-free) NeMo path and must not be silently broken.** `nemo_rails.py`'s `_NEMO_TIMEOUT_SECONDS = 0.45` assumes rail evaluation that does **not** itself require a full LLM call (input rails short-circuit before the main LLM runs). A reasoning-capable safety classifier is, almost by definition, an LLM (or small classifier-model) call — it cannot realistically complete inside a 450 ms budget over a remote provider. This tension is real and must be resolved explicitly (see `ARCHITECTURE.md` DD-GRD-003), not glossed over — the existing contract stays intact for the default (classifier-off) path; the classifier gets its own, separately-measured and separately-configured budget when opted in.
- **Provider/SDK isolation must be respected for two different third-party SDKs.** `nemoguardrails` already lives inside `prismal/security/nemo_rails.py` (a deliberate, existing precedent: a non-"LLM provider" SDK that itself orchestrates LLM calls, isolated in `security/` rather than `providers/`). `guardrails-ai` (imported as `guardrails`) is the same shape of dependency — a guardrails orchestration SDK, not a raw model client — and this phase follows the same precedent rather than inventing a new isolation rule (see `ARCHITECTURE.md` DD-GRD-004). Any *actual* LLM call either SDK needs to make (the classifier's judgment call, a re-ask completion) is still injected from `prismal/providers/` per Rule #4 — only the SDK's own orchestration object construction stays in `security/`.

> **Scope boundary vs. `runtime-hardening`.** `OutputValidator` and `ActionInterceptor` are owned by `specs/runtime-hardening/`. This phase does not fork or replace either — `StructuredOutputGuard` is a new, separate module that composes with `OutputValidator` at the same seam (tool-arg validation before dispatch), the same way `ToolPolicyEngine` composes with `ActionInterceptor` rather than replacing it.

---

## 3. Target Users

- **Security Lead:** a real, ML-backed content-safety layer behind L3 (closing the "regex-only" critique), with an auditable, bounded-latency contract.
- **AI Engineer:** a schema-first, retry-capable way to guarantee structured tool/agent output without hand-rolling re-ask loops.
- **Platform Host (`prismal-server`):** can enable a stronger default safety posture (`nemo_classifier_enabled`) and stronger output guarantees (`structured_output_guard_enabled`) per tenant without code changes.
- **Compliance:** a documented mapping from "Prismal ships NeMo + Guardrails AI" (already implied by `pyproject.toml` keywords/deps) to what is actually active and why.

---

## 4. Goals and Success Metrics

| Goal | Metric | Target |
|---|---|---|
| L3 stops being a silent no-op | `config/nemo_rails/config.yml` loads; `NemoRailsLayer.available=True` when `nemo_guardrails_enabled=True` in a default install | Enforced |
| L3 gains real classification | A reasoning-capable safety-classifier rail scores content against configurable categories (default: the 5 categories already asserted in tests) | Enforced (opt-in `nemo_classifier_enabled`) |
| Existing latency contract preserved | Classifier-off (default) path P99 stays ≤ 500 ms, unchanged from today | 100% (regression test) |
| Structured output gets retries | `StructuredOutputGuard` bounds and meters automatic re-ask on schema violation | Enforced (opt-in `structured_output_guard_enabled`) |
| Hub validators available, not mandatory | PII / hallucination-provenance / toxicity Hub validators callable per-schema | Available, zero cost when unused |
| Re-ask cost is visible | Every re-ask LLM call is metered through the existing Budget system | 100% of re-asks |
| Observability | New OTel counters for classifier verdicts and re-ask outcomes | Emitted |
| Backward-compat | Both flags default `False` ⇒ compiled graph and L1–L5 pipeline byte-for-byte unchanged | 100% (snapshot test) |

---

## 5. Scope

### In Scope

**GRD1 — NeMo config + reasoning safety-classifier rail**
- `config/nemo_rails/config.yml` + Colang `.co` flow files (dialog/topical rails, matching the 5(+1) categories already asserted in tests) — the artifact `NemoRailsLayer` has always expected but never received.
- A new NeMo **custom action** (reasoning-capable content-safety classifier, following the intent of NeMo's bundled `content_safety_reasoning`/`llama_guard` examples — described here, not vendored) invoked from Colang, gated by `nemo_classifier_enabled`.
- Preserve the existing `[NEMO_BLOCKED:<category>]` sentinel convention and `_parse_block_response()` untouched.
- Preserve the existing ≤ 500 ms P99 / fail-open contract for the **default** (classifier-off) dialog-rail path; the classifier gets its own explicit, separately-configured timeout budget.

**GRD2 — Structured-output guardrails via `guardrails-ai`**
- New optional extra `[guardrails-ai]`.
- New module `prismal/security/structured_output_guard.py` — `StructuredOutputGuard`, composing with (not replacing) `OutputValidator`.
- Pydantic-schema-first validation with bounded, Budget-metered automatic re-ask.
- Optional opt-in surfacing of Guardrails Hub validators (PII, hallucination/provenance, toxicity).
- Graceful degradation (`MissingDependencyError`) when the extra is not installed.

**GRD3 — Integration, settings, OTel, tests, docs, packaging**
- New `nemo_classifier_*` and `structured_output_guard_*` settings.
- New exceptions under a `GuardrailsModernizationError` hierarchy.
- New OTel counters/histograms.
- Unit + integration tests (including a regression test proving the classifier-off latency contract is unchanged).
- `docs/security/guardrails-modernization.md`, `examples/guardrails_modernization.py`.
- `README.md` / `CHANGELOG.md` entries — written as **planned**, not shipped.

### Out of Scope

- Rewriting `GuardrailsEngine`'s L2 regex layer (`security/patterns/injection_patterns.yaml` stays as-is; the classifier is additive, not a replacement).
- Vendoring NVIDIA's example Colang/Python files verbatim — this phase describes and implements the *intent* (reasoning-capable classifier rail) with Prismal's own config and action code.
- Rewriting `OutputValidator`, `ActionInterceptor`, or the `runtime-hardening` middleware chain — `StructuredOutputGuard` is a new, separate seam that composes with them.
- A general-purpose "LLM Guard"-style L1 rewrite (fast PII/prompt-injection scanner) — noted in the gap analysis §4 as the *industry's* Layer 1 of a 3-layer reference architecture, but Prismal's existing `InputSanitizer` + L2 regex already occupy that slot; revisiting L1's implementation is a separate, future decision.
- Any change to the `agent-eval-harness` red-team runner itself (only its *corpus* is expected to grow to exercise these new paths, tracked as a follow-up in that spec, not this one).

---

## 6. Functional Requirements (summary)

| ID | Requirement | Priority |
|---|---|---|
| RF-GRD-001 | `config/nemo_rails/config.yml` + Colang flows ship in the repo; `NemoRailsLayer.available=True` when `nemo_guardrails_enabled=True` | `MUST` |
| RF-GRD-002 | A reasoning-capable safety-classifier rail scores content against configurable categories, gated by `nemo_classifier_enabled` | `MUST` |
| RF-GRD-003 | The existing `[NEMO_BLOCKED:<category>]` sentinel convention and `_parse_block_response()` are unchanged | `MUST` |
| RF-GRD-004 | The classifier-off (default) dialog-rail path keeps its ≤ 500 ms P99 / fail-open contract, unchanged from today | `MUST` |
| RF-GRD-005 | The classifier path has its own explicit, separately-configured timeout and fails open on timeout/error | `MUST` |
| RF-GRD-006 | `StructuredOutputGuard.validate()` validates model output against a Pydantic schema with bounded automatic re-ask | `MUST` |
| RF-GRD-007 | Every re-ask LLM call is metered through the existing Budget system (`budget_guard_fn` contract) | `MUST` |
| RF-GRD-008 | `StructuredOutputGuard` composes with `OutputValidator` (both run; neither is bypassed) | `MUST` |
| RF-GRD-009 | Guardrails Hub validators (PII, hallucination/provenance, toxicity) are opt-in per-schema, zero cost when unused | `SHOULD` |
| RF-GRD-010 | Absence of the `[guardrails-ai]` extra degrades gracefully (`MissingDependencyError`), never crashes the graph | `MUST` |
| RF-GRD-011 | OTel counters/histograms for classifier verdicts and re-ask outcomes | `SHOULD` |
| RF-GRD-012 | `nemo_classifier_enabled=False` and `structured_output_guard_enabled=False` ⇒ compiled graph byte-for-byte unchanged | `MUST` |
| RF-GRD-013 | No provider SDK import outside `providers/`; `guardrails`/`nemoguardrails` SDK imports isolated inside `security/`; no `mcp`/`skills` import in `agents/**` | `MUST` |

---

## 7. Risks and Mitigations (summary)

| Risk | Mitigation |
|---|---|
| Classifier LLM call blows the existing 450 ms P99 contract | Contract only applies to the classifier-off default path; classifier gets its own, explicitly slower, separately-measured timeout (RF-GRD-005) |
| Classifier adds meaningful per-request cost | Off by default; metered via Budget when it does invoke an LLM; heuristic/local-model option left open for hosts that need low cost |
| `guardrails-ai`'s own re-ask mechanism makes an uncontrolled number of LLM calls | Bounded by `structured_output_guard_max_reasks`; each attempt goes through the injected `budget_guard_fn`, which can veto further attempts |
| False positives from the classifier block legitimate flows | `hardening_mode`-style `off\|warn\|enforce` convention reused for the classifier; `warn` before `enforce` rollout |
| `guardrails-ai` API surface volatility (young-ish ecosystem) | `StructuredOutputGuard` is a thin adapter; the public Prismal-facing interface (`StructuredOutputVerdict`, `.validate()`) is stable even if the underlying `Guard` call shape changes |
| Duplicating `OutputValidator`'s schema check | `StructuredOutputGuard` explicitly composes with, never replaces, `OutputValidator`; the final coerced value still passes through `OutputValidator`'s escape/path/command checks |
| Behavior leak when disabled | Every wiring point gated on its own flag; snapshot test (mirrors `hardening_enabled`/`budget_enabled` precedent) |

---

## 8. Dependencies

- `prismal/security/` (`guardrails.py`, `nemo_rails.py`, `output_validator.py`, `audit.py`, `prompt_builder.py`).
- `prismal/providers/` (classifier judgment call + any re-ask LLM call — provider isolation rule #4).
- `prismal/budget/` (`BudgetGuard`, `make_budget_guard_fn` — re-ask metering, same contract `reflection_loop`/`debate_round`/`LATSAgent.search` already use).
- `prismal/monitoring/otel.py` (new counters/histograms).
- `nemoguardrails` (already a base dependency, 0.21.0) — no version change required, only the missing config artifact and a new custom action.
- New optional dependency: `guardrails-ai` (imported as `guardrails`), behind `[guardrails-ai]`.
- `specs/runtime-hardening/` (`OutputValidator`, `ActionInterceptor` — composed with, not modified in their own contracts).
- `specs/cost-budget-governance/` (`Budget`, `CostMeter`, `BudgetGuard` — reused verbatim for re-ask metering).
- `specs/agent-eval-harness/` (red-team corpus should grow to cover the classifier + structured-output paths — tracked there).

---

## 9. Next Steps

Implement per `TASKS.md` (phases GRD1–GRD3). Ship both layers behind their own opt-in flags, default `False`. Validate the classifier-off latency contract with a regression test before enabling `nemo_classifier_enabled` anywhere. Coordinate with `agent-eval-harness` to extend its red-team corpus once both layers are live.

---

## Change History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-07-04 | Ernesto Crespo | Initial draft from gap-analysis (docs/gap-analysis-loops-harness-guardrails-2026-07.md, item #2/#3) |
