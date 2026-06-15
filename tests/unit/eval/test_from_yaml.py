"""Tests for EvalSet YAML loading (Phase V — SPEC-EVL-TYP-001 / RF-EVL-001).

V1 "done when": eval-sets parse with composable assertions; a malformed set
raises ``EvalSetError``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismal.core.exceptions import EvalError, EvalSetError
from prismal.eval.types import AssertionType, EvalSet

_VALID = """\
suite: rag_groundedness
cases:
  - id: rag-001
    input: "What does the budget hard cap do?"
    setup: { tool_provider: fake, vector_store: fake, seed: 7 }
    tags: [rag]
    assertions:
      - type: tool_usage
        must_call: ["rag_agent"]
        max_steps: 6
      - type: groundedness
        min_score: 0.8
      - type: llm_judge
        rubric: "Answer cites the budget layer"
        min_score: 0.7
"""


def _write(tmp_path: Path, text: str) -> str:
    p = tmp_path / "set.yaml"
    p.write_text(text)
    return str(p)


def test_from_yaml_loads_suite_and_cases(tmp_path: Path) -> None:
    """A well-formed eval-set parses into typed cases and assertions."""
    es = EvalSet.from_yaml(_write(tmp_path, _VALID))

    assert isinstance(es, EvalSet)
    assert es.suite == "rag_groundedness"
    assert len(es.cases) == 1

    case = es.cases[0]
    assert case.id == "rag-001"
    assert case.input == "What does the budget hard cap do?"
    assert case.setup == {"tool_provider": "fake", "vector_store": "fake", "seed": 7}
    assert case.tags == ["rag"]
    assert len(case.assertions) == 3


def test_from_yaml_parses_assertion_fields(tmp_path: Path) -> None:
    """Each assertion's typed fields survive the round-trip."""
    es = EvalSet.from_yaml(_write(tmp_path, _VALID))
    tool, ground, judge = es.cases[0].assertions

    assert tool.type is AssertionType.TOOL_USAGE
    assert tool.must_call == ["rag_agent"]
    assert tool.max_steps == 6

    assert ground.type is AssertionType.GROUNDEDNESS
    assert ground.min_score == 0.8

    assert judge.type is AssertionType.LLM_JUDGE
    assert judge.rubric == "Answer cites the budget layer"
    assert judge.min_score == 0.7


def test_from_yaml_missing_suite_raises(tmp_path: Path) -> None:
    """An eval-set without a ``suite`` key is malformed."""
    with pytest.raises(EvalSetError):
        EvalSet.from_yaml(_write(tmp_path, "cases: []\n"))


def test_from_yaml_unknown_assertion_type_raises(tmp_path: Path) -> None:
    """An unknown assertion ``type`` is rejected at load time."""
    bad = "suite: s\ncases:\n  - id: c1\n    input: x\n    assertions:\n      - type: telepathy\n"
    with pytest.raises(EvalSetError):
        EvalSet.from_yaml(_write(tmp_path, bad))


def test_from_yaml_case_missing_id_raises(tmp_path: Path) -> None:
    """A case without an ``id`` is malformed."""
    bad = "suite: s\ncases:\n  - input: x\n    assertions: []\n"
    with pytest.raises(EvalSetError):
        EvalSet.from_yaml(_write(tmp_path, bad))


def test_from_yaml_nonexistent_path_raises(tmp_path: Path) -> None:
    """A missing file path raises ``EvalSetError``, not a bare OSError."""
    with pytest.raises(EvalSetError):
        EvalSet.from_yaml(str(tmp_path / "nope.yaml"))


def test_eval_set_error_is_eval_error() -> None:
    """``EvalSetError`` is part of the ``EvalError`` hierarchy."""
    assert issubclass(EvalSetError, EvalError)
