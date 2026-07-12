"""Unit tests for blind_review_pipeline synthesis value objects (Phase BRP1/BRP4)."""

from __future__ import annotations

from prismal.agents.subgraphs.code_review.types import CodeIssue, CodeReviewReport


def _issue(description: str, *, file: str = "f.py", line: int | None = 1) -> CodeIssue:
    return CodeIssue(
        severity="medium",  # type: ignore[arg-type]
        category="logic",  # type: ignore[arg-type]
        description=description,
        file=file,
        line=line,
        suggestion="fix",
    )


def test_synthesize_min_score() -> None:
    """report.score is the conservative min of the two verdicts (SPEC-BRP-SYN-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.synthesis import synthesize_verdicts

    a = CodeReviewReport(summary="a", score=0.9, approved=True)
    b = CodeReviewReport(summary="b", score=0.7, approved=False)

    result = synthesize_verdicts(a, b, approval_threshold=0.8)

    assert result.report.score == 0.7
    assert result.report.approved is False  # 0.7 < 0.8
    assert result.reviewer_a_score == 0.9
    assert result.reviewer_b_score == 0.7


def test_synthesize_union_issues_deduped() -> None:
    """report.issues is the de-duplicated union of both verdicts (SPEC-BRP-SYN-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.synthesis import synthesize_verdicts

    a = CodeReviewReport(issues=[_issue("dup"), _issue("only_a")], score=0.9, approved=True)
    b = CodeReviewReport(issues=[_issue("dup"), _issue("only_b")], score=0.85, approved=True)

    result = synthesize_verdicts(a, b, approval_threshold=0.8)

    descriptions = sorted(i.description for i in result.report.issues)
    assert descriptions == ["dup", "only_a", "only_b"]


def test_synthesize_agreement_flag() -> None:
    """agreement reflects whether both verdicts' approved flags match (SPEC-BRP-SYN-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.synthesis import synthesize_verdicts

    both_approve = synthesize_verdicts(
        CodeReviewReport(score=0.9, approved=True),
        CodeReviewReport(score=0.85, approved=True),
        approval_threshold=0.8,
    )
    disagree = synthesize_verdicts(
        CodeReviewReport(score=0.9, approved=True),
        CodeReviewReport(score=0.5, approved=False),
        approval_threshold=0.8,
    )

    assert both_approve.agreement is True
    assert disagree.agreement is False


def test_synthesis_result_roundtrip() -> None:
    """SynthesisResult is a frozen value object that round-trips via equality (SPEC-BRP-TYP-001)."""
    from prismal.agents.subgraphs.blind_review_pipeline.synthesis import SynthesisResult
    from prismal.agents.subgraphs.code_review.types import CodeReviewReport

    report = CodeReviewReport(summary="merged", score=0.9, approved=True)
    result = SynthesisResult(
        report=report,
        agreement=True,
        reviewer_a_score=0.95,
        reviewer_b_score=0.9,
    )

    assert result.report is report
    assert result.agreement is True
    assert result.reviewer_a_score == 0.95
    assert result.reviewer_b_score == 0.9

    # Frozen: two SynthesisResult with equal fields compare equal.
    same = SynthesisResult(
        report=report,
        agreement=True,
        reviewer_a_score=0.95,
        reviewer_b_score=0.9,
    )
    assert result == same
