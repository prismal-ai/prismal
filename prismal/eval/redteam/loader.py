"""Red-team corpus loader (SPEC-EVL-RED-001).

Loads the adversarial corpus as an :class:`EvalSet` and validates that every case
carries at least one ``security`` assertion — a corpus whose cases assert nothing
about containment is malformed.
"""

from __future__ import annotations

from pathlib import Path

from prismal.core.exceptions import EvalSetError
from prismal.eval.types import AssertionType, EvalSet

# repo_root/tests/eval/redteam/corpus.yaml — loader.py is prismal/eval/redteam/loader.py
_DEFAULT_CORPUS = Path(__file__).resolve().parents[3] / "tests/eval/redteam/corpus.yaml"


def load_redteam_corpus(path: str | None = None) -> EvalSet:
    """Load and validate the adversarial corpus.

    Args:
        path: Corpus YAML path. Defaults to the shipped
            ``tests/eval/redteam/corpus.yaml``.

    Returns:
        The parsed :class:`EvalSet`.

    Raises:
        EvalSetError: The corpus is missing/malformed, or a case lacks a
            ``security`` assertion.
    """
    resolved = path if path is not None else str(_DEFAULT_CORPUS)
    eval_set = EvalSet.from_yaml(resolved)
    for case in eval_set.cases:
        if not any(a.type is AssertionType.SECURITY for a in case.assertions):
            raise EvalSetError(
                f"red-team case {case.id!r} has no 'security' assertion; "
                "every adversarial case must assert containment"
            )
    return eval_set


__all__ = ["load_redteam_corpus"]
