"""Code review subgraph (C4 / SPEC-SUBGRAPH-002).

Pipeline::

    linter → security_scanner → logic_reviewer → suggester → report_generator

Public entry points::

    from lightagent.agents.subgraphs.code_review import (
        build_code_review_subgraph,
        CodeIssue,
        CodeReviewReport,
        CodeReviewError,
    )
"""

from __future__ import annotations

from lightagent.agents.subgraphs.code_review.builder import (
    build_code_review_subgraph,
)
from lightagent.agents.subgraphs.code_review.linter_node import make_linter_node
from lightagent.agents.subgraphs.code_review.logic_reviewer_node import (
    make_logic_reviewer_node,
)
from lightagent.agents.subgraphs.code_review.report_generator_node import (
    make_report_generator_node,
)
from lightagent.agents.subgraphs.code_review.security_scanner_node import (
    make_security_scanner_node,
)
from lightagent.agents.subgraphs.code_review.suggester_node import (
    make_suggester_node,
)
from lightagent.agents.subgraphs.code_review.types import (
    Category,
    CodeIssue,
    CodeReviewReport,
    Severity,
)
from lightagent.core.exceptions import CodeReviewError

__all__ = [
    "Category",
    "CodeIssue",
    "CodeReviewError",
    "CodeReviewReport",
    "Severity",
    "build_code_review_subgraph",
    "make_linter_node",
    "make_logic_reviewer_node",
    "make_report_generator_node",
    "make_security_scanner_node",
    "make_suggester_node",
]
