# Prismal Guardrails Modernization — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `DRAFT` |
| **Version** | 1.0 |
| **Date** | 2026-07-04 |
| **Target package version** | `3.6.0` (SemVer minor) |
| **PLAN** | `specs/guardrails-modernization/PLAN.md` |
| **Architecture** | `specs/guardrails-modernization/ARCHITECTURE.md` |
| **TASKS** | `specs/guardrails-modernization/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Async only where an LLM/classifier call is involved (`nemo_actions.content_safety_reasoning`, `StructuredOutputGuard.validate`); pure helpers stay `sync` and must **not raise** on the hot path (fail-open, mirroring `NemoRailsLayer`'s existing exception handling).
- Frozen dataclasses for value objects (mirrors `InjectionVerdict`, `OutputVerdict`, `RunawayStatus`).
- Constructors accept `settings: Settings | None = None`.
- No provider SDK imports outside `prismal/providers/`. `nemoguardrails` and `guardrails` (guardrails-ai) SDK imports stay isolated inside `prismal/security/` (existing precedent for `nemoguardrails`; this spec extends the same precedent to `guardrails-ai` per `ARCHITECTURE.md` DD-GRD-004) and are lazy/deferred (`try/except ImportError`), never imported at module top level in a way that breaks the base install.
- No `prismal.mcp` / `prismal.skills` import inside `prismal/agents/**`.
- User/untrusted text reaches the classifier's prompt or a re-ask prompt only via `SecurePromptBuilder` — never f-string concatenation (Critical Rule #1).
- The existing `[NEMO_BLOCKED:<category>]` sentinel convention and `_parse_block_response()` in `security/nemo_rails.py` are **not modified** by this spec.
- All new runtime state lives under `state["metadata"]["guardrails_modernization"]`; no live SDK objects (`LLMRails`, `guardrails.Guard`) are ever placed in checkpointed state.
- Every classifier/re-ask control honours `mode ∈ {off, warn, enforce}` where applicable, mirroring the `hardening_mode` convention from `runtime-hardening`.
- `nemo_classifier_enabled=False` and `structured_output_guard_enabled=False` ⇒ zero wiring observable (byte-for-byte unchanged compiled graph).

---

## Module Summary

| Module | Purpose |
|---|---|
| `config/nemo_rails/config.yml` | NeMo `LLMRails` model + rails configuration (new artifact) |
| `config/nemo_rails/main.co` | Dialog/topical Colang flows for the 5(+1) existing sentinel categories (new artifact) |
| `config/nemo_rails/safety_classifier.co` | Colang flow invoking the reasoning classifier custom action (new artifact) |
| `prismal/security/nemo_actions.py` | Reasoning-capable safety-classifier custom action + registration helper |
| `prismal/security/nemo_rails.py` | *(extend)* conditional action registration in `NemoRailsLayer.__init__`; public API unchanged |
| `prismal/security/structured_output_guard.py` | `StructuredOutputGuard`, `StructuredOutputVerdict` |
| `prismal/core/config.py` | `nemo_classifier_*`, `structured_output_guard_*` settings |
| `prismal/core/exceptions.py` | `GuardrailsModernizationError` hierarchy |
| `prismal/monitoring/otel.py` | New counters/histograms |

---

## SPEC-GRD-NEMO-CFG-001: NeMo config artifacts (`config/nemo_rails/`)

```yaml
# config/nemo_rails/config.yml — illustrative shape, not the shipped content.
models:
  - type: main
    engine: <resolved via the same provider config Prismal already uses>
    model: <settings-driven, no hardcoded provider>

rails:
  input:
    flows:
      - self check input
      - content safety reasoning input     # gated by nemo_classifier_enabled at runtime
  output:
    flows:
      - self check output
      - content safety reasoning output     # gated by nemo_classifier_enabled at runtime

prompts:
  - task: self_check_input
    content: |
      Categories: violence, self_harm, illegal_activities, pii_request,
      competitor_disparagement. Respond with the matching category or "safe".
```

- `main.co` defines `define bot refuse ...` flows per category, each prefixing `[NEMO_BLOCKED:<category>]`, reproducing exactly the sentinel categories `test_nemo_rails.py` already asserts (`violence`, `self_harm`, `illegal_activities`, `pii_request`, `competitor_disparagement` on input; `unsafe_output` on output).
- `safety_classifier.co` defines a flow that `execute content_safety_reasoning(...)` (the custom action from `SPEC-GRD-NEMO-CLS-001`) and maps a non-`"safe"` verdict onto the same `[NEMO_BLOCKED:<category>]` prefix, so it is indistinguishable downstream from a dialog-rail block.
- These flows are only reachable when `nemo_classifier_enabled=True`; `config.yml`'s `rails.input.flows`/`rails.output.flows` list is rendered conditionally by `NemoRailsLayer` (or the classifier flow is a no-op passthrough when the action is unregistered — implementation detail for `TASKS.md`).

**Acceptance:** `NemoRailsLayer(Path("config/nemo_rails")).available is True` when `nemoguardrails` is installed (it already is, base dependency) — `RF-GRD-001`. `tests/integration/security/test_nemo_pipeline.py::test_nemo_rails_config_dir_exists` passes without modification.

## SPEC-GRD-NEMO-CLS-001: Reasoning safety-classifier action (`security/nemo_actions.py`)

```python
ClassifierFn = Callable[[str, Sequence[str]], Awaitable[str]]  # (text, categories) -> category | "safe"


async def content_safety_reasoning(
    text: str,
    *,
    categories: Sequence[str],
    classifier_fn: ClassifierFn | None = None,
    settings: Settings | None = None,
) -> str:
    """Score `text` against `categories` using a reasoning-capable safety classifier.

    `text` reaches the classifier's prompt only via SecurePromptBuilder. When
    `classifier_fn` is not injected, resolves a default via
    `providers.registry.ProviderRegistry().get_llm(settings.nemo_classifier_model)`
    (Rule #4 — the actual model call lives in providers/, never here). Bounded by
    `settings.nemo_classifier_timeout_seconds` (separate from the existing 450 ms
    dialog-rail budget — SPEC contract DD-GRD-003). Never raises: timeout or any
    exception returns "safe" (fail-open) and is audited + counted as
    result="timeout"|"error".
    """


def register(rails: "LLMRails", *, settings: Settings | None = None) -> None:
    """Register `content_safety_reasoning` as a NeMo custom action on `rails`,
    only when `settings.nemo_classifier_enabled`. No-op otherwise. Called from
    `NemoRailsLayer.__init__` after `RailsConfig.from_path()` succeeds."""
```

**Acceptance:** a curated corpus of harmful prompts across the 5 default categories yields the matching category with `nemo_classifier_enabled=True`; a timeout or provider error yields `"safe"` and is audited+counted, never raises into the caller — `RF-GRD-002`, `RF-GRD-005`.

## SPEC-GRD-NEMO-TIMEOUT-001: Separate classifier timeout contract

- `_NEMO_TIMEOUT_SECONDS = 0.45` in `security/nemo_rails.py` remains **unchanged** and continues to bound only the base dialog-rail `generate_async` call when `nemo_classifier_enabled=False` (the default) — `RF-GRD-004`.
- A new, independent `asyncio.wait_for(..., timeout=settings.nemo_classifier_timeout_seconds)` wraps only the classifier action's own judgment call inside `content_safety_reasoning`, never the outer `check_input`/`check_output` timeout.
- **Acceptance:** a regression test asserts `check_input`/`check_output` P99 is unaffected (still governed by `_NEMO_TIMEOUT_SECONDS`) when `nemo_classifier_enabled=False`, proving RF-GRD-004 even though the classifier code now exists in the tree.

## SPEC-GRD-SOG-001: Structured output guard (`security/structured_output_guard.py`)

```python
BudgetGuardFn = Callable[[dict[str, object]], Awaitable[bool]]   # same type as budget.guard.make_budget_guard_fn(...)
ReaskFn = Callable[[str, str], Awaitable[str]]                    # (schema_repr, prior_raw_output) -> new raw output


@dataclass(frozen=True)
class StructuredOutputVerdict:
    """Outcome of validating one piece of structured model output."""

    ok: bool
    reason: str = ""
    coerced: Any | None = None                       # validated value (e.g. .model_dump())
    reask_count: int = 0
    hub_findings: list[str] = field(default_factory=list)


class StructuredOutputGuard:
    """Schema-first structured-output validation with bounded, metered re-ask.

    Composes with (does not replace) `OutputValidator` — see ARCHITECTURE.md
    DD-GRD-006. Degrades gracefully to `MissingDependencyError` when the
    `[guardrails-ai]` extra is not installed; callers catch it and fall back to
    `OutputValidator.validate_tool_args()` alone.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        reask_fn: ReaskFn | None = None,              # default wires ProviderRegistry().get_llm()
        budget_guard_fn: BudgetGuardFn | None = None,  # None = zero-overhead always-allow
        audit: AuditLogger | None = None,
    ) -> None: ...

    async def validate(
        self,
        tool_name: str,
        raw_output: str,
        schema: type[BaseModel],
        *,
        hub_validators: list[str] | None = None,
    ) -> StructuredOutputVerdict:
        """Validate `raw_output` against `schema` via a guardrails-ai `Guard`.

        On a schema violation, re-asks up to `settings.structured_output_guard_max_reasks`
        times using `reask_fn`; before each attempt, calls `budget_guard_fn(state)` (if
        provided) and stops with ok=False, reason="budget_denied" when it returns False.
        When `hub_validators` is given and
        `settings.structured_output_guard_hub_validators_enabled`, additionally runs the
        named Guardrails Hub validators (e.g. "detect_pii", "provenance_llm",
        "toxic_language") and reports any findings in `hub_findings` without failing
        the schema verdict by itself (findings are advisory unless the caller checks
        them). Never raises: an absent `guardrails-ai` install raises
        `MissingDependencyError` at construction time (not mid-call), so callers can
        catch it once and fall back to `OutputValidator` for the lifetime of the guard.
        """
```

- Integration: a node that wants structured output constructs `StructuredOutputGuard` once (or resolves it from a per-run registry, mirroring `budget/resolve.py`'s pattern), calls `.validate()` on the raw LLM text, and **still** passes the resulting `.coerced` value through `OutputValidator.validate_tool_args()` before tool dispatch — `RF-GRD-008`.

**Acceptance:** invalid output that becomes valid after ≤ N re-asks returns `ok=True` with `reask_count > 0`; output that never validates within the bound returns `ok=False, reason="reask_exhausted"`; a `budget_guard_fn` returning `False` on the first attempt returns `ok=False, reason="budget_denied", reask_count=0` — `RF-GRD-006`, `RF-GRD-007`.

## SPEC-GRD-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `nemo_classifier_enabled` | `bool` | `False` | Master opt-in for the reasoning safety-classifier rail |
| `nemo_classifier_model` | `str \| None` | `None` | Optional model override for the classifier judgment call (falls back to the default provider) |
| `nemo_classifier_categories` | `list[str]` | `["violence", "self_harm", "illegal_activities", "pii_request", "competitor_disparagement"]` | Configurable category set (default reuses the 5 already asserted in tests) |
| `nemo_classifier_threshold` | `float` | `0.7` | Confidence threshold to treat a classifier verdict as a block (0-1) |
| `nemo_classifier_timeout_seconds` | `float` | `3.0` | Independent timeout for the classifier action only (DD-GRD-003) — never affects `_NEMO_TIMEOUT_SECONDS` |
| `structured_output_guard_enabled` | `bool` | `False` | Master opt-in for `StructuredOutputGuard` |
| `structured_output_guard_max_reasks` | `int` | `2` | Bound on automatic re-ask attempts (0 = validate once, no re-ask) |
| `structured_output_guard_hub_validators_enabled` | `bool` | `False` | Master gate for Guardrails Hub validators; per-call `hub_validators` is the opt-in on top of this |

Env prefix `PRISMAL_` (e.g. `PRISMAL_NEMO_CLASSIFIER_ENABLED`, `PRISMAL_STRUCTURED_OUTPUT_GUARD_ENABLED`). A `_validate_guardrails_modernization` model-validator rejects `nemo_classifier_categories=[]` when `nemo_classifier_enabled=True`, and rejects a negative `structured_output_guard_max_reasks`, at load time.

## SPEC-GRD-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class GuardrailsModernizationError(PrismalError): ...

class NemoClassifierError(GuardrailsModernizationError): ...
class NemoClassifierConfigError(NemoClassifierError): ...      # bad config.yml / missing action registration

class StructuredOutputGuardError(GuardrailsModernizationError): ...
class StructuredOutputReaskExhausted(StructuredOutputGuardError): ...   # surfaced only if a caller opts out of the graceful verdict path

# Reused, not reinvented, for the missing optional extra:
# MissingDependencyError(PrismalError) — raised as
# MissingDependencyError("guardrails-ai is not installed", extra_to_install="guardrails-ai")
```

## SPEC-GRD-OTEL-001: Counters/histograms (`monitoring/otel.py` extension)

`prismal.nemo_classifier_checks_total{category,result}`, `prismal.nemo_classifier_latency_seconds` (histogram), `prismal.structured_output_reask_total{outcome}`, `prismal.structured_output_hub_validator_blocks_total{validator}`.

## SPEC-GRD-PKG-001: Packaging (`pyproject.toml` extension)

```toml
[project.optional-dependencies]
guardrails-ai = [
  "guardrails-ai>=0.6.0",   # exact floor to be confirmed against the resolved uv.lock at implementation time
]
```

- Added to the `all` extras aggregate alongside the other opt-in extras.
- `nemoguardrails` requires **no** version bump — the existing base dependency (`>=0.10.1`, resolving to `0.21.0`) already supports custom actions and the bundled classifier-example pattern this spec follows.
- `[tool.mypy.overrides]`'s module list gains `"guardrails.*"` alongside the existing `"nemoguardrails.*"` entry (both are lazily imported, unstubbed third-party SDKs).

---

## Acceptance Criteria (per requirement)

| Requirement | Acceptance criterion |
|---|---|
| RF-GRD-001 | `config/nemo_rails/config.yml` + `main.co` exist; `NemoRailsLayer.available=True` with `nemo_guardrails_enabled=True` |
| RF-GRD-002 | Classifier action scores a curated corpus into the configured categories when `nemo_classifier_enabled=True` |
| RF-GRD-003 | `_parse_block_response()` unit tests pass unmodified; classifier-emitted blocks parse identically to dialog-rail blocks |
| RF-GRD-004 | Regression test: classifier-off `check_input`/`check_output` P99 unaffected by the new code's mere presence |
| RF-GRD-005 | Classifier timeout/error yields `"safe"` (fail-open), audited + counted as `result="timeout"`\|`"error"` |
| RF-GRD-006 | Invalid structured output resolves after a bounded re-ask, or reports `ok=False, reason="reask_exhausted"` |
| RF-GRD-007 | Each re-ask attempt consults the injected `budget_guard_fn`; a denial stops further attempts |
| RF-GRD-008 | `StructuredOutputGuard`'s coerced output still passes through `OutputValidator.validate_tool_args()` unchanged |
| RF-GRD-009 | `hub_validators` findings are only computed when explicitly named per-call, with the master gate also `True` |
| RF-GRD-010 | Without `[guardrails-ai]` installed, construction raises `MissingDependencyError(extra_to_install="guardrails-ai")`, never crashes the graph |
| RF-GRD-011 | Each control increments its OTel counter/histogram |
| RF-GRD-012 | Both flags `False` ⇒ compiled-graph snapshot byte-for-byte unchanged |
| RF-GRD-013 | AST guard: no provider import outside `providers/`; `nemoguardrails`/`guardrails` imports confined to `security/`; no `mcp`/`skills` import in `agents/**` |
