# Prismal Typed Decision Models — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (2026-10-05, v3.15.0) |
| **Version** | 1.0 |
| **Date** | 2026-10-04 |
| **Phase** | DEC |
| **Target package version** | `3.15.0` (SemVer minor — new opt-in functionality) |
| **PLAN** | `specs/typed-decision-models/PLAN.md` |
| **Architecture** | `specs/typed-decision-models/ARCHITECTURE.md` |
| **TASKS** | `specs/typed-decision-models/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`; frozen dataclasses for value objects;
  constructors accept `settings: Settings | None = None`.
- `DecisionPort.decide()` and `DecisionCascade.decide()` **must not raise** for backend failures:
  errors, timeouts and over-limit inputs become `Decision(abstained=True)`. Configuration and
  forbidden-use errors raise at **registration/startup**, never per request.
- `laya` and `typesafe-sdk` are imported **only** inside `prismal/decisions/adapters/`, deferred to
  first use (extras `[laya]`, `[jev]`) — Critical Rule #4 extended; AST-guarded.
- Untrusted text reaches a decision model only through `DecisionStateBuilder`
  (SPEC-DEC-SEC-001) — the decision-model analogue of `SecurePromptBuilder` (Critical Rule #1).
- EARS requirements; every `MUST` maps to ≥ 1 task and ≥ 1 test (TASKS §8). Volatile vendor facts
  are **[verify]** and live in settings, never in code. Module layout: ARCHITECTURE §3.2.

## SPEC-DEC-TYP-001: Questions (`decisions/types.py`)
```python
class DecisionKind(str, Enum):
    CHOICE = "choice"   # one of N options (Jev ≤ 255, laya-serve ≤ 100, Laya quality ≤ 20) [verify]
    SCORE = "score"     # one of 2..10 ordered levels
    NOUL = "noul"       # yes/no -> P(true)
class RiskTier(str, Enum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"
class DecisionMode(str, Enum):
    OFF = "off"; SHADOW = "shadow"; ENFORCE = "enforce"
@dataclass(frozen=True)
class OptionSpec:
    id: str            # stable id, e.g. route name
    description: str   # developer-authored text only
@dataclass(frozen=True)
class QuestionSpec:
    id: str                                    # unique decision id, e.g. "route.supervisor"
    kind: DecisionKind
    instructions: str                          # developer-authored constant (SEC-001)
    options: tuple[OptionSpec, ...] = ()       # CHOICE only
    criteria: str = ""                         # SCORE/NOUL rubric, developer-authored
    levels: tuple[str, ...] = ()               # SCORE only, 2..10 ordered labels, low -> high
    risk_tier: RiskTier = RiskTier.MEDIUM
    language_hint: str | None = None           # "en" | "es" | None (auto-detect)
    def schema_hash(self) -> str: ...          # sha256 over canonical JSON (audit)
```
- WHEN a `SCORE` spec declares < 2 or > 10 levels, or a `CHOICE` spec declares < 2 options or
  duplicate option ids, THE SYSTEM SHALL raise `DecisionConfigError` at construction.

## SPEC-DEC-TYP-002: Decisions (`decisions/types.py`)
```python
class Outcome(str, Enum):
    ACCEPTED = "accepted"; ESCALATED = "escalated"; ABSTAINED = "abstained"
Reason = Literal["", "below_tau", "low_margin", "shuffle_inconsistent", "timeout", "backend_error",
                 "over_limit", "tainted", "uncalibrated", "model_mismatch", "unsupported"]
@dataclass(frozen=True)
class Decision:
    question_id: str
    kind: DecisionKind
    value: str | bool | None            # option id | level label | bool; None when abstained
    probabilities: Mapping[str, float]  # calibrated; option id / level / {"true","false"}
    answer_confidence: float            # max(probabilities) — P(chosen answer), calibrated
    spread_confidence: float            # vendor distribution-shape confidence (1.0 = all mass on one)
    margin: float                       # top1 - top2
    expected_level: float | None        # SCORE only: sum(p_i * i)/(n-1); analysis, never output
    model: str                          # pinned, e.g. "jev-1.13.0", "laya-multilingual@<sha>"
    backend: str                        # "rule" | "laya" | "jev" | "llm" | "fake"
    calibration_version: str | None
    latency_ms: float
    abstained: bool = False
    outcome: Outcome = Outcome.ABSTAINED
    escalated_from: tuple[str, ...] = ()   # backends tried before this one
    reason: Reason = ""
```
- THE SYSTEM SHALL compute `answer_confidence` from calibrated probabilities and SHALL NOT derive it
  from the vendor `confidence` field (mapped only to `spread_confidence`, DD-DEC-003).
- WHEN `abstained` is `True`, THE SYSTEM SHALL set `value=None` and `outcome != ACCEPTED`. For
  `NOUL`, `value = P(true) >= 0.5` and `answer_confidence = max(p, 1 - p)`.

## SPEC-DEC-TYP-003: Gate policy (`decisions/types.py`)
```python
@dataclass(frozen=True)
class GatePolicy:
    tau_accept: Mapping[RiskTier, float | None] = field(default_factory=lambda: {
        RiskTier.LOW: 0.85, RiskTier.MEDIUM: 0.90, RiskTier.HIGH: 0.97, RiskTier.CRITICAL: None})
    min_margin: float = 0.20
    shuffle_check_tiers: frozenset[RiskTier] = frozenset({RiskTier.HIGH, RiskTier.CRITICAL})
    timeout_ms: int = 800
    confirm_floor: float = 0.50     # HITL bands: [floor, tau) -> confirm; < floor -> human
    def tau(self, tier: RiskTier) -> float | None: ...   # None = never auto-accept
```
- WHERE `risk_tier` is `CRITICAL`, THE SYSTEM SHALL never return `Outcome.ACCEPTED` from any
  non-LLM backend, regardless of confidence.
- Per-decision τ from calibration (SPEC-DEC-CAL-001) MAY raise but SHALL NOT lower the tier default.

## SPEC-DEC-PRT-001: `DecisionPort` (`decisions/ports.py`)
```python
@runtime_checkable
class DecisionPort(Protocol):
    name: str          # backend id
    model: str         # pinned model version
    async def decide(self, state: Mapping[str, str], questions: Mapping[str, QuestionSpec], *,
                     policy: GatePolicy) -> Mapping[str, Decision]:
        """One Decision per question id. Must not raise: failures -> abstained."""
    def supports(self, question: QuestionSpec, *, language: str) -> bool: ...
    async def aclose(self) -> None: ...
```
- WHEN any backend raises, times out (`policy.timeout_ms`) or receives over-limit input, THE SYSTEM
  SHALL return `Decision(abstained=True, reason=...)` for every affected question id.
- `supports()` SHALL return `False` for a `CHOICE` with more options than the backend's configured
  maximum, or a language the loaded checkpoint/calibration does not cover.

## SPEC-DEC-ADP-001: Jev-compatible HTTP (`adapters/systemone_http.py`)
```python
class SystemOneHttpDecisionAdapter:
    def __init__(self, *, base_url: str, api_key: SecretStr | None, model: str,
                 name: str = "jev", max_choice_options: int = 255,
                 max_request_tokens: int = 64_000, settings: Settings | None = None) -> None: ...
    async def decide(self, state, questions, *, policy) -> Mapping[str, Decision]: ...
    async def decide_batch(self, states: Sequence[Mapping[str, str]], questions, *,
                           policy) -> list[Mapping[str, Decision]]: ...   # ≤ 64 states (laya-serve)
```
- Wire format **[verify]**: request `{"model", "state", "questions": {name: {"type",
  "instructions", "criteria"|"options"|"levels"}}}`; response `{"model": "jev-1.13.0", "answers":
  {...}, "usage": {...}}`. One adapter serves TypeSafe API, OpenRouter `/api/v1/systemone` and
  `laya-serve` `/v1/systemone` (+ `/batch`), selected by `base_url` only. OpenRouter's route is
  verified (2026-10-04): `POST https://openrouter.ai/api/v1/systemone`, 32k-token context (state +
  questions), input-only billing with `usage.cost` on every response. OpenRouter's alpha Decisions
  API (`/api/alpha/decisions`) is out of scope until it leaves alpha.
- WHEN the response `model` differs from the pinned `model`, THE SYSTEM SHALL abstain with
  `reason="model_mismatch"` and increment `prismal.decision_model_mismatch_total`.
- THE SYSTEM SHALL send the API key only as `Authorization: Bearer`; never in a URL or log.
- WHEN `usage` is present, THE SYSTEM SHALL record it via `CostMeter` (`cost_source="decision"`).

## SPEC-DEC-ADP-002: Laya in-process (`adapters/laya_local.py`)
```python
class LayaLocalDecisionAdapter:
    def __init__(self, *, checkpoint: str = "laya-multilingual", preload: bool = False,
                 max_loaded: int = 2, max_len: int = 1024, batch_size: int = 64,
                 max_choice_options: int = 20, settings: Settings | None = None) -> None: ...
    async def decide(self, state, questions, *, policy) -> Mapping[str, Decision]: ...
    async def warmup(self) -> None: ...   # forces the deferred `import laya` + load
```
- Calls `laya.Router().predict(state, questions)` / `predict_batch(..., batch_size)` inside
  `asyncio.to_thread`; `predict_long(max_len=8192)` only where the checkpoint supports it **[verify]**.
- WHEN the GPU path is active, THE SYSTEM SHALL require `LAYA_CUDA_AMP=fp16` (bf16 shifts
  probabilities by up to ±0.073 **[verify]**) and log a startup warning otherwise.
- WHEN `language != "en"` and the checkpoint is the English `laya`, `supports()` SHALL return `False`.

## SPEC-DEC-ADP-003..005: Jev SDK, LLM fallback, fake
```python
class JevSdkDecisionAdapter:            # adapters/jev_sdk.py, extra [jev]
    def __init__(self, *, api_key: SecretStr, model: str, base_url: str | None = None,
                 settings: Settings | None = None) -> None: ...
    async def decide(self, state, questions, *, policy) -> Mapping[str, Decision]: ...
class LlmFallbackDecisionAdapter:       # adapters/llm_fallback.py
    def __init__(self, *, model: str | None = None, budget_guard: BudgetGuard | None = None,
                 settings: Settings | None = None) -> None: ...
    async def decide(self, state, questions, *, policy) -> Mapping[str, Decision]: ...
class FakeDecisionAdapter:              # adapters/fake.py
    def __init__(self, scripted: Mapping[str, Decision | Callable[[Mapping[str, str]], Decision]],
                 *, name: str = "fake", model: str = "fake-1", fail: bool = False) -> None: ...
```
- ADP-003 wraps `typesafe_sdk.AsyncTypeSafeClient.system_one`; names conflict between sources
  **[verify]**, so all SDK access is isolated behind one `_call()` shim.
- ADP-004 uses `ProviderRegistry().get_llm(model).with_structured_output(...)`, prompt built only
  through `SecurePromptBuilder`, metered via `budget_guard`; probabilities are one-hot,
  `calibration_version=None`. It is the *terminal* tier with pre-DEC semantics, not gated by τ.

## SPEC-DEC-CAS-001: Cascade (`decisions/cascade.py`)
```python
RuleHook = Callable[[Mapping[str, str], QuestionSpec], Decision | None]
class DecisionCascade:
    def __init__(self, backends: Sequence[DecisionPort], *, rule_hook: RuleHook | None = None,
                 calibration: CalibrationStore | None = None, audit: AuditPort | None = None,
                 taint: TaintRegistry | None = None, settings: Settings | None = None) -> None: ...
    async def decide(self, state: DecisionState, questions: Mapping[str, QuestionSpec], *,
                     policy: GatePolicy, integration: str,
                     mode: DecisionMode = DecisionMode.ENFORCE,
                     corroborate: Callable[[Decision], bool] | None = None,
                     ) -> Mapping[str, Decision]: ...
    @classmethod
    def from_settings(cls, settings: Settings, *, audit: AuditPort | None = None,
                      vault: CredentialVaultPort | None = None) -> DecisionCascade: ...
```
- THE SYSTEM SHALL try tiers in `decision_backend_order` (default `["rule","laya","jev","llm"]`),
  skipping unsupported backends; WHEN a tier passes `evaluate_gate`, it SHALL return `ACCEPTED`.
- IF a tier abstains, times out or fails the gate, THEN THE SYSTEM SHALL escalate to the next tier
  and append it to `escalated_from`; it SHALL NEVER convert an abstention into an accept.
- WHEN all non-LLM tiers escalate and `llm` is absent from the order, THE SYSTEM SHALL return
  `outcome=ESCALATED` (caller runs its pre-DEC path). Non-LLM time is bounded by `timeout_ms`.

## SPEC-DEC-CAS-002/003: Gate evaluation and option-order shuffle (`decisions/gate.py`)
```python
def evaluate_gate(d: Decision, q: QuestionSpec, policy: GatePolicy, *, calibrated: bool,
                  tainted: bool, corroborated: bool = False) -> tuple[bool, Reason]: ...
def shuffle_options(q: QuestionSpec, *, seed: int) -> QuestionSpec: ...
```
- Accept iff `calibrated` ∧ `tau(q.risk_tier) is not None` ∧ `answer_confidence >= tau` ∧
  `margin >= min_margin` ∧ (shuffle-consistent WHERE required) ∧ ¬(`tainted` ∧ ¬`corroborated`
  for ≥ MEDIUM, SEC-003). Never raises.
- (CAS-003) WHERE `q.kind == CHOICE` and `q.risk_tier in policy.shuffle_check_tiers`, THE SYSTEM
  SHALL issue a second request with options permuted (seed derived from `state.sha256`) and accept
  only if both argmax option **ids** agree; otherwise `reason="shuffle_inconsistent"` and escalate.

## SPEC-DEC-CAL-001/002: Calibration and startup enforcement (`decisions/calibration.py`)
```python
@dataclass(frozen=True)
class LabeledExample:
    state: Mapping[str, str]; question_id: str; label: str; language: str
@dataclass(frozen=True)
class CalibrationRecord:
    decision_id: str; model: str; method: Literal["temperature", "isotonic"]
    params: Mapping[str, Any]; tau: float; target_precision: float
    ece_before: float; ece_after: float; n_by_language: Mapping[str, int]
    precision_by_language: Mapping[str, float]; created_at: str; version: str
def load_labeled_set(path: str | Path) -> list[LabeledExample]: ...      # JSONL
def fit_calibration(preds: Sequence[Decision], examples: Sequence[LabeledExample], *,
                    method: Literal["temperature", "isotonic"]) -> Mapping[str, Any]: ...
def fit_thresholds(preds: Sequence[Decision], examples: Sequence[LabeledExample], *,
                   target_precision: float, floor: float) -> float: ...
def apply_calibration(d: Decision, record: CalibrationRecord) -> Decision: ...
class CalibrationStore:
    def __init__(self, directory: str | Path) -> None: ...
    def get(self, decision_id: str, model: str) -> CalibrationRecord | None: ...
    def save(self, record: CalibrationRecord) -> Path: ...   # <dir>/<decision_id>@<model>.json
    def validate_for_enforce(self, decision_id: str, model: str,
                             languages: Sequence[str]) -> None: ...  # DecisionCalibrationError
```
- Laya → temperature scaling (raw ECE ≈ 0.466 **[verify]**); Jev → isotonic map. `fit_thresholds`
  returns the smallest τ reaching `target_precision`, clamped to ≥ the tier default (`floor`).
- WHEN the pinned model version changes, THE SYSTEM SHALL treat the old record as absent.
- (CAL-002) WHEN an integration is in `enforce` at startup and no `CalibrationRecord` exists for every
  `(decision_id, model)` it uses, THE SYSTEM SHALL raise `DecisionCalibrationError` from
  `_validate_decisions`.
- (CAL-002) IF a record has `ece_after > decision_max_ece` (default 0.05), THEN THE SYSTEM SHALL refuse enforce.

## SPEC-DEC-CAL-003: Spanish calibration requirement
- WHERE `"es"` is in `decision_languages` (default `["en","es"]`), THE SYSTEM SHALL require
  `n_by_language["es"] >= decision_min_calibration_examples` (default 300) and
  `precision_by_language["es"] >= target_precision` before enforcing `es` traffic; otherwise `es`
  traffic for that decision SHALL run in shadow while `en` may enforce.
- WHEN a state's detected language is `es`, THE SYSTEM SHALL route Laya tiers to
  `laya-multilingual`, never the English `laya`; outside `decision_languages` it SHALL escalate.

## SPEC-DEC-SHD-001/002: Shadow mode, promotion, demotion (`decisions/shadow.py`)
```python
class ShadowRunner:
    def __init__(self, cascade: DecisionCascade, *, sample_rate: float = 1.0,
                 audit: AuditPort | None = None) -> None: ...
    def launch(self, state: DecisionState, questions: Mapping[str, QuestionSpec], *,
               policy: GatePolicy, integration: str) -> asyncio.Task[Mapping[str, Decision]]: ...
    def record_reference(self, integration: str, question_id: str, reference: str | bool,
                         decision: Decision) -> None: ...
    def report(self, integration: str | None = None) -> AgreementReport: ...
@dataclass(frozen=True)
class AgreementReport:
    integration: str; n: int; n_accepted: int; agreement_all: float; agreement_accepted: float
    coverage: float; by_language: Mapping[str, float]; by_backend: Mapping[str, float]
```
- WHILE an integration is in `shadow`, THE SYSTEM SHALL return exactly the pre-DEC result to the
  graph; the decision task SHALL NOT delay it beyond `policy.timeout_ms` and its failure SHALL NOT
  surface.
- THE SYSTEM SHALL update `prismal.decision_shadow_agreement_ratio{integration,language}`.
```python
@dataclass(frozen=True)
class PromotionCriteria:  # defaults per ARCHITECTURE §3.5
    min_n: int = 2000; min_days: int = 14; min_agreement_accepted: float = 0.97
    max_ece: float = 0.05; min_es_examples: int = 300; max_es_precision_gap: float = 0.02
def promotion_ready(report: AgreementReport, criteria: PromotionCriteria) -> tuple[bool, list[str]]: ...
```
- (SHD-002) Promotion to `enforce` is an operator config change, never automatic.
- (SHD-002) WHERE `decision_auto_demote` is enabled (default), WHEN rolling audited agreement (last 500
  audited decisions) falls below the integration's floor, THE SYSTEM SHALL switch that integration to
  `shadow` in-process and increment `prismal.decision_drift_alarms_total`.

## SPEC-DEC-AUD-001: Audit (`decisions/audit.py`)
```python
@dataclass(frozen=True)
class DecisionAuditRecord:
    event: Literal["decision.made"]; integration: str; mode: DecisionMode
    question_id: str; question_schema_sha256: str; state_sha256: str
    option_order: tuple[str, ...]; shuffle_order: tuple[str, ...] | None
    backend: str; model: str; calibration_version: str | None
    probabilities: Mapping[str, float]; answer_confidence: float; spread_confidence: float
    margin: float; tau: float | None; risk_tier: RiskTier; outcome: Outcome; reason: Reason
    escalated_from: tuple[str, ...]; tainted: bool; injection_flags: tuple[str, ...]
    language: str; latency_ms: float; shadow_reference: str | None; shadow_agree: bool | None
    session_id: str | None; run_id: str | None; org_id: str | None; agent_did: str | None
    timestamp: str
def audit_decision(audit: AuditPort, record: DecisionAuditRecord) -> None: ...  # fail-open
```
- THE SYSTEM SHALL log one record per question per decision via `AuditPort.log_event` and SHALL
  NOT include raw state text, option descriptions or secrets (hashes only; probabilities to 4 dp).

## SPEC-DEC-SEC-001/003: State building, injection mitigations, taint (`decisions/state.py`)
```python
@dataclass(frozen=True)
class DecisionState:
    fields: Mapping[str, str]; tainted_fields: frozenset[str]
    injection_flags: tuple[str, ...]; language: str; sha256: str
class DecisionStateBuilder:
    def __init__(self, *, sanitizer: InputSanitizer | None = None,
                 taint: TaintRegistry | None = None, max_chars: int = 16_000) -> None: ...
    def add_trusted(self, key: str, value: str) -> DecisionStateBuilder: ...
    def add_untrusted(self, key: str, value: str, *, source: str) -> DecisionStateBuilder: ...
    def build(self) -> DecisionState: ...
```
- THE SYSTEM SHALL pass untrusted text only as **state** values (L1-sanitized, fenced, labelled
  `untrusted:<source>`), never inside `instructions`, `criteria`, option descriptions or levels.
- WHEN untrusted text contains an authority claim ("pre-approved", "already authorized", "ignore
  previous", "aprobado previamente", "ya autorizado", …) or matches `GuardrailsEngine` injection
  rules, THE SYSTEM SHALL add an `injection_flags` entry and mark the state tainted.
- IF the state exceeds the backend limit (Jev 64k request, state + longest question ≤ 32k; Laya
  512 / 1,024 tokens **[verify]**), THEN THE SYSTEM SHALL abstain for `risk_tier >= MEDIUM` rather
  than truncate silently.
- (SEC-003) IF any state field is tainted (Phase H `TaintRegistry` or SEC-001 flags) and `risk_tier >= MEDIUM`,
  THEN THE SYSTEM SHALL NOT accept unless the caller's deterministic `corroborate(decision)` returns
  `True`; otherwise `reason="tainted"` and escalate.

## SPEC-DEC-SEC-002: Forbidden uses (`decisions/validation.py`)
```python
FORBIDDEN_DECISION_USES: Final[tuple[str, ...]] = (
    "clear_injection_flag", "extend_runaway_budget", "count_or_arithmetic",
    "date_comparison", "authorize_critical_action", "decide_on_tainted_input_alone")
def validate_question(q: QuestionSpec, *, integration: str) -> None: ...  # ForbiddenDecisionError
```
- WHEN a `QuestionSpec` is registered whose instructions/criteria ask to count, compare numbers,
  compute, or compare dates (lint: "how many", "more than", "greater/less than", "sum",
  "before/after <date>", "cuántos", "mayor que", "antes de", digit comparisons), THE SYSTEM SHALL
  raise `ForbiddenDecisionError`.
- THE SYSTEM SHALL NOT allow `prismal/security/**` (except `security/decision_bridge.py`, DEC-SAFE),
  `prismal/budget/**`, `prismal/scheduler/**` or `prismal/identity/policy.py` to import
  `prismal.decisions` (AST test).
- WHEN a `Decision` reaches `ToolPolicyEngine`, `ActionInterceptor` or `PolicyPort`, THE SYSTEM
  SHALL treat it as advisory context only; a deny from those engines SHALL always win.

## SPEC-DEC-INT-001..008: Integrations (`decisions/integrations/`)
```python
async def decide_route(state: AgentState, valid_routes: Sequence[str], *,
                       cascade: DecisionCascade, settings: Settings) -> str | None: ...       # 001
async def judge_with_cascade(sample: EvalSample, rubric: Rubric, *, cascade: DecisionCascade,
                             llm_judge: Judge, settings: Settings) -> JudgeResult: ...       # 002
async def hitl_band(action: GatedAction, *, cascade: DecisionCascade,
                    settings: Settings) -> Literal["auto", "confirm", "human"]: ...          # 003
async def propose_tool(state: AgentState, allowed: Sequence[BaseTool], *,
                       cascade: DecisionCascade) -> str | None: ...                          # 004
async def grade_chunks(query: str, chunks: Sequence[Document], *,
                       cascade: DecisionCascade) -> list[bool | None]: ...                   # 005
def make_relevance_scorer(cascade: DecisionCascade,
                          ) -> Callable[[str, str], Awaitable[float | None]]: ...            # 006
async def score_worker_output(goal: str, output: str, *,
                              cascade: DecisionCascade) -> Decision: ...                     # 007
async def safety_prescreen(text: str, *, cascade: DecisionCascade) -> bool: ...  # 008: True = escalate
```

| ID | Flag | Criterion (EARS) |
|---|---|---|
| INT-001 ROUTE | `decision_routing_enabled` | WHEN `match_intent()` returns `None` and mode is `enforce`, THE SYSTEM SHALL return an accepted route from `effective_valid_routes` (+ `FINISH`) or `None` ⇒ LLM supervisor; ≤ 20 options ⇒ Laya multilingual, > 20 ⇒ Jev. |
| INT-002 EVAL | `decision_eval_judge_enabled` | WHEN a rubric is tagged `math`/`code`/`logic`, THE SYSTEM SHALL use the LLM judge only; otherwise an accepted Score/Noul replaces it and `decision_eval_audit_rate` (5 %) of accepted items are re-judged by the LLM for drift. |
| INT-003 HITL | `decision_hitl_enabled` | WHERE the action is irreversible or `CRITICAL`, THE SYSTEM SHALL return `"human"`; otherwise `auto` ≥ τ, `confirm` in `[confirm_floor, τ)`, `human` below. A decision SHALL never be the sole approver of an irreversible action. |
| INT-004 TOOL | `decision_tool_proposal_enabled` | THE SYSTEM SHALL propose only tools in the phase-allowed set; `ToolPolicyEngine` + `ActionInterceptor` still authorize every call. |
| INT-005 RAG | `decision_rag_relevance_enabled` | WHEN a chunk decision abstains, THE SYSTEM SHALL fall back to the CRAG LLM grader for that chunk. |
| INT-006 CTX | (TKE4 opt-in) | THE SYSTEM SHALL return `None` on abstention so TKE4 keeps its default policy. |
| INT-007 SWARM | `decision_swarm_eval_enabled` | THE SYSTEM SHALL score text outputs only; numeric/completeness checks stay deterministic. |
| INT-008 SAFE | `decision_safety_prescreen_enabled` | THE SYSTEM SHALL only return "escalate to `GuardrailsEngine`/NeMo" or "no opinion"; it SHALL NEVER clear or skip a guardrail (monotone stricter). |

## SPEC-DEC-CFG-001: Settings (`core/config.py`)
| Setting | Type | Default | Purpose |
|---|---|---|---|
| `decisions_enabled` | `bool` | `False` | Master flag |
| `decision_<integration>_enabled` (8, INT-001..008) | `bool` | `False` | One per integration |
| `decision_backend_order` | `list[str]` | `["rule","laya","jev","llm"]` | Cascade tiers |
| `decision_modes` | `dict[str, DecisionMode]` | `{}` (enabled ⇒ `shadow`) | `off\|shadow\|enforce` per integration |
| `jev_base_url` / `jev_api_key` | `str` / `SecretStr \| None` | `"https://api.typesafe.ai/v1"` **[verify]** / `None` | TypeSafe, OpenRouter or laya-serve; key via vault |
| `jev_model` / `jev_transport` | `str` / `Literal["http","sdk"]` | `"jev-1.13.0"` / `"http"` | Pinned (rejects `*latest*`) |
| `laya_mode` / `laya_checkpoint` | `Literal["local","http"]` / `str` | `"local"` / `"laya-multilingual"` | In-process vs `laya-serve` |
| `laya_base_url` / `laya_api_key` | `str \| None` / `SecretStr \| None` | `None` | `laya-serve` + `LAYA_API_KEY` bearer |
| `laya_preload` / `laya_max_loaded` | `bool` / `int` | `False` / `2` | Servers set preload `True` |
| `decision_timeout_ms` | `int` | `800` | Non-LLM tier budget |
| `decision_calibration_dir` | `str` | `"config/decision_calibration"` | Calibration files |
| `decision_languages` | `list[str]` | `["en","es"]` | Languages allowed to enforce |
| `decision_min_calibration_examples` / `decision_max_ece` | `int` / `float` | `300` / `0.05` | CAL-002/003 |
| `decision_tau_overrides` | `dict[str, float]` | `{}` | Raise-only τ per tier |
| `decision_shadow_sample_rate` / `decision_eval_audit_rate` | `float` | `1.0` / `0.05` | Sampling |
| `decision_auto_demote` / `decision_llm_fallback_model` | `bool` / `str \| None` | `True` / `None` | SHD-002 / LLM tier |

`_validate_decisions` rejects unknown backends/modes, unpinned `jev_model`, τ overrides below
defaults and `enforce` without calibration (CAL-002). Secrets resolve via `ConfigSourcePort` /
`CredentialVault`, are never mirrored to `os.environ` and never logged.

## SPEC-DEC-ERR-001: Exceptions (`core/exceptions.py`)
```python
class DecisionError(PrismalError): ...
class DecisionConfigError(DecisionError): ...       # startup / construction
class DecisionCalibrationError(DecisionError): ...  # enforce without valid calibration
class ForbiddenDecisionError(DecisionError): ...    # SEC-002, at registration
class DecisionBackendError(DecisionError): ...      # internal; converted to abstention
```

## SPEC-DEC-OTEL-001: Metrics (`monitoring/otel.py`)
`prismal.decisions_total{integration,backend,outcome}`, `prismal.decision_latency_ms{backend}`
(histogram), `prismal.decision_shadow_agreement_ratio{integration,language}` (gauge),
`prismal.llm_calls_avoided_total{integration}`, `prismal.decision_drift_alarms_total{integration}`,
`prismal.decision_injection_flags_total{integration}`, `prismal.decision_shuffle_inconsistent_total`,
`prismal.decision_model_mismatch_total{backend}`. Telemetry is fail-open.

## SPEC-DEC-CMP-001: Opt-in compatibility
- WHEN `decisions_enabled=False` (or every integration flag is `False`), THE SYSTEM SHALL produce a
  compiled supervisor graph and node outputs byte-for-byte identical to 3.14.0 (snapshot test).
- THE SYSTEM SHALL add no graph node in any mode; decisions run inside existing nodes.
