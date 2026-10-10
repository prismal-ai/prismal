"""Forbidden uses — behaviour (Phase DEC — SPEC-DEC-SEC-002, DEC6-03).

Import-graph rules live in ``tests/unit/decisions/test_no_sdk_imports.py``;
here: the cascade never mutates taint provenance, never touches the runaway
guard, refuses forbidden questions at registration and a policy deny wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismal.core.exceptions import ForbiddenDecisionError
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.cascade import DecisionCascade
from prismal.decisions.state import DecisionStateBuilder
from prismal.decisions.types import Decision, DecisionKind, GatePolicy, QuestionSpec
from prismal.decisions.validation import FORBIDDEN_DECISION_USES
from prismal.security.taint import Provenance, TaintRegistry

Q = QuestionSpec(id="rag.chunk_relevance", kind=DecisionKind.NOUL, instructions="Relevant?")


async def test_taint_registry_is_never_mutated(tmp_path: Path) -> None:
    reg = TaintRegistry()
    reg.mark_untrusted("tool output text", Provenance.TOOL)
    before = reg.to_dict()
    yes = Decision.from_probabilities(Q, {"true": 1.0}, backend="laya", model="lm")
    cascade = DecisionCascade(
        [FakeDecisionAdapter({Q.id: yes}, name="laya", model="lm")], taint=reg
    )
    st = (
        DecisionStateBuilder(taint=reg)
        .add_untrusted("chunk", "tool output text", source="tool")
        .build()
    )
    await cascade.decide(st, {Q.id: Q}, policy=GatePolicy(), integration="rag")
    assert reg.to_dict() == before
    assert reg.is_untrusted("tool output text")


@pytest.mark.parametrize(
    "instructions",
    [
        "Has the agent used more than 10 tool calls?",  # RunawayGuard budget
        "Is the run under budget? Sum the token counts.",  # BudgetGuard
        "Is the cron due before 2026-10-06?",  # scheduler / DateTimeService
    ],
)
async def test_counting_budget_and_date_questions_refused(instructions: str) -> None:
    q = QuestionSpec(id="x", kind=DecisionKind.NOUL, instructions=instructions)
    st = DecisionStateBuilder().add_trusted("k", "v").build()
    with pytest.raises(ForbiddenDecisionError):
        await DecisionCascade([]).decide(st, {q.id: q}, policy=GatePolicy(), integration="swarm")


@pytest.mark.parametrize("use", FORBIDDEN_DECISION_USES)
async def test_forbidden_integration_refused(use: str) -> None:
    st = DecisionStateBuilder().add_trusted("k", "v").build()
    with pytest.raises(ForbiddenDecisionError):
        await DecisionCascade([]).decide(st, {Q.id: Q}, policy=GatePolicy(), integration=use)


def test_policy_deny_wins_over_an_accepted_proposal() -> None:
    """A Decision is advisory input only: the tool policy engine has no decision input."""
    import inspect

    from prismal.security.action_interceptor import ActionInterceptor
    from prismal.security.tool_policy import ToolPolicyEngine

    for cls in (ToolPolicyEngine, ActionInterceptor):
        source = inspect.getsource(cls)
        assert "Decision" not in source.replace("PolicyDecision", "")
    assert "decisions" not in inspect.getsource(ToolPolicyEngine)


def test_runaway_guard_has_no_decision_input() -> None:
    import inspect

    from prismal.security import runaway

    assert "prismal.decisions" not in inspect.getsource(runaway)
    assert Any is not None
