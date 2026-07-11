"""Value objects, deterministic merge, and node for the Blind Review Pipeline (Phase BRP).

``SynthesisResult`` (SPEC-BRP-TYP-001) captures the outcome of merging two
independent reviewer verdicts; ``synthesize_verdicts`` (SPEC-BRP-SYN-001) is the
deterministic, LLM-free merge; ``make_synthesis_node`` wraps it as a LangGraph
node that consolidates both reviewer verdicts into
``state["metadata"]["blind_review"]["synthesis"]``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from prismal.agents.subgraphs.code_review.types import CodeReviewReport
from prismal.monitoring.otel import OTelManager

if TYPE_CHECKING:
    from prismal.agents.subgraphs.code_review.types import CodeIssue
    from prismal.core.config import Settings

logger = structlog.get_logger("prismal.subgraphs.blind_review_pipeline.synthesis")
otel = OTelManager()

SynthesizeFn = Callable[[CodeReviewReport, CodeReviewReport], "SynthesisResult"]

__all__ = ["SynthesisResult", "SynthesizeFn", "make_synthesis_node", "synthesize_verdicts"]


@dataclass(frozen=True)
class SynthesisResult:
    """Deterministic merge of two independent reviewer verdicts.

    Attributes:
        report: The merged :class:`CodeReviewReport` (de-duplicated union of
            issues, conservative ``min`` score, ``approved`` re-derived against
            the approval threshold).
        agreement: ``True`` when both verdicts' ``approved`` flags match.
        reviewer_a_score: Blind reviewer A's raw score, retained for
            observability.
        reviewer_b_score: Blind reviewer B's raw score, retained for
            observability.
    """

    report: CodeReviewReport
    agreement: bool
    reviewer_a_score: float
    reviewer_b_score: float


def synthesize_verdicts(
    verdict_a: CodeReviewReport,
    verdict_b: CodeReviewReport,
    *,
    approval_threshold: float,
) -> SynthesisResult:
    """Deterministically merge two reviewer verdicts (SPEC-BRP-SYN-001, no LLM).

    - ``report.issues`` = de-duplicated union of both issue lists (dedupe key:
      ``(file, line, category, description)``).
    - ``report.score`` = ``min(a.score, b.score)`` (conservative).
    - ``report.approved`` = ``score >= approval_threshold``.
    - ``report.summary`` = a deterministic digest (issue counts by severity).
    - ``agreement`` = whether both verdicts' ``approved`` flags match.
    """
    seen: set[tuple[str, int | None, str, str]] = set()
    issues: list[CodeIssue] = []
    for issue in [*verdict_a.issues, *verdict_b.issues]:
        key = (issue.file, issue.line, issue.category, issue.description)
        if key not in seen:
            seen.add(key)
            issues.append(issue)

    score = min(verdict_a.score, verdict_b.score)
    approved = score >= approval_threshold

    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    digest = ", ".join(f"{sev}={counts[sev]}" for sev in sorted(counts)) or "no issues"
    summary = f"blind review: score={score:.2f}, {digest}"

    report = CodeReviewReport(issues=issues, summary=summary, score=score, approved=approved)
    return SynthesisResult(
        report=report,
        agreement=verdict_a.approved == verdict_b.approved,
        reviewer_a_score=verdict_a.score,
        reviewer_b_score=verdict_b.score,
    )


def _report_to_dict(report: CodeReviewReport) -> dict[str, Any]:
    """Serialize a report to a nested dict (dot-notation-navigable by ``score_gate``).

    ``issues`` are kept as ``CodeIssue`` instances so the implementer's retry
    path (``_extract_prior_issues``) receives structured issues, not prose.
    """
    return {
        "issues": report.issues,
        "summary": report.summary,
        "score": report.score,
        "approved": report.approved,
    }


def make_synthesis_node(
    synthesize_fn: SynthesizeFn | None = None,
    *,
    settings: Settings | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async node merging both reviewer verdicts into ``synthesis``.

    Reads ``reviewer_a_verdict`` / ``reviewer_b_verdict`` from
    ``state["metadata"]["blind_review"]`` and writes a nested-dict
    ``synthesis`` (so ``score_gate`` can read ``synthesis.report.score``).
    """

    async def synthesis_node(state: dict[str, Any]) -> dict[str, Any]:
        with otel.start_span("blind_review.synthesis") as span:
            span.set_attribute("prismal.subgraph", "blind_review_pipeline")
            span.set_attribute("prismal.agent", "synthesis")

            resolved_settings = settings
            if resolved_settings is None:
                from prismal.core.config import get_settings

                resolved_settings = get_settings()
            threshold = resolved_settings.blind_review_approval_threshold
            fn = synthesize_fn or (
                lambda a, b: synthesize_verdicts(a, b, approval_threshold=threshold)
            )

            br = dict(state.get("metadata", {}).get("blind_review", {}))
            verdict_a = br.get("reviewer_a_verdict") or CodeReviewReport(score=0.0, approved=False)
            verdict_b = br.get("reviewer_b_verdict") or CodeReviewReport(score=0.0, approved=False)

            result = fn(verdict_a, verdict_b)

            br["synthesis"] = {
                "report": _report_to_dict(result.report),
                "agreement": result.agreement,
                "reviewer_a_score": result.reviewer_a_score,
                "reviewer_b_score": result.reviewer_b_score,
            }
            logger.info(
                "blind_review.synthesis_written",
                score=result.report.score,
                approved=result.report.approved,
                agreement=result.agreement,
                issues=len(result.report.issues),
            )

            return {
                "current_agent": "synthesis",
                "metadata": {**state.get("metadata", {}), "blind_review": br},
            }

    return synthesis_node
