# Guardrails Modernization (Phase GRD)

Guardrails Modernization closes two concrete gaps in Prismal's 5-layer
security stack: **Layer 3 (NeMo Guardrails) shipped no config** (a code-complete
no-op) and **output enforcement had no schema-first, retry-capable framework**.
Both land as **opt-in, additive** controls — with every new flag at its
default (`False`), the compiled supervisor graph and the existing L1–L5
pipeline are byte-for-byte unchanged (snapshot-tested).

| Control | Module | Flag |
|---|---|---|
| Reasoning safety-classifier rail | `security/nemo_actions.py` | `nemo_classifier_enabled` |
| Structured-output guard (bounded re-ask) | `security/structured_output_guard.py` | `structured_output_guard_enabled` |

## GRD1 — NeMo config + reasoning safety-classifier rail

`prismal/security/nemo_rails.py::NemoRailsLayer` always had the right shape
(`RailsConfig.from_path()` + `LLMRails(...)`) but the repo never shipped
`config/nemo_rails/` — so `nemo_guardrails_enabled=True` silently did nothing.
This phase ships:

- `config/nemo_rails/config.yml` + `main.co` — dialog/topical Colang flows for
  the 5(+1) sentinel categories already asserted by
  `tests/unit/security/test_nemo_rails.py` (`violence`, `self_harm`,
  `illegal_activities`, `pii_request`, `competitor_disparagement`, plus
  `unsafe_output` on the output path).
- `config/nemo_rails/safety_classifier.co` + `security/nemo_actions.py::content_safety_reasoning` —
  a reasoning-capable classifier custom action, gated by `nemo_classifier_enabled`
  (default `False`). A non-`"safe"` verdict is wrapped in the existing
  `[NEMO_BLOCKED:<category>]` sentinel — `_parse_block_response()` needed zero changes.

```bash
export PRISMAL_NEMO_GUARDRAILS_ENABLED=true     # activates the base dialog rails
export PRISMAL_NEMO_CLASSIFIER_ENABLED=true      # activates the reasoning classifier rail
export PRISMAL_NEMO_CLASSIFIER_TIMEOUT_SECONDS=3.0
```

### Settings-driven, no hardcoded provider

`LLMRails.__init__` eagerly initializes its own main LLM from `config.yml`'s
`models:` entry unless one is injected — which would otherwise require a real
`OPENAI_API_KEY` in every environment. `NemoRailsLayer` instead resolves the
main LLM via `providers/registry.py::ProviderRegistry().get_llm()` (Rule #4)
and injects it as `LLMRails(rails_config, llm=resolved_llm)`, so the classifier
stays fully settings-driven and provider-agnostic.

### Two independent timeout contracts

- `_NEMO_TIMEOUT_SECONDS = 0.45` — the existing dialog-rail budget. Unchanged,
  and only bounds `check_input`/`check_output`'s `generate_async` call.
- `nemo_classifier_timeout_seconds` (default `3.0`) — a **separate** timeout
  wrapping only the classifier action's own judgment call inside
  `content_safety_reasoning`. A reasoning-capable classifier is itself an LLM
  call and cannot realistically complete inside a 450 ms budget over a remote
  provider — this phase deliberately does not claim a false ≤500 ms number for
  a fundamentally different code path. A classifier timeout or any exception
  fails open (verdict `"safe"`) and is audited + counted as
  `result="timeout"`/`"error"`.

## GRD2 — Structured-output guardrails via `guardrails-ai`

`security/output_validator.py::OutputValidator.validate_tool_args()` is a
single-shot, no-retry schema check. `StructuredOutputGuard`
(`security/structured_output_guard.py`) adds bounded, Budget-metered automatic
re-ask on schema violation, plus opt-in Guardrails Hub validators — and
**composes with, never replaces**, `OutputValidator`: the guard's coerced
output still flows through `OutputValidator.validate_tool_args()` for its
existing escape/path checks.

```bash
pip install "prismal-ai[guardrails-ai]"
export PRISMAL_STRUCTURED_OUTPUT_GUARD_ENABLED=true
export PRISMAL_STRUCTURED_OUTPUT_GUARD_MAX_REASKS=2
```

```python
from prismal.security.structured_output_guard import StructuredOutputGuard

guard = StructuredOutputGuard()  # raises MissingDependencyError if [guardrails-ai] absent
verdict = await guard.validate("get_weather", raw_llm_output, WeatherArgs)
if verdict.ok:
    args = verdict.coerced  # still passes through OutputValidator before tool dispatch
```

### Design: `Guard.validate()` only, no `llm_api` passthrough

`guardrails-ai`'s `Guard` object supports two shapes: `Guard.validate(raw_output)`
(pure schema validation, **no LLM call**) and `Guard.__call__(llm_api=..., num_reasks=...)`
(guardrails-ai drives its own generate+validate+reask loop). `StructuredOutputGuard`
uses only `Guard.validate()` and drives the bounded re-ask loop itself via an
injected `reask_fn` resolved from `providers/` (Rule #4) — `guardrails-ai`'s own
`llm_api` mechanism is never invoked. This keeps re-ask attempts budget-gated
per-attempt (`budget_guard_fn`, the same contract `reflection_loop`/`debate_round`/
`LATSAgent.search` already consume) and keeps the re-ask prompt routed through
`SecurePromptBuilder`, neither of which `guardrails-ai`'s own loop supports.

### Hub validators — opt-in per-call, not global

```python
verdict = await guard.validate(
    "get_weather", raw_output, WeatherArgs, hub_validators=["detect_pii"]
)
verdict.hub_findings  # e.g. ["detect_pii: ..."] — advisory, doesn't fail the schema verdict alone
```

`structured_output_guard_hub_validators_enabled` (default `False`) is the
master gate; the per-call `hub_validators` list is the opt-in on top of it —
enabling the layer never silently starts running extra network-calling
validators. Each named Hub validator (e.g. `detect_pii`, `provenance_llm`,
`toxic_language`) requires its own separate install
(`guardrails hub install hub://guardrails/<name>`); an uninstalled or unknown
name is skipped gracefully, never crashing the call.

## Observability

- `prismal.nemo_classifier_checks_total{category,result}` (`result` ∈ `safe|blocked|timeout|error`)
- `prismal.nemo_classifier_latency_seconds` (histogram, separate from the dialog-rail latency)
- `prismal.structured_output_reask_total{outcome}` (`outcome` ∈ `resolved|exhausted|budget_denied`)
- `prismal.structured_output_hub_validator_blocks_total{validator}`

## Example

See `examples/guardrails_modernization.py` — runs offline with injected fakes;
the `StructuredOutputGuard` half degrades gracefully with an install hint when
`[guardrails-ai]` is absent.

## Related

- `specs/guardrails-modernization/` — SPEC/ARCHITECTURE/PLAN/TASKS.
- `docs/security/runtime-hardening.md` — owns `OutputValidator`/`ActionInterceptor`,
  composed with here, not modified.
- `docs/budget.md` — `BudgetGuard`/`make_budget_guard_fn` reused verbatim for re-ask metering.
