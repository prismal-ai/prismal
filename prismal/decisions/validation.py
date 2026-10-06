"""Forbidden decision uses (Phase DEC — SPEC-DEC-SEC-002, DD-DEC-008).

Typed decision models are literal: they cannot count, compute or compare dates,
and injection can move their verdict. Questions that ask for those things are
rejected at **registration** time (never per request).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from prismal.core.exceptions import ForbiddenDecisionError

if TYPE_CHECKING:
    from prismal.decisions.types import QuestionSpec

FORBIDDEN_DECISION_USES: Final[tuple[str, ...]] = (
    "clear_injection_flag",
    "extend_runaway_budget",
    "count_or_arithmetic",
    "date_comparison",
    "authorize_critical_action",
    "decide_on_tainted_input_alone",
)

_MONTHS = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december"
    r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre"
    r"|noviembre|diciembre)"
)
_DATE = rf"(?:\d{{1,4}}(?:[-/.]\d{{1,2}}){{0,2}}|{_MONTHS}|(?:el\s+)?\d{{1,2}}\s+de\s+{_MONTHS})"

_LINT: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (use, re.compile(pattern, re.IGNORECASE))
    for use, pattern in (
        ("count_or_arithmetic", r"\bhow\s+many\b"),
        ("count_or_arithmetic", r"\bcount\s+(?:the|how|all)\b"),
        ("count_or_arithmetic", r"\b(?:more|fewer|less|greater|larger|smaller)\s+than\b"),
        ("count_or_arithmetic", r"\b(?:sum|total|average|mean)\b"),
        ("count_or_arithmetic", r"\b(?:compute|calculate|add\s+up)\b"),
        ("count_or_arithmetic", r"\d\s*(?:<=|>=|<|>|==)\s*\d"),
        ("count_or_arithmetic", r"\bcu[aá]nt[oa]s\b"),
        ("count_or_arithmetic", r"\b(?:mayor|menor)(?:es)?\s+que\b"),
        ("count_or_arithmetic", r"\bm[aá]s\s+de\s+\d"),
        ("count_or_arithmetic", r"\b(?:suma|promedio|calcula|calcular)\b"),
        ("date_comparison", rf"\b(?:before|after|prior\s+to)\s+{_DATE}\b"),
        ("date_comparison", rf"\b(?:antes|despu[eé]s)\s+del?\s+{_DATE}"),
    )
)


def lint_text(text: str) -> str | None:
    """Return the forbidden use ``text`` asks for, or ``None``."""
    for use, pattern in _LINT:
        if pattern.search(text):
            return use
    return None


def validate_question(q: QuestionSpec, *, integration: str) -> None:
    """Raise ``ForbiddenDecisionError`` for a forbidden integration or question text."""
    if integration in FORBIDDEN_DECISION_USES:
        raise ForbiddenDecisionError(f"integration {integration!r} is a forbidden decision use")
    texts = [q.instructions, q.criteria, *q.levels, *(o.description for o in q.options)]
    for text in texts:
        use = lint_text(text)
        if use is not None:
            raise ForbiddenDecisionError(
                f"question {q.id!r} ({integration}) asks for a forbidden use: {use}"
            )
