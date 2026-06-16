# Prismal Agent Evaluation & Reliability Harness — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-13 |
| **Target package version** | `3.3.0` (SemVer minor) |
| **PLAN** | `specs/agent-eval-harness/PLAN.md` |
| **Architecture** | `specs/agent-eval-harness/ARCHITECTURE.md` |
| **TASKS** | `specs/agent-eval-harness/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- `prismal/eval/` is a sibling of the runtime; it imports the **public** graph entry point and the **public ports** only — never `prismal.agents.*` internals, never `prismal.mcp`/`prismal.skills` directly.
- No provider SDK imports outside `prismal/providers/` (the LLM-judge wires through `providers/`).
- Deterministic by default: `EvalRunner` uses `build_test_runtime` fakes + seeds; real-model runs are opt-in (`live_api`).
- Frozen dataclasses / Pydantic models for value objects; pure scoring functions must not raise.
- The harness **does not bypass** security: adversarial payloads enter through the normal agent path.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/eval/types.py` | `EvalCase`, `EvalSet`, `Assertion`, `Trajectory`, `CaseResult`, `Scorecard` |
| `prismal/eval/runner.py` | `EvalRunner` |
| `prismal/eval/trajectory.py` | trajectory capture + metrics |
| `prismal/eval/assertions.py` | assertion evaluators |
| `prismal/eval/judges.py` | LLM-as-judge + groundedness |
| `prismal/eval/regression.py` | baseline compare + gate |
| `prismal/eval/redteam/` | adversarial catalog + loader |
| `prismal/eval/report.py` | JSON/Markdown/Langfuse report |
| `prismal/eval/__main__.py` | CLI |

---

## SPEC-EVL-TYP-001: Value objects (`eval/types.py`)

```python
class AssertionType(str, Enum):
    EXACT = "exact"
    SEMANTIC = "semantic"
    LLM_JUDGE = "llm_judge"
    TOOL_USAGE = "tool_usage"
    GROUNDEDNESS = "groundedness"
    SECURITY = "security"        # adversarial containment


@dataclass(frozen=True)
class Assertion:
    type: AssertionType
    # exact/semantic
    expected: str | None = None
    min_score: float | None = None          # semantic/llm_judge/groundedness
    # llm_judge
    rubric: str | None = None
    # tool_usage
    must_call: list[str] = field(default_factory=list)
    never_call: list[str] = field(default_factory=list)
    max_steps: int | None = None
    # security
    attack_class: str | None = None         # injection|tool_abuse|exfiltration|jailbreak
    must_block: bool = True


@dataclass(frozen=True)
class EvalCase:
    id: str
    input: str
    assertions: list[Assertion]
    setup: dict = field(default_factory=dict)   # tool_provider/vector_store/seed/flags
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvalSet:
    suite: str
    cases: list[EvalCase]

    @classmethod
    def from_yaml(cls, path: str) -> "EvalSet": ...


@dataclass(frozen=True)
class TrajectoryStep:
    node: str
    role: str
    content: str = ""
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_ok: bool | None = None


@dataclass(frozen=True)
class Trajectory:
    case_id: str
    final_answer: str
    steps: list[TrajectoryStep]
    visited_nodes: list[str]
    tool_calls: int
    tool_errors: int
    cost_usd: float
    tokens: int
    latency_ms: float
    terminated: bool


@dataclass(frozen=True)
class AssertionResult:
    assertion: Assertion
    passed: bool
    score: float | None
    detail: str = ""


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    trajectory: Trajectory
    assertion_results: list[AssertionResult]


@dataclass(frozen=True)
class Scorecard:
    suite: str
    version: str
    pass_rate: float
    avg_steps: float
    tool_error_rate: float
    avg_cost_usd: float
    avg_latency_ms: float
    cases: list[CaseResult]
```

## SPEC-EVL-RUN-001: Runner (`eval/runner.py`)

```python
class EvalRunner:
    def __init__(
        self,
        *,
        graph_factory: Callable[..., Awaitable[Any]] | None = None,  # default: get_async_compiled_graph
        runtime_factory: Callable[..., Any] | None = None,           # default: build_test_runtime (fakes)
        judge: "Judge | None" = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def run_case(self, case: EvalCase) -> CaseResult:
        """Build a test runtime per case.setup, run case.input through the graph
        via astream, capture the Trajectory, evaluate every assertion, return a
        CaseResult. Deterministic with fakes + seed; never raises (failures become
        passed=False with detail)."""

    async def run_set(self, eval_set: EvalSet) -> Scorecard: ...
```

## SPEC-EVL-TRJ-001: Trajectory capture (`eval/trajectory.py`)

```python
async def capture_trajectory(graph, case: EvalCase) -> Trajectory:
    """Drive graph.astream(...) for one case; collect messages, tool-calls,
    visited nodes, cost/latency (via monitoring/), and termination. No agent
    instrumentation — purely consumes the public event stream."""
```

## SPEC-EVL-ASR-001: Assertions (`eval/assertions.py`)

```python
def assert_exact(traj: Trajectory, a: Assertion) -> AssertionResult: ...
def assert_semantic(traj: Trajectory, a: Assertion, *, embeddings) -> AssertionResult: ...   # cosine via embeddings port
def assert_tool_usage(traj: Trajectory, a: Assertion) -> AssertionResult: ...                 # must/never call + max_steps
async def assert_llm_judge(traj: Trajectory, a: Assertion, *, judge) -> AssertionResult: ...  # rubric score
async def assert_groundedness(traj: Trajectory, a: Assertion, *, judge) -> AssertionResult: ...  # answer ⊂ retrieved ctx
def assert_security(traj: Trajectory, a: Assertion) -> AssertionResult:
    """For an adversarial case: pass iff the attack was contained — the malicious
    tool was NOT executed AND a security signal fired (guardrail/injection/policy).
    Reads the audit log + OTel counters surfaced on the trajectory."""
```

## SPEC-EVL-JDG-001: Judges (`eval/judges.py`)

```python
class Judge:
    def __init__(self, *, judge_fn: Callable[[str], Awaitable[float]] | None = None,
                 settings: Settings | None = None) -> None:
        """Default judge_fn wires ProviderRegistry().get_llm() (model-agnostic),
        metered via Budget. Used by llm_judge and groundedness assertions."""

    async def score(self, *, rubric: str, answer: str, context: str = "") -> float: ...
```

## SPEC-EVL-REG-001: Regression (`eval/regression.py`)

```python
@dataclass(frozen=True)
class RegressionResult:
    passed: bool
    regressions: list[str]      # human-readable per-metric regressions


def compare(current: Scorecard, baseline: Scorecard, *, tolerance: float = 0.02) -> RegressionResult:
    """Fail if pass_rate drops, or avg_steps / tool_error_rate / cost rise beyond
    tolerance vs baseline. Baselines are committed under tests/eval/baselines/."""
```

## SPEC-EVL-RED-001: Adversarial suite (`eval/redteam/`)

```python
def load_redteam_corpus(path: str | None = None) -> EvalSet:
    """Load the adversarial corpus (see redteam-corpus.example.yaml): each case is
    an EvalCase with a SECURITY assertion (attack_class + must_block). Classes:
    injection (direct + indirect), tool_abuse, exfiltration, jailbreak,
    system_prompt_leak."""
```

Each red-team case runs against the **real graph** (with Phase H controls enabled) and asserts containment via `assert_security`.

## SPEC-EVL-RPT-001: Report (`eval/report.py`)

```python
def to_json(card: Scorecard) -> str: ...
def to_markdown(card: Scorecard) -> str: ...           # human scorecard per release
def to_langfuse(card: Scorecard, *, settings: Settings | None = None) -> None: ...  # optional export
```

## SPEC-EVL-CLI-001: CLI (`eval/__main__.py`)

```
python -m prismal.eval run     --suite tests/eval/sets/rag_groundedness.yaml [--live-api] [--baseline ...] [--json out.json]
python -m prismal.eval redteam --corpus tests/eval/redteam/corpus.yaml
python -m prismal.eval gate    --current run.json --baseline baseline.json --tolerance 0.02
```

`pytest -m eval` / `pytest -m redteam` run the same suites with fakes.

## SPEC-EVL-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `eval_default_mode` | `str` | `"fakes"` | `fakes` \| `live_api` |
| `eval_judge_model` | `str` | `""` | Optional LLM-judge model override |
| `eval_regression_tolerance` | `float` | `0.02` | Per-metric regression tolerance |
| `eval_seed` | `int` | `0` | Global seed for reproducibility |
| `eval_langfuse_export` | `bool` | `False` | Export scorecards to Langfuse |

Env prefix `PRISMAL_` (e.g. `PRISMAL_EVAL_DEFAULT_MODE`). The harness adds **no** runtime settings that affect the agents.

## SPEC-EVL-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class EvalError(PrismalError): ...
class EvalSetError(EvalError): ...       # malformed eval-set / corpus
class RegressionGateFailed(EvalError): ...   # raised by the CI gate
```

## Acceptance Criteria (per requirement)

| Requirement (PLAN) | Acceptance criterion |
|---|---|
| RF-EVL-001 | `EvalSet.from_yaml` loads cases with composable assertions; bad set → `EvalSetError` |
| RF-EVL-002 | `EvalRunner.run_case` runs the graph and returns a populated `Trajectory` (cost/latency included) |
| RF-EVL-003 | exact/semantic/llm_judge/tool_usage/groundedness assertions each pass+fail on crafted cases |
| RF-EVL-004 | `Scorecard` → JSON+MD; `compare()` fails on a seeded regression beyond tolerance |
| RF-EVL-005 | A red-team injection case is **contained** (malicious tool not executed; security signal fired) |
| RF-EVL-006 | `python -m prismal.eval run` and `pytest -m eval` run with fakes (no `live_api`) |
| RF-EVL-007 | Same seed + fakes ⇒ identical scorecard across runs; `live_api` opt-in |
| (cross) | Harness imports only public graph entry + ports; AST guard: no `agents.*` internals, no `mcp`/`skills` |
