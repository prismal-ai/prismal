"""Shared dataclasses for the code_review subgraph (SPEC-SUBGRAPH-002)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal["security", "logic", "style", "performance", "test"]


@dataclass
class CodeIssue:
    """One issue detected by any analyzer in the code_review pipeline.

    Attributes:
        severity: Severity level (``critical`` / ``high`` / ``medium`` /
            ``low`` / ``info``).
        category: Kind of issue (``security`` / ``logic`` / ``style`` /
            ``performance`` / ``test``).
        description: Human-readable explanation of the issue.
        file: Path (or synthetic name) of the file the issue lives in.
        line: 1-indexed line number, or ``None`` when unknown.
        suggestion: Remediation hint.
    """

    severity: Severity
    category: Category
    description: str
    file: str
    line: int | None
    suggestion: str


@dataclass
class CodeReviewReport:
    """Final report produced by ``report_generator_node``.

    Attributes:
        issues: All issues surfaced across all analyzers.
        summary: Human-readable digest (≈ what the user sees).
        score: ``1.0`` = clean, ``0.0`` = many critical issues. Clamped.
        approved: ``True`` when ``score >= approval_threshold``.
    """

    issues: list[CodeIssue] = field(default_factory=list)
    summary: str = ""
    score: float = 1.0
    approved: bool = True


__all__ = ["Category", "CodeIssue", "CodeReviewReport", "Severity"]
