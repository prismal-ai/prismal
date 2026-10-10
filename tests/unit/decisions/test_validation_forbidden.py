"""Forbidden decision uses (Phase DEC — SPEC-DEC-SEC-002, DEC1-07)."""

from __future__ import annotations

import pytest

from prismal.core.exceptions import ForbiddenDecisionError
from prismal.decisions.types import DecisionKind, OptionSpec, QuestionSpec
from prismal.decisions.validation import FORBIDDEN_DECISION_USES, validate_question


def _noul(instructions: str, criteria: str = "") -> QuestionSpec:
    return QuestionSpec(
        id="q", kind=DecisionKind.NOUL, instructions=instructions, criteria=criteria
    )


def test_forbidden_list_matches_spec() -> None:
    assert FORBIDDEN_DECISION_USES == (
        "clear_injection_flag",
        "extend_runaway_budget",
        "count_or_arithmetic",
        "date_comparison",
        "authorize_critical_action",
        "decide_on_tainted_input_alone",
    )


@pytest.mark.parametrize(
    "text",
    [
        "How many items are in the list?",
        "Is the total more than ten?",
        "Is the value greater than the limit?",
        "Is x less than y?",
        "Does the sum exceed the budget?",
        "Compute the average and decide.",
        "Was it filed before 2026-01-01?",
        "Did it happen after March 3?",
        "Is 5 > 3?",
        "¿Cuántos elementos hay?",
        "¿Es el monto mayor que el límite?",
        "¿Es menor que cien?",
        "¿Se presentó antes de 2025?",
        "¿Ocurrió después del 3 de marzo?",
        "Calcula el promedio.",
    ],
)
def test_counting_arithmetic_and_dates_rejected(text: str) -> None:
    with pytest.raises(ForbiddenDecisionError):
        validate_question(_noul(text), integration="rag")


def test_lint_also_covers_criteria() -> None:
    with pytest.raises(ForbiddenDecisionError):
        validate_question(
            _noul("Is it ok?", criteria="ok means fewer than 3 errors"), integration="x"
        )


@pytest.mark.parametrize(
    "text",
    [
        "Is the chunk relevant to the user's question?",
        "Pick the best agent for the request after reading it carefully.",
        "¿El fragmento es relevante para la pregunta?",
        "Answer before you overthink: is the tone polite?",
    ],
)
def test_legitimate_questions_pass(text: str) -> None:
    validate_question(_noul(text), integration="rag")


@pytest.mark.parametrize("integration", FORBIDDEN_DECISION_USES)
def test_forbidden_integration_names_rejected(integration: str) -> None:
    with pytest.raises(ForbiddenDecisionError):
        validate_question(_noul("Is it relevant?"), integration=integration)


def test_option_descriptions_are_linted() -> None:
    q = QuestionSpec(
        id="q",
        kind=DecisionKind.CHOICE,
        instructions="Pick one.",
        options=(OptionSpec("a", "if there are more than 3 rows"), OptionSpec("b", "otherwise")),
    )
    with pytest.raises(ForbiddenDecisionError):
        validate_question(q, integration="route")
