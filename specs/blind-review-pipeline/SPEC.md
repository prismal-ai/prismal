# Prismal Blind Review Pipeline — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` (v3.11.0, 2026-07-11) |
| **Version** | 1.0 |
| **Date** | 2026-07-10 |
| **Phase** | BRP |
| **Target package version** | `3.11.0` |
| **PLAN** | `specs/blind-review-pipeline/PLAN.md` |
| **Architecture** | `specs/blind-review-pipeline/ARCHITECTURE.md` |
| **TASKS** | `specs/blind-review-pipeline/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Async where an LLM call is involved (`spec_agent_node`,
  `implementer_agent_node`, the reviewer node bodies); pure helpers
  (`synthesize_verdicts`, `BlindnessGuard`) are sync.
- Frozen dataclasses for new value objects; reuse (do not redefine)
  `CodeIssue`/`CodeReviewReport` from `agents/subgraphs/code_review/types.py`.
- Constructors / factories accept `settings: Settings | None = None`.
- No provider SDK imports outside `prismal/providers/`.
- **Callable injection** everywhere (`spec_fn`, `implementer_fn`,
  `reviewer_a_fn`, `reviewer_b_fn`, `synthesize_fn`) so tests run without an
  LLM backend, mirroring `SkynetSupervisor`'s `plan_fn`/`evaluate_fn` and
  `build_code_review_subgraph`'s `linter_fn`/`scanner_fn`/`reviewer_fn`.
- Spec/artifact text is **user-derived or model-generated content that
  crosses an agent boundary**: it reaches any model only via
  `SecurePromptBuilder`; never f-stringed into a prompt.
- BRP must not import `prismal.mcp` / `prismal.skills`; tools come from the
  injected `ToolProviderPort` (AST-guarded, extending
  `test_no_mcp_skills_imports.py`'s coverage).
- Reviewer nodes must not read `state["messages"]` (AST-guarded by a
  dedicated test — see `ARCHITECTURE.md §4`).
- All BRP runtime state lives under `state["metadata"]["blind_review"]`.
- The existing top-level `state["iteration_count"]` field is reused for the
  correction-loop bound — BRP does not introduce a second counter.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/agents/subgraphs/blind_review_pipeline/spec_agent.py` | `make_spec_agent_node()` factory — produces the `spec_agent_node`: goal → `spec_artifact` |
| `prismal/agents/subgraphs/blind_review_pipeline/implementer_agent.py` | `make_implementer_agent_node()` factory — produces the `implementer_agent_node`: spec (+ prior issues) → `implementation_artifact` |
| `prismal/agents/subgraphs/blind_review_pipeline/reviewer_node.py` | `make_reviewer_node()` factory + `BlindnessGuard` |
| `prismal/agents/subgraphs/blind_review_pipeline/synthesis.py` | `synthesize_verdicts()` — deterministic merge |
| `prismal/agents/subgraphs/blind_review_pipeline/builder.py` | `build_blind_review_pipeline_subgraph()` / `register_blind_review_pipeline()` |
| `prismal/core/config.py` | Settings extension (`blind_review_*`) |
| `prismal/core/exceptions.py` | `BlindReviewPipelineError` hierarchy |

---

## SPEC-BRP-CFG-001: Settings (`core/config.py`, extension)

```python
class Settings(BaseSettings):
    ...
    # Phase BRP — Blind Review Pipeline (opt-in; False ⇒ graph byte-for-byte
    # unchanged, mirrors kokoro_enabled / skynet_enabled / budget_enabled).
    blind_review_pipeline_enabled: bool = Field(default=False)

    # Per-role model overrides. None ⇒ ProviderRegistry default model.
    blind_review_spec_model: str | None = Field(default=None)
    blind_review_implementer_model: str | None = Field(default=None)
    blind_review_reviewer_a_model: str | None = Field(default=None)
    blind_review_reviewer_b_model: str | None = Field(default=None)

    # Per-role tool capability filters, passed to ToolProviderPort.get_tools(
    # agent_name=..., capabilities=...).
    blind_review_spec_capabilities: list[str] = Field(
        default_factory=lambda: ["docs", "requirements"]
    )
    blind_review_implementer_capabilities: list[str] = Field(
        default_factory=lambda: ["code", "sandbox"]
    )
    blind_review_reviewer_a_capabilities: list[str] = Field(
        default_factory=lambda: ["code_review", "testing"]
    )
    blind_review_reviewer_b_capabilities: list[str] = Field(
        default_factory=lambda: ["security", "style"]
    )

    # Loop control (mirrors code_review's approval_threshold + dev_pipeline's
    # score_gate max_iterations).
    blind_review_approval_threshold: float = Field(default=0.8)
    blind_review_max_iterations: int = Field(default=3)
```

**Validation (`_validate_blind_review`, called from `Settings` model
validators, mirrors `_validate_skynet`/`_validate_budget`):**

- `0.0 <= blind_review_approval_threshold <= 1.0`, else
  `BlindReviewConfigError`.
- `blind_review_max_iterations >= 1`, else `BlindReviewConfigError`.
- If `blind_review_reviewer_a_model is not None and
  blind_review_reviewer_a_model == blind_review_reviewer_b_model`: log a
  `WARNING` (`blind_review.reviewers_share_model`) — **not** a hard failure;
  a same-model panel is a legal (if weaker) configuration.

## SPEC-BRP-ERR-001: Exceptions (`core/exceptions.py`, extension)

```python
class BlindReviewPipelineError(PrismalError):
    """Base for all Blind Review Pipeline errors."""

class BlindReviewConfigError(BlindReviewPipelineError):
    """Invalid blind_review_* settings (threshold/iterations range)."""

class BlindReviewBlindnessViolationError(BlindReviewPipelineError):
    """Raised by BlindnessGuard when a reviewer prompt appears to embed
    state["messages"] content. Defense-in-depth backstop — the primary
    control is the reviewer node's narrow input contract (ARCHITECTURE.md §4)."""
```

## SPEC-BRP-TYP-001: Value objects (`blind_review_pipeline/synthesis.py`)

Reuses `CodeIssue`/`CodeReviewReport` from
`agents.subgraphs.code_review.types` (DD-BRP-006) — no new issue/report
dataclass. One new, small value object for the merge outcome:

```python
from prismal.agents.subgraphs.code_review.types import CodeIssue, CodeReviewReport

@dataclass(frozen=True)
class SynthesisResult:
    """Deterministic merge of two independent reviewer verdicts."""
    report: CodeReviewReport      # merged issues + score + approved
    agreement: bool               # True when both verdicts' `approved` flags match
    reviewer_a_score: float
    reviewer_b_score: float
```

## SPEC-BRP-SPEC-001: `spec_agent_node` (`blind_review_pipeline/spec_agent.py`)

```python
SpecFn = Callable[[str], Awaitable[str]]   # (secure_prompt) -> spec_artifact text

def make_spec_agent_node(
    spec_fn: SpecFn | None = None,
    *,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node: goal (state["messages"]) -> spec_artifact.

    This is the ONLY node in the pipeline allowed to read state["messages"]
    in full — it is the entry point that turns free-form user intent into a
    bounded specification. Default spec_fn wires
    ProviderRegistry(settings).get_llm(settings.blind_review_spec_model)
    and tools via ToolProviderPort(agent_name="spec_agent",
    capabilities=settings.blind_review_spec_capabilities).

    Writes: state["metadata"]["blind_review"]["spec_artifact"].
    """
```

## SPEC-BRP-IMPL-001: `implementer_agent_node` (`blind_review_pipeline/implementer_agent.py`)

```python
ImplementerFn = Callable[[str, list[CodeIssue] | None], Awaitable[str]]
# (spec_artifact, prior_issues_or_None) -> implementation_artifact

def make_implementer_agent_node(
    implementer_fn: ImplementerFn | None = None,
    *,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node: spec_artifact (+ prior synthesis
    issues on retry) -> implementation_artifact.

    Reads ONLY state["metadata"]["blind_review"]["spec_artifact"] and, when
    present, state["metadata"]["blind_review"]["synthesis"]["report"]["issues"]
    — never state["messages"] directly (RF-BRP-02). Calls
    ActionInterceptor.check() before any file write / code execution, exactly
    as agents/subgraphs/dev_pipeline/developer_agent.py does today.

    Writes: state["metadata"]["blind_review"]["implementation_artifact"].
    """
```

## SPEC-BRP-REV-001: `make_reviewer_node` factory (`blind_review_pipeline/reviewer_node.py`)

```python
ReviewerFn = Callable[[str, str], Awaitable[CodeReviewReport]]
# (spec_artifact, implementation_artifact) -> CodeReviewReport — same 2-arg
# shape as code_review's reviewer_fn/scanner_fn (PLAN.md §2), generalized
# from (code, file) to (spec, artifact).

def make_reviewer_node(
    role: Literal["reviewer_a", "reviewer_b"],
    *,
    model_id: str | None,
    capabilities: list[str],
    reviewer_fn: ReviewerFn | None = None,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async, BLIND LangGraph node for one reviewer.

    Contract (RF-BRP-03/04 — statically and dynamically enforced, see
    ARCHITECTURE.md §4):
      * The returned node function's body reads exactly two fields —
        state["metadata"]["blind_review"]["spec_artifact"] and
        ["implementation_artifact"] — via a private `_extract_blind_context`
        helper. It never indexes state["messages"] and never reads the other
        reviewer's verdict.
      * Default reviewer_fn wires ProviderRegistry(settings).get_llm(model_id)
        and tools via ToolProviderPort(agent_name=role, capabilities=capabilities).
      * Before building the prompt, calls
        BlindnessGuard.assert_no_message_leak(spec_artifact, implementation_artifact)
        — raises BlindReviewBlindnessViolationError on a suspected leak.

    Writes: state["metadata"]["blind_review"][f"{role}_verdict"] (a CodeReviewReport).
    """


class BlindnessGuard:
    """Runtime backstop for RF-BRP-04 (defense in depth; the primary control
    is the narrow input contract above, not this heuristic)."""

    @staticmethod
    def assert_no_message_leak(*fields: str) -> None:
        """Best-effort check that none of *fields* look like a serialized
        AgentState.messages list (e.g. contains repeated 'HumanMessage(' /
        'AIMessage(' / 'tool_call_id' substrings). Raises
        BlindReviewBlindnessViolationError if suspicious content is found."""
```

## SPEC-BRP-SYN-001: `synthesize_verdicts` (`blind_review_pipeline/synthesis.py`)

```python
def synthesize_verdicts(
    verdict_a: CodeReviewReport,
    verdict_b: CodeReviewReport,
    *,
    approval_threshold: float,
) -> SynthesisResult:
    """Deterministic, non-LLM merge (DD-BRP-003).

    - report.issues = de-duplicated union of verdict_a.issues + verdict_b.issues
      (dedupe key: (file, line, category, description)).
    - report.score = min(verdict_a.score, verdict_b.score)  # conservative
    - report.approved = report.score >= approval_threshold
    - report.summary = a short deterministic digest (counts by severity)
    - agreement = (verdict_a.approved == verdict_b.approved)
    """
```

## SPEC-BRP-SUB-001: Builder (`blind_review_pipeline/builder.py`)

```python
_NAME = "blind_review_pipeline"

def build_blind_review_pipeline_subgraph(
    spec_fn: SpecFn | None = None,
    implementer_fn: ImplementerFn | None = None,
    reviewer_a_fn: ReviewerFn | None = None,
    reviewer_b_fn: ReviewerFn | None = None,
    synthesize_fn: Callable[[CodeReviewReport, CodeReviewReport], SynthesisResult] | None = None,
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the blind_review_pipeline SubgraphDefinition.

    Topology (ARCHITECTURE.md §3.2):
        spec_agent -> implementer -> {reviewer_a, reviewer_b} -> synthesis
                       ^                                            |
                       └──────────────── score_gate (fail) ─────────┘
        synthesis -> score_gate (pass) -> approval_seed -> human_approval -> hitl_gate -> END

    Reuses score_gate(field="blind_review.synthesis.report.score",
    threshold=settings.blind_review_approval_threshold, on_pass="approval_seed",
    on_fail="implementer", max_iterations=settings.blind_review_max_iterations)
    and the dev_pipeline HITL trio unmodified.
    """

async def register_blind_review_pipeline(
    checkpointer_path: str = "data/db/checkpoints_subgraph_blind_review_pipeline.db",
) -> None:
    """Build and register the subgraph. Idempotent — mirrors
    register_dev_pipeline/register_code_review (skip if already registered)."""
```

## SPEC-BRP-SUP-001: Supervisor + intent integration (behavior-changing; gated)

```python
# agents/intent_router.py::match_intent() — new pattern group, only matched
# when settings.blind_review_pipeline_enabled is True:
#   "revisión ciega", "blind review", "panel de revisores", "dual review"
#   -> "blind_review_pipeline"

# agents/graph.py::get_async_compiled_graph() — wires the
# "blind_review_pipeline" route only when settings.blind_review_pipeline_enabled;
# effective_valid_routes / build_system_prompt gate on the same flag.
# With the flag False, the compiled graph is byte-for-byte identical to today
# (proven by a snapshot test — TASKS.md BRP5).
```

## SPEC-BRP-AUD-001: Audit + Observability

- `AuditLogger` events (hash-first, no raw content): `blind_review.spec`,
  `blind_review.implement`, `blind_review.review_a`, `blind_review.review_b`,
  `blind_review.synthesis`, `blind_review.hitl_decision`.
- OTel spans: `blind_review.spec`, `blind_review.implement`,
  `blind_review.review_a`, `blind_review.review_b`, `blind_review.synthesis`
  (mirrors the `debate.round`/`moa.generate`/`skynet.*` span naming style).
- Counters: `prismal.blind_review_iterations_total`,
  `prismal.blind_review_reviewer_disagreement_total` (incremented when
  `SynthesisResult.agreement is False`).
