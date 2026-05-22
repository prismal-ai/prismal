"""Builder for the code_review subgraph (C4 / SPEC-SUBGRAPH-002).

Linear pipeline::

    linter → security_scanner → logic_reviewer → suggester → report_generator

Each analyzer appends its findings to a shared ``issues`` list in the
state's ``code_review`` metadata. The suggester then drafts remediations
per issue, and ``report_generator`` produces the final
:class:`CodeReviewReport` with a severity-weighted score and an approval
flag based on ``approval_threshold``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.agents.subgraphs.code_review.linter_node import make_linter_node
from prismal.agents.subgraphs.code_review.logic_reviewer_node import (
    make_logic_reviewer_node,
)
from prismal.agents.subgraphs.code_review.report_generator_node import (
    make_report_generator_node,
)
from prismal.agents.subgraphs.code_review.security_scanner_node import (
    make_security_scanner_node,
)
from prismal.agents.subgraphs.code_review.suggester_node import (
    make_suggester_node,
)
from prismal.agents.subgraphs.registry import SubgraphDefinition
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.subgraphs.code_review.types import CodeIssue
    from prismal.core.config import Settings

logger = get_logger("lightagent.subgraphs.code_review.builder")

_NAME = "code_review"
_DESCRIPTION = (
    "Code review pipeline: "
    "linter → security_scanner → logic_reviewer → suggester → report_generator"
)


def build_code_review_subgraph(
    linter_fn: (Callable[[str, str], Awaitable[list[CodeIssue]]] | None) = None,
    scanner_fn: (Callable[[str, str], Awaitable[list[CodeIssue]]] | None) = None,
    reviewer_fn: (Callable[[str, str], Awaitable[list[CodeIssue]]] | None) = None,
    suggester_fn: (Callable[[str, list[CodeIssue]], Awaitable[list[str]]] | None) = None,
    approval_threshold: float = 0.8,
    settings: Settings | None = None,
) -> SubgraphDefinition:
    """Build the code_review :class:`SubgraphDefinition`."""
    del settings  # reserved

    linter = make_linter_node(linter_fn=linter_fn)
    security = make_security_scanner_node(scanner_fn=scanner_fn)
    logic = make_logic_reviewer_node(reviewer_fn=reviewer_fn)
    suggester = make_suggester_node(suggester_fn=suggester_fn)
    report = make_report_generator_node(approval_threshold=approval_threshold)

    definition = SubgraphDefinition(
        name=_NAME,
        description=_DESCRIPTION,
        entry_point="linter",
        nodes={
            "linter": linter,
            "security_scanner": security,
            "logic_reviewer": logic,
            "suggester": suggester,
            "report_generator": report,
        },
        edges=[
            ("linter", "security_scanner"),
            ("security_scanner", "logic_reviewer"),
            ("logic_reviewer", "suggester"),
            ("suggester", "report_generator"),
        ],
    )
    logger.info(
        "code_review_subgraph_built",
        nodes=list(definition.nodes.keys()),
        approval_threshold=approval_threshold,
    )
    return definition


async def register_code_review(
    approval_threshold: float = 0.8,
    settings: Settings | None = None,
) -> None:
    """Register the code_review subgraph (idempotent)."""
    from prismal.agents.subgraphs.registry import SubgraphRegistry

    registry = SubgraphRegistry.get_instance()
    if registry.get(_NAME) is not None:
        logger.info("code_review.already_registered")
        return
    definition = build_code_review_subgraph(
        approval_threshold=approval_threshold, settings=settings
    )
    await registry.register(_NAME, definition)
    logger.info("code_review.registered")


__all__ = ["build_code_review_subgraph", "register_code_review"]
