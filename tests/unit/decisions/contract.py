"""Shared ``DecisionPort`` contract cases (Phase DEC — SPEC-DEC-PRT-001).

Every adapter registers a :class:`ContractCase` in ``CASES`` (see
``test_port_contract.py``); the suite asserts the port shape, one decision per
question id, never-raise on error / timeout / over-limit input, and ``supports``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from prismal.decisions.ports import DecisionPort
from prismal.decisions.types import DecisionKind, OptionSpec, QuestionSpec, RiskTier

CHOICE = QuestionSpec(
    id="route.supervisor",
    kind=DecisionKind.CHOICE,
    instructions="Pick the best agent for the request.",
    options=(OptionSpec("coder", "Writes code"), OptionSpec("researcher", "Searches the web")),
    risk_tier=RiskTier.LOW,
)
NOUL = QuestionSpec(
    id="rag.chunk_relevance",
    kind=DecisionKind.NOUL,
    instructions="Is the chunk relevant to the query?",
)
SCORE = QuestionSpec(
    id="eval.judge",
    kind=DecisionKind.SCORE,
    instructions="Rate faithfulness.",
    levels=("unsupported", "partial", "supported"),
)
QUESTIONS = {q.id: q for q in (CHOICE, NOUL, SCORE)}
STATE = {"user": "«untrusted:user»\nwrite a sort function\n«/untrusted»"}


def wide_choice(n: int) -> QuestionSpec:
    return QuestionSpec(
        id="wide",
        kind=DecisionKind.CHOICE,
        instructions="Pick one.",
        options=tuple(OptionSpec(f"o{i}", f"Option {i}") for i in range(n)),
    )


@dataclass(frozen=True)
class ContractCase:
    """Factories for one adapter: healthy, failing and slow (> timeout) instances."""

    name: str
    ok: Callable[[], DecisionPort]
    failing: Callable[[], DecisionPort]
    slow: Callable[[], DecisionPort]
    max_choice_options: int
