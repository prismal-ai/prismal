"""Value objects for the evaluation harness (SPEC-EVL-TYP-001).

Frozen dataclasses for eval-sets, captured trajectories, and scorecards. These
carry no behaviour beyond construction and YAML loading (``EvalSet.from_yaml``);
scoring lives in :mod:`prismal.eval.assertions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class AssertionType(StrEnum):
    """The kinds of checks an :class:`Assertion` can express."""

    EXACT = "exact"
    SEMANTIC = "semantic"
    LLM_JUDGE = "llm_judge"
    TOOL_USAGE = "tool_usage"
    GROUNDEDNESS = "groundedness"
    SECURITY = "security"  # adversarial containment


@dataclass(frozen=True)
class Assertion:
    """A single, composable success criterion for an :class:`EvalCase`."""

    type: AssertionType
    # exact / semantic
    expected: str | None = None
    min_score: float | None = None  # semantic / llm_judge / groundedness
    # llm_judge
    rubric: str | None = None
    # tool_usage
    must_call: list[str] = field(default_factory=list)
    never_call: list[str] = field(default_factory=list)
    max_steps: int | None = None
    # security
    attack_class: str | None = None  # injection|tool_abuse|exfiltration|jailbreak
    must_block: bool = True


@dataclass(frozen=True)
class EvalCase:
    """One input plus the assertions its trajectory must satisfy."""

    id: str
    input: str
    assertions: list[Assertion]
    setup: dict[str, Any] = field(default_factory=dict)  # tool_provider/vector_store/seed/flags
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TrajectoryStep:
    """A single observed step in a run (a message or a tool call)."""

    node: str
    role: str
    content: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_ok: bool | None = None


@dataclass(frozen=True)
class Trajectory:
    """The captured execution of one case against the graph."""

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
    """The outcome of evaluating one :class:`Assertion` against a trajectory."""

    assertion: Assertion
    passed: bool
    score: float | None
    detail: str = ""


@dataclass(frozen=True)
class CaseResult:
    """The aggregate outcome of one :class:`EvalCase`."""

    case_id: str
    passed: bool
    trajectory: Trajectory
    assertion_results: list[AssertionResult]


@dataclass(frozen=True)
class Scorecard:
    """Suite-level metrics across every :class:`CaseResult`."""

    suite: str
    version: str
    pass_rate: float
    avg_steps: float
    tool_error_rate: float
    avg_cost_usd: float
    avg_latency_ms: float
    cases: list[CaseResult]


@dataclass(frozen=True)
class EvalSet:
    """A named suite of cases, loadable from YAML."""

    suite: str
    cases: list[EvalCase]

    @classmethod
    def from_yaml(cls, path: str) -> EvalSet:
        """Load and validate an eval-set from a YAML file.

        Args:
            path: Filesystem path to the eval-set YAML.

        Returns:
            The parsed :class:`EvalSet`.

        Raises:
            EvalSetError: The file is missing, unparseable, or structurally
                invalid (missing ``suite``, a case without ``id``, an unknown
                assertion ``type``, …). The loader never lets a raw ``OSError``
                or ``yaml`` error escape.
        """
        import yaml

        from prismal.core.exceptions import EvalSetError

        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise EvalSetError(f"cannot read eval-set '{path}': {exc}") from exc
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise EvalSetError(f"invalid YAML in eval-set '{path}': {exc}") from exc

        return cls._from_mapping(data, source=path)

    @classmethod
    def _from_mapping(cls, data: Any, *, source: str) -> EvalSet:
        """Build an :class:`EvalSet` from an already-parsed mapping."""
        from prismal.core.exceptions import EvalSetError

        if not isinstance(data, dict):
            raise EvalSetError(f"eval-set '{source}' must be a mapping")
        suite = data.get("suite")
        if not isinstance(suite, str) or not suite:
            raise EvalSetError(f"eval-set '{source}' is missing a 'suite' name")

        raw_cases = data.get("cases", [])
        if not isinstance(raw_cases, list):
            raise EvalSetError(f"eval-set '{source}' field 'cases' must be a list")

        cases = [_parse_case(rc, source=source) for rc in raw_cases]
        return cls(suite=suite, cases=cases)


def _parse_case(raw: Any, *, source: str) -> EvalCase:
    """Parse one raw case mapping into an :class:`EvalCase`."""
    from prismal.core.exceptions import EvalSetError

    if not isinstance(raw, dict):
        raise EvalSetError(f"eval-set '{source}' has a non-mapping case")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise EvalSetError(f"eval-set '{source}' has a case without a string 'id'")
    case_input = raw.get("input", "")
    if not isinstance(case_input, str):
        raise EvalSetError(f"case '{case_id}' field 'input' must be a string")

    raw_assertions = raw.get("assertions", [])
    if not isinstance(raw_assertions, list):
        raise EvalSetError(f"case '{case_id}' field 'assertions' must be a list")
    assertions = [_parse_assertion(ra, case_id=case_id) for ra in raw_assertions]

    setup = raw.get("setup", {}) or {}
    if not isinstance(setup, dict):
        raise EvalSetError(f"case '{case_id}' field 'setup' must be a mapping")
    tags = raw.get("tags", []) or []
    if not isinstance(tags, list):
        raise EvalSetError(f"case '{case_id}' field 'tags' must be a list")

    return EvalCase(
        id=case_id,
        input=case_input,
        assertions=assertions,
        setup=dict(setup),
        tags=list(tags),
    )


def _parse_assertion(raw: Any, *, case_id: str) -> Assertion:
    """Parse one raw assertion mapping into an :class:`Assertion`."""
    from prismal.core.exceptions import EvalSetError

    if not isinstance(raw, dict):
        raise EvalSetError(f"case '{case_id}' has a non-mapping assertion")
    raw_type = raw.get("type")
    if not isinstance(raw_type, str):
        raise EvalSetError(f"case '{case_id}' has a non-string assertion type {raw_type!r}")
    try:
        atype = AssertionType(raw_type)
    except ValueError as exc:
        raise EvalSetError(f"case '{case_id}' has an unknown assertion type {raw_type!r}") from exc

    return Assertion(
        type=atype,
        expected=raw.get("expected"),
        min_score=raw.get("min_score"),
        rubric=raw.get("rubric"),
        must_call=list(raw.get("must_call", []) or []),
        never_call=list(raw.get("never_call", []) or []),
        max_steps=raw.get("max_steps"),
        attack_class=raw.get("attack_class"),
        must_block=bool(raw.get("must_block", True)),
    )


__all__ = [
    "Assertion",
    "AssertionResult",
    "AssertionType",
    "CaseResult",
    "EvalCase",
    "EvalSet",
    "Scorecard",
    "Trajectory",
    "TrajectoryStep",
]
