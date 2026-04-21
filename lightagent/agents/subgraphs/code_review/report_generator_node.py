"""Report-generator node for code_review subgraph.

Terminal node. Aggregates all accumulated issues into a
:class:`CodeReviewReport`, computes a severity-weighted score, and writes
the report to ``state["metadata"]["code_review"]["report"]``. Also appends
an ``AIMessage`` so the summary is visible to the caller.

Scoring model::

    score = 1.0 - Σ weight[severity]       (clamped to [0, 1])

Default weights (subtract from 1.0)::

    critical → 0.40   # one critical issue alone fails the 0.8 gate
    high     → 0.20
    medium   → 0.10
    low      → 0.05
    info     → 0.01

Approval: ``score >= approval_threshold`` (default ``0.8``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage

from lightagent.agents.subgraphs.code_review.types import CodeReviewReport
from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lightagent.agents.subgraphs.code_review.types import CodeIssue

logger = get_logger("lightagent.subgraphs.code_review.report")

_SEVERITY_WEIGHT: dict[str, float] = {
    "critical": 0.40,
    "high": 0.20,
    "medium": 0.10,
    "low": 0.05,
    "info": 0.01,
}


def _score_issues(issues: list[CodeIssue]) -> float:
    penalty = sum(_SEVERITY_WEIGHT.get(i.severity, 0.0) for i in issues)
    return max(0.0, min(1.0, 1.0 - penalty))


def _summarise(
    issues: list[CodeIssue],
    suggestions: list[str],
    score: float,
    approved: bool,
) -> str:
    if not issues:
        return "No issues found — the code is approved."
    counts = dict.fromkeys(_SEVERITY_WEIGHT, 0)
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    breakdown = ", ".join(f"{n} {sev}" for sev, n in counts.items() if n > 0)
    status = "APPROVED" if approved else "REJECTED"
    return (
        f"Code review {status} (score={score:.2f}, {len(issues)} total: "
        f"{breakdown}; {len(suggestions)} suggestion(s))."
    )


def make_report_generator_node(
    approval_threshold: float = 0.8,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that assembles the final report."""
    if not 0.0 <= approval_threshold <= 1.0:
        raise ValueError(f"approval_threshold must be in [0, 1]; got {approval_threshold}")

    async def report_generator_node(state: dict[str, Any]) -> dict[str, Any]:
        existing = dict(state.get("metadata") or {}).get("code_review") or {}
        issues: list[CodeIssue] = list(existing.get("issues") or [])
        suggestions: list[str] = list(existing.get("suggestions") or [])
        score = _score_issues(issues)
        approved = score >= approval_threshold
        summary = _summarise(issues, suggestions, score, approved)

        report = CodeReviewReport(
            issues=issues,
            summary=summary,
            score=score,
            approved=approved,
        )
        logger.info(
            "code_review_report_done",
            score=score,
            approved=approved,
            n_issues=len(issues),
        )
        updated = {**existing, "report": report}
        return {
            "messages": [AIMessage(content=summary)],
            "metadata": {
                **(state.get("metadata") or {}),
                "code_review": updated,
            },
        }

    return report_generator_node


__all__ = ["make_report_generator_node"]
