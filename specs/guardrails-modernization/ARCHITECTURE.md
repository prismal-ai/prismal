# Prismal Guardrails Modernization — Technical Design Document

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Phase** | GRD |
| **Target package version** | `3.6.0` (SemVer minor — new opt-in functionality, not yet started) |
| **PLAN** | `specs/guardrails-modernization/PLAN.md` |
| **SPEC** | `specs/guardrails-modernization/SPEC.md` |
| **TASKS** | `specs/guardrails-modernization/TASKS.md` |

---

## 1. Context

Prismal's security stack is code-complete for L3 (`NemoRailsLayer`) and L4-output (`OutputValidator`) but both are shallower than their own tests/design intent imply: L3 has never had a config to load, and output validation has no retry loop. This phase ships the missing NeMo config plus a reasoning-capable classifier rail (GRD1), and a new `StructuredOutputGuard` wrapping `guardrails-ai` for schema-first, retry-capable output enforcement (GRD2), wired through settings/OTel/tests (GRD3). Both land as opt-in extensions that reuse existing seams — `GuardrailsEngine._nemo_layer`, the `ActionInterceptor`/`OutputValidator` tool-dispatch boundary, and the Budget `budget_guard_fn` contract already consumed by `reflection_loop`, `debate_round`, `tree_of_thoughts`, `LATSAgent.search`, and `MixtureOfAgents.generate`.

## 2. Feasibility with the existing core (confirmed)

- `NemoRailsLayer.__init__` already has the exact shape needed: `RailsConfig.from_path(str(config_path))` + `LLMRails(rails_config)` (`nemo_rails.py:136-140`). Shipping `config/nemo_rails/config.yml` + `.co` files requires **zero changes** to this constructor.
- `check_input`/`check_output` already parse the `[NEMO_BLOCKED:<category>]` sentinel generically (`_parse_block_response`) — a Colang flow that calls a new custom action and prepends the same sentinel needs no Python-side parsing changes.
- NeMo Guardrails supports custom actions registered on `LLMRails` (`rails.register_action(fn, name)`); this is the standard mechanism its own bundled `content_safety_reasoning`/`llama_guard` examples use, and is the integration point for GRD1's classifier without touching `check_input`/`check_output`.
- `OutputValidator.validate_tool_args()` already isolates "validate structured args against a schema" as a discrete, composable step (`output_validator.py:51-69`) — `StructuredOutputGuard` sits *before* it in the pipeline (validate-with-reask, then still escape/path-check the final value), not instead of it.
- `budget.guard.make_budget_guard_fn(guard: BudgetGuard | None) -> Callable[[dict], Awaitable[bool]]` is already the exact shape every expensive pattern in the repo consumes (`budget_guard_fn` parameter on `reflection_loop`, `debate_round`, `tree_of_thoughts`, `LATSAgent.search`, `MixtureOfAgents.generate`) — re-ask metering reuses this verbatim, no new metering primitive needed.
- `OTelManager._register_standard_metrics()` is a flat, append-only list of `self._counters[...] = self._meter.create_counter(...)` calls (`monitoring/otel.py:219-329`) — new counters slot in the same way, grouped under a `# Guardrails Modernization (Phase GRD)` comment, mirroring the existing `# Runtime Hardening (Phase H)` / `# Cost & Budget Governance (Phase C)` sections.
- `core/exceptions.py` already has the exact precedent needed for "optional extra not installed": `MissingDependencyError(PrismalError)` (`extra_to_install=...`), used elsewhere in the repo for opt-in backends — `StructuredOutputGuard` reuses it verbatim for a missing `guardrails-ai` install rather than inventing a new exception.

No new LangGraph capability is required. No change to the `@prismal_node` middleware ordering established by `runtime-hardening` is required — both new controls are consumed *by* existing seams, not inserted as new middleware stages.

## 3. Proposed Architecture

### 3.1 New / extended modules

| Module | Purpose |
|---|---|
| `config/nemo_rails/config.yml` | **New config artifact.** Model config + `rails: input/output` wiring for `LLMRails`. Not previously shipped. |
| `config/nemo_rails/main.co` | **New config artifact.** Dialog/topical Colang flows reproducing the 5(+1) sentinel categories already asserted in `test_nemo_rails.py`. |
| `config/nemo_rails/safety_classifier.co` | **New config artifact.** Colang flow invoking the new classifier custom action, gated by `nemo_classifier_enabled`, mapping its verdict onto the same sentinel convention. |
| `prismal/security/nemo_actions.py` | **New module.** The reasoning-capable safety-classifier custom action, registered on `LLMRails` from `NemoRailsLayer.__init__` when `nemo_classifier_enabled=True`. Resolves its judgment call via `providers/` (never a raw NIM/Llama Guard SDK import here). |
| `prismal/security/nemo_rails.py` | *(extend)* `NemoRailsLayer.__init__` conditionally calls `nemo_actions.register(rails, settings)` after loading the config, when `nemo_classifier_enabled`. `check_input`/`check_output` public signatures **unchanged**. |
| `prismal/security/structured_output_guard.py` | **New module.** `StructuredOutputGuard`, `StructuredOutputVerdict`. Wraps `guardrails-ai` (`import guardrails`) lazily; degrades to `MissingDependencyError` when the extra is absent. |
| `prismal/core/config.py` | `nemo_classifier_*` + `structured_output_guard_*` settings (SPEC-GRD-CFG-001). |
| `prismal/core/exceptions.py` | `GuardrailsModernizationError` hierarchy (SPEC-GRD-ERR-001); reuses existing `MissingDependencyError` for the extra-not-installed case. |
| `prismal/monitoring/otel.py` | New counters/histograms (SPEC-GRD-OTEL-001). |
| `config/tool_policies.yaml` | *(unchanged)* — out of scope; referenced only to confirm `config/` is the right directory convention for `config/nemo_rails/`. |

All new runtime state (classifier verdict cache keys, per-run re-ask counters) lives under `state["metadata"]["guardrails_modernization"]`, mirroring the `budget`/`skynet`/`kokoro`/`hardening` isolation convention. **No live SDK objects** (`LLMRails`, `guardrails.Guard`) are ever placed in checkpointed state — `NemoRailsLayer` is already a per-process singleton resolved via `get_nemo_layer()`; `StructuredOutputGuard` instances are constructed per-call/per-run the same way the Budget meter/guard are, never serialized.

### 3.2 Data flow

**GRD1 — classifier-off (default) path — unchanged from today:**

```
user turn ──► L1 (InputSanitizer) ──► L2 (regex) ──► L3 dialog rails (config/nemo_rails/main.co)
                                                          │  (≤ 450 ms wait_for, fail-open)
                                                          ▼
                                              [NEMO_BLOCKED:<category>] | pass-through
```

**GRD1 — classifier-on (opt-in) path — new, separately timed:**

```
user/tool/RAG text ──► L1+L2 (existing) ──► L3 dialog rails
                                                │
                                    (nemo_classifier_enabled=True)
                                                ▼
                          safety_classifier.co ──execute──► nemo_actions.content_safety_reasoning()
                                                │                    │
                                                │        providers/ (ProviderRegistry) judgment call
                                                │        budget: metered like any LLM call
                                                │        timeout: nemo_classifier_timeout_seconds
                                                │        (separate from the 450 ms dialog budget)
                                                ▼
                                  [NEMO_BLOCKED:<category>] | pass-through   (same sentinel convention)
```

**GRD2 — structured output:**

```
model raw output (text) ──► StructuredOutputGuard.validate(tool_name, raw_output, schema)
                                    │
                     guardrails-ai Guard.for_pydantic(schema) validates
                                    │
                         ok? ──yes──► coerced value
                                    │
                                   no
                                    │
                     bounded re-ask loop (≤ structured_output_guard_max_reasks)
                        each attempt: budget_guard_fn(state) checked first
                        budget exhausted / max reasks hit ──► ok=False, reason, reask_count
                                    │
                     (optional, opt-in) Hub validators: PII | provenance | toxicity
                                    │
                                    ▼
                     OutputValidator.validate_tool_args(tool_name, coerced, schema)   [unchanged, still runs]
                                    │
                                    ▼
                     ActionInterceptor.check() / ToolPolicyEngine.evaluate()          [unchanged, still runs]
```

`StructuredOutputGuard` never bypasses `OutputValidator` or `ActionInterceptor` — it runs strictly *before* them in the pipeline, narrowing what reaches them, exactly as `ToolPolicyEngine` composes with `ActionInterceptor` rather than replacing it (Fase H precedent).

### 3.3 No middleware-chain changes

Neither GRD1 nor GRD2 inserts a new stage into the `@prismal_node` chain (`error_mapping → otel → logger → security → audit → tool_policy → retry → timeout → user_fn → output_validator → pii_redact`, per `runtime-hardening/ARCHITECTURE.md` §3.3). GRD1's classifier is consumed *inside* the existing `security` stage (via `GuardrailsEngine` → `NemoRailsLayer`, unchanged call sites). GRD2's `StructuredOutputGuard` is consumed *inside* the existing `output_validator` stage, as a new pre-step a node opts into when it declares a Pydantic schema for its output.

## 4. Design Decisions

### DD-GRD-001: Ship the missing config as data, not code
`config/nemo_rails/config.yml` + `*.co` are declarative YAML/Colang artifacts committed to the repo, matching the directory `NemoRailsLayer` has always expected. `NemoRailsLayer`'s loading contract (`RailsConfig.from_path`) is unchanged; this phase is "finally shipping the fixture the code already assumed," not a code rewrite.

### DD-GRD-002: Classifier verdict reuses the existing sentinel convention — no new parsing
The classifier custom action returns a bare category string (one of the configured `nemo_classifier_categories`, defaulting to the same 5 already asserted in `test_nemo_rails.py`: `violence`, `self_harm`, `illegal_activities`, `pii_request`, `competitor_disparagement`, plus `unsafe_output` on the output path) or `"safe"`. The Colang flow wraps a non-safe verdict in `[NEMO_BLOCKED:<category>]`, identical to the existing `define bot refuse ...` pattern. `_parse_block_response()` requires **zero changes** — `RF-GRD-003`.

### DD-GRD-003: The classifier gets its own timeout budget — the 450 ms contract is not silently broken
`_NEMO_TIMEOUT_SECONDS = 0.45` was designed for input rails that short-circuit *before* the main LLM call — a reasoning-capable classifier is itself an LLM/small-model call and cannot realistically complete inside that budget over a remote provider. Rather than either (a) silently blowing the existing P99 contract, or (b) claiming a false ≤500 ms number for a fundamentally different code path, this phase:
- Leaves `_NEMO_TIMEOUT_SECONDS` and the existing dialog-rail contract **untouched** for the default (`nemo_classifier_enabled=False`) path — `RF-GRD-004`.
- Introduces a **separate** `nemo_classifier_timeout_seconds` setting (proposed default: `3.0`) that only applies to the classifier action's own `asyncio.wait_for`, wrapped independently inside `nemo_actions.py`, not inside `NemoRailsLayer.check_input`/`check_output`'s existing timeout. A classifier timeout fails open (verdict = `"safe"`, audited) — `RF-GRD-005`.
- Emits a **separate** histogram (`prismal.nemo_classifier_latency_seconds`) so the two contracts are never conflated in observability either.

> **Open question:** should the default `nemo_classifier_timeout_seconds` be tuned per-provider (fast local/small classifier models could plausibly hit sub-second P99, remote frontier-model judges will not), or should it stay a single global knob? Flagging for reviewer input before TASKS execution — this spec assumes a single global setting with a conservative default.

### DD-GRD-004: `guardrails-ai` SDK import isolation follows the `nemo_rails.py` precedent, not a new rule
Rule #4 (no provider SDK imports outside `providers/`) targets *LLM provider* SDKs (`anthropic`, `openai`, `google.generativeai`, `ollama`, etc.). `nemoguardrails` is already a committed exception to a literal reading of that rule — it lives in `prismal/security/nemo_rails.py` because it is a **guardrails orchestration SDK**, not an LLM provider client, even though it internally drives LLM calls. `guardrails-ai` (`import guardrails`, the `Guard` class) is the same shape of dependency, so `structured_output_guard.py` follows the identical precedent: the SDK import is lazy/deferred (mirroring `nemo_rails.py`'s `try: from nemoguardrails import LLMRails, RailsConfig / except ImportError`), lives inside `prismal/security/`, and any *actual* LLM completion the SDK needs (its own re-ask call) is injected as a callable resolved from `providers/` (`ProviderRegistry().get_llm()`), never constructed inside `security/`.

> **Open question:** `guardrails-ai`'s `Guard` object historically expects either a raw provider SDK callable or a LiteLLM-style model string for its own re-ask invocation. Whether we inject a thin `providers/`-resolved callable (preserving isolation strictly) or accept the small isolation compromise of Guard's own environment-variable-driven model resolution needs a concrete implementation-time decision — flagged here rather than resolved unilaterally, since it affects whether `[guardrails-ai]` truly stays provider-agnostic day one.

### DD-GRD-005: Bounded re-ask, metered exactly like every other expensive pattern
`StructuredOutputGuard.validate()` accepts `budget_guard_fn: Callable[[dict], Awaitable[bool]] | None = None`, the exact type `make_budget_guard_fn()` returns — the same contract `reflection_loop`, `debate_round`, `tree_of_thoughts`, `LATSAgent.search`, and `MixtureOfAgents.generate` already consume. `None` means zero-overhead always-allow (the disabled/default path). Re-ask attempts stop at `min(structured_output_guard_max_reasks, first budget_guard_fn() == False)`.

### DD-GRD-006: `StructuredOutputGuard` composes with, never replaces, `OutputValidator`
`StructuredOutputGuard` operates on the **raw pre-validation text** the model produced, before it is even shaped into tool-call args; `OutputValidator.validate_tool_args()` still runs afterward on the coerced value for its existing schema/escape checks, and `validate_freeform()` still runs for path/command/HTML outputs. This is intentional defense-in-depth (mirrors `ToolPolicyEngine` + `ActionInterceptor` from `runtime-hardening`), not redundant — `StructuredOutputGuard` adds *retry* and *Hub-validator semantics* that `OutputValidator` was explicitly scoped out of (`runtime-hardening/PLAN.md` §5 "Out of Scope" implicitly, by never mentioning re-ask).

### DD-GRD-007: Hub validators are opt-in per-call, not global
A caller passes `hub_validators: list[str] | None` naming specific Guardrails Hub validators (e.g. `"detect_pii"`, `"provenance_llm"`, `"toxic_language"`) to apply on top of the Pydantic schema check. Default is `None` (no Hub validators) even when `structured_output_guard_enabled=True` and even when `structured_output_guard_hub_validators_enabled=True` — the latter is a master gate, the former is the per-call opt-in, so enabling the layer never silently starts running extra network-calling validators on every structured output.

### DD-GRD-008: This phase's trigger is a currently-false test assertion, not a hypothetical
`tests/integration/security/test_nemo_pipeline.py::test_nemo_rails_config_dir_exists` already asserts `Path("config/nemo_rails").is_dir()`, `config.yml` exists, and `main.co` exists — none of which is true in the repository today. GRD1's `config/nemo_rails/` deliverable is precisely what turns this existing (currently-failing-if-run) assertion into a true one; no new test contract is being invented, an existing one is finally being satisfied.

## 5. Security & cost

- The classifier only ever receives content that has already passed through L1 sanitization; per Critical Rule #1, any user-controlled text (including tool/RAG/media content already tainted by `runtime-hardening`'s `TaintRegistry`) reaches the classifier's prompt only via `SecurePromptBuilder` — never f-string concatenation.
- Classifier and re-ask LLM calls are metered through Budget exactly like any other LLM call; both are disabled by default (`nemo_classifier_enabled=False`, `structured_output_guard_enabled=False`).
- All classifier verdicts and re-ask outcomes are hash-first audited via `AuditLogger` (content hash, never raw content), consistent with every other security layer.
- `guardrails-ai` Hub validators that call external services (e.g. a hosted PII/toxicity API) are explicitly flagged in `docs/security/guardrails-modernization.md` as a data-egress consideration or an on-prem inference; opt-in per DD-GRD-007 to keep that decision visible.

## 6. Observability

### 6.1 OTel counters/histograms (registered in `OTelManager`)
- `prismal.nemo_classifier_checks_total{category,result}` (`result` ∈ `safe|blocked|timeout|error`)
- `prismal.nemo_classifier_latency_seconds` (histogram; **separate** from the existing dialog-rail latency, DD-GRD-003)
- `prismal.structured_output_reask_total{outcome}` (`outcome` ∈ `resolved|exhausted|budget_denied`)
- `prismal.structured_output_hub_validator_blocks_total{validator}`

### 6.2 Spans
- `prismal.security.nemo_classifier_check`, `prismal.security.structured_output_validate`.

## 7. Relationship to existing specs

- **`runtime-hardening/`** — owns `OutputValidator`/`ActionInterceptor`; this phase composes new controls at the same seams without modifying their public contracts.
- **`cost-budget-governance/`** — `StructuredOutputGuard` reuses `BudgetGuard`/`make_budget_guard_fn` verbatim for re-ask metering; the classifier's judgment call is metered the same way any provider-routed LLM call already is.
- **`agent-eval-harness/`** — its red-team corpus is the natural home for adversarial prompts targeting the new classifier and for schema-violation fixtures targeting `StructuredOutputGuard`; extending that corpus is out of this phase's scope but is the intended follow-up.

## 8. Testing strategy (summary; detail in `TASKS.md`)

- Unit: sentinel parsing unchanged (regression); classifier action mapped to categories on a curated corpus; classifier timeout/error fail-open; `StructuredOutputGuard` valid/invalid/re-ask/exhausted paths with a fake `budget_guard_fn`; `MissingDependencyError` path when `guardrails-ai` is absent.
- Integration: `config/nemo_rails/` loads end-to-end with a mocked `LLMRails`; `nemo_classifier_enabled=False` / `structured_output_guard_enabled=False` graph snapshot unchanged; classifier-off latency regression test (P99 ≤ 500 ms unaffected by the new code paths existing but disabled).
- Guards: no provider import outside `providers/`; `guardrails`/`nemoguardrails` imports confined to `prismal/security/` (AST test extension); no `mcp`/`skills` import in `agents/**` (reuse existing AST test).

## 9. Rollout

1. Ship `config/nemo_rails/` + `nemo_actions.py` behind `nemo_classifier_enabled=False` (the base dialog rails become active as soon as `nemo_guardrails_enabled=True`, which is itself already off by default — no behavior change until an operator opts in to either).
2. Ship `structured_output_guard.py` behind `structured_output_guard_enabled=False`; `[guardrails-ai]` extra documented but not required.
3. Enable `nemo_classifier_enabled` in `warn`-equivalent audit mode in staging first; tune `nemo_classifier_threshold`/`nemo_classifier_timeout_seconds` from the new OTel counters.
4. Enable `structured_output_guard_enabled` per-schema (opt-in `hub_validators` stay empty) before turning on any Hub validator.
