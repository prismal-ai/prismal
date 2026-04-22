"""Unit tests for code_review subgraph (C4).

Pipeline::

    linter -> security_scanner -> logic_reviewer -> suggester -> report_generator

- linter / security_scanner / logic_reviewer: each produces a list of
  :class:`CodeIssue` appended to ``state["metadata"]["code_review"]["issues"]``.
- suggester: generates remediation suggestions.
- report_generator: computes severity-weighted score and the approval flag.

All analyzers take injected callables, so tests don't require real ruff /
bandit / LLM backends.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from lightagent.agents.subgraphs.code_review import (
    CodeIssue,
    CodeReviewError,
    CodeReviewReport,
    make_linter_node,
    make_logic_reviewer_node,
    make_report_generator_node,
    make_security_scanner_node,
    make_suggester_node,
)
from lightagent.agents.subgraphs.code_review.builder import (
    build_code_review_subgraph,
)
from lightagent.core.exceptions import LightAgentError

# ── Helpers ───────────────────────────────────────────────────────────────────


def _state(code: str = "def foo():\n    pass\n", **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "messages": [HumanMessage(content="review code")],
        "metadata": {"code_review": {"code": code, "file": "demo.py"}},
    }
    base.update(overrides)
    return base


def _issue(
    severity: str = "medium",
    category: str = "logic",
    description: str = "issue",
    file: str = "demo.py",
    line: int | None = 1,
    suggestion: str = "fix it",
) -> CodeIssue:
    return CodeIssue(
        severity=severity,  # type: ignore[arg-type]
        category=category,  # type: ignore[arg-type]
        description=description,
        file=file,
        line=line,
        suggestion=suggestion,
    )


# ── Dataclass / exception ────────────────────────────────────────────────────


def test_code_issue_is_instantiable() -> None:
    i = _issue(severity="critical", category="security")
    assert i.severity == "critical"
    assert i.category == "security"


def test_code_review_report_is_instantiable() -> None:
    r = CodeReviewReport(issues=[], summary="clean", score=1.0, approved=True)
    assert r.approved is True


def test_code_review_error_inherits_from_lightagent_error() -> None:
    assert issubclass(CodeReviewError, LightAgentError)


# ── linter_node ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_linter_node_appends_issues_from_injected_fn() -> None:
    async def fake_linter(code: str, file: str) -> list[CodeIssue]:
        return [_issue(category="style", description="long line", severity="low")]

    node = make_linter_node(linter_fn=fake_linter)
    update = await node(_state())
    issues = update["metadata"]["code_review"]["issues"]
    assert len(issues) == 1
    assert issues[0].category == "style"


@pytest.mark.asyncio
async def test_linter_node_preserves_existing_issues() -> None:
    async def fake_linter(code: str, file: str) -> list[CodeIssue]:
        return [_issue(description="new")]

    node = make_linter_node(linter_fn=fake_linter)
    state = _state()
    state["metadata"]["code_review"]["issues"] = [_issue(description="pre-existing")]
    update = await node(state)
    issues = update["metadata"]["code_review"]["issues"]
    assert len(issues) == 2
    descriptions = {i.description for i in issues}
    assert descriptions == {"pre-existing", "new"}


@pytest.mark.asyncio
async def test_linter_node_noop_without_code() -> None:
    node = make_linter_node(linter_fn=MagicMock())
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


# ── security_scanner_node ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_security_scanner_appends_security_issues() -> None:
    async def fake_scanner(code: str, file: str) -> list[CodeIssue]:
        return [
            _issue(category="security", severity="critical", description="dangerous call"),
            _issue(category="security", severity="high", description="hardcoded key"),
        ]

    node = make_security_scanner_node(scanner_fn=fake_scanner)
    update = await node(_state("code"))
    issues = update["metadata"]["code_review"]["issues"]
    assert {i.severity for i in issues} == {"critical", "high"}
    assert all(i.category == "security" for i in issues)


# ── logic_reviewer_node ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logic_reviewer_appends_logic_issues() -> None:
    async def fake_reviewer(code: str, file: str) -> list[CodeIssue]:
        return [_issue(category="logic", description="off-by-one")]

    node = make_logic_reviewer_node(reviewer_fn=fake_reviewer)
    update = await node(_state())
    issues = update["metadata"]["code_review"]["issues"]
    assert issues[0].category == "logic"


@pytest.mark.asyncio
async def test_logic_reviewer_default_is_noop() -> None:
    """Without a reviewer_fn, no issues are added."""
    node = make_logic_reviewer_node()
    update = await node(_state())
    assert update["metadata"]["code_review"]["issues"] == []


@pytest.mark.asyncio
async def test_logic_reviewer_noop_without_code() -> None:
    node = make_logic_reviewer_node()
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


@pytest.mark.asyncio
async def test_logic_reviewer_swallows_callable_errors() -> None:
    async def failing(code: str, file: str) -> list[CodeIssue]:
        raise RuntimeError("boom")

    node = make_logic_reviewer_node(reviewer_fn=failing)
    update = await node(_state())
    # Error is logged; issues remain empty instead of crashing the graph.
    assert update["metadata"]["code_review"]["issues"] == []


@pytest.mark.asyncio
async def test_security_scanner_default_is_noop() -> None:
    node = make_security_scanner_node()
    update = await node(_state())
    assert update["metadata"]["code_review"]["issues"] == []


@pytest.mark.asyncio
async def test_security_scanner_noop_without_code() -> None:
    node = make_security_scanner_node()
    update = await node({"messages": [], "metadata": {}})
    assert update == {}


@pytest.mark.asyncio
async def test_security_scanner_swallows_callable_errors() -> None:
    async def failing(code: str, file: str) -> list[CodeIssue]:
        raise RuntimeError("boom")

    node = make_security_scanner_node(scanner_fn=failing)
    update = await node(_state())
    assert update["metadata"]["code_review"]["issues"] == []


# ── suggester_node ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suggester_produces_one_suggestion_per_issue() -> None:
    async def fake_suggester(code: str, issues: list[CodeIssue]) -> list[str]:
        return [f"fix-{i.description}" for i in issues]

    node = make_suggester_node(suggester_fn=fake_suggester)
    state = _state()
    state["metadata"]["code_review"]["issues"] = [
        _issue(description="X"),
        _issue(description="Y"),
    ]
    update = await node(state)
    suggestions = update["metadata"]["code_review"]["suggestions"]
    assert suggestions == ["fix-X", "fix-Y"]


@pytest.mark.asyncio
async def test_suggester_noop_when_no_issues() -> None:
    async def fake_suggester(code: str, issues: list[CodeIssue]) -> list[str]:
        return ["unused"]

    node = make_suggester_node(suggester_fn=fake_suggester)
    state = _state()
    state["metadata"]["code_review"]["issues"] = []
    update = await node(state)
    suggestions = update["metadata"]["code_review"]["suggestions"]
    assert suggestions == []


# ── report_generator_node ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_report_score_is_one_for_no_issues() -> None:
    node = make_report_generator_node(approval_threshold=0.8)
    state = _state()
    state["metadata"]["code_review"]["issues"] = []
    update = await node(state)
    report: CodeReviewReport = update["metadata"]["code_review"]["report"]
    assert report.score == 1.0
    assert report.approved is True


@pytest.mark.asyncio
async def test_report_score_penalises_critical_issues_most() -> None:
    """One critical issue drops the score far more than one 'info' issue."""
    node = make_report_generator_node(approval_threshold=0.8)
    s_crit = _state()
    s_crit["metadata"]["code_review"]["issues"] = [_issue(severity="critical")]
    s_info = _state()
    s_info["metadata"]["code_review"]["issues"] = [_issue(severity="info")]

    u_crit = await node(s_crit)
    u_info = await node(s_info)
    crit_score = u_crit["metadata"]["code_review"]["report"].score
    info_score = u_info["metadata"]["code_review"]["report"].score
    assert crit_score < info_score
    assert info_score > 0.8  # still approved
    assert crit_score < 0.8  # rejected


@pytest.mark.asyncio
async def test_report_respects_approval_threshold() -> None:
    """Score exactly at threshold is approved; below is not."""
    node_loose = make_report_generator_node(approval_threshold=0.5)
    node_strict = make_report_generator_node(approval_threshold=0.95)
    state = _state()
    state["metadata"]["code_review"]["issues"] = [_issue(severity="medium")]
    loose_report = (await node_loose(state))["metadata"]["code_review"]["report"]
    strict_report = (await node_strict(state))["metadata"]["code_review"]["report"]
    assert loose_report.approved is True
    assert strict_report.approved is False


@pytest.mark.asyncio
async def test_report_score_clamped_to_zero() -> None:
    """Many critical issues can't push the score below 0.0."""
    node = make_report_generator_node()
    state = _state()
    state["metadata"]["code_review"]["issues"] = [_issue(severity="critical") for _ in range(20)]
    update = await node(state)
    assert update["metadata"]["code_review"]["report"].score == 0.0


@pytest.mark.asyncio
async def test_report_generator_appends_ai_message_with_summary() -> None:
    node = make_report_generator_node()
    state = _state()
    state["metadata"]["code_review"]["issues"] = [_issue(severity="high", description="watch out")]
    update = await node(state)
    assert "messages" in update
    assert isinstance(update["messages"][0], AIMessage)
    body = update["messages"][0].content
    assert "1 issue" in body or "1 total" in body or "high" in body.lower()


# ── builder ──────────────────────────────────────────────────────────────────


def test_build_code_review_subgraph_has_five_nodes() -> None:
    defn = build_code_review_subgraph()
    assert set(defn.nodes.keys()) == {
        "linter",
        "security_scanner",
        "logic_reviewer",
        "suggester",
        "report_generator",
    }


def test_build_code_review_subgraph_entry_point_is_linter() -> None:
    defn = build_code_review_subgraph()
    assert defn.entry_point == "linter"


def test_build_code_review_subgraph_linear_edges() -> None:
    defn = build_code_review_subgraph()
    edges = set(defn.edges)
    expected = {
        ("linter", "security_scanner"),
        ("security_scanner", "logic_reviewer"),
        ("logic_reviewer", "suggester"),
        ("suggester", "report_generator"),
    }
    assert expected.issubset(edges)
