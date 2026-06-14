"""Runtime tool-policy enforcement (Phase H — H4-03).

Covers the per-run enforcer (call counting), the ActionInterceptor seam, and the
react_loop pre-dispatch integration.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from prismal.agents.tool_registry import react_loop
from prismal.core.config import Settings
from prismal.core.exceptions import ToolPolicyDenied
from prismal.security.action_interceptor import ActionInterceptor
from prismal.security.permissions import PermissionManager
from prismal.security.tool_policy import (
    PolicyEffect,
    RunToolPolicy,
    ToolPolicy,
    ToolPolicyEngine,
)


def _engine(policies: list[ToolPolicy], default: str = "allow") -> ToolPolicyEngine:
    return ToolPolicyEngine(policies, settings=Settings(hardening_tool_policy_default=default))


# ── RunToolPolicy (per-run call counting) ────────────────────────────────────


def test_run_policy_counts_allowed_calls_for_rate_limit() -> None:
    eng = _engine(
        [ToolPolicy(agent="coder", tool="write_file", effect=PolicyEffect.ALLOW, rate_limit_per_run=2)]
    )
    run = RunToolPolicy(eng)
    assert run.check(agent="coder", tool="write_file", args={}).effect is PolicyEffect.ALLOW
    assert run.check(agent="coder", tool="write_file", args={}).effect is PolicyEffect.ALLOW
    # 3rd call exceeds the limit of 2.
    assert run.check(agent="coder", tool="write_file", args={}).effect is PolicyEffect.DENY


# ── ActionInterceptor.check_tool_policy (the named seam) ──────────────────────


def test_action_interceptor_raises_on_deny() -> None:
    eng = _engine([ToolPolicy(agent="*", tool="http_request", effect=PolicyEffect.DENY)])
    interceptor = ActionInterceptor(permission_manager=PermissionManager())
    with pytest.raises(ToolPolicyDenied):
        interceptor.check_tool_policy(
            agent="coder", tool="http_request", args={}, call_count=0, policy_engine=eng
        )


def test_action_interceptor_returns_decision_on_allow() -> None:
    eng = _engine([], default="allow")
    interceptor = ActionInterceptor(permission_manager=PermissionManager())
    decision = interceptor.check_tool_policy(
        agent="coder", tool="read_file", args={}, call_count=0, policy_engine=eng
    )
    assert decision.effect is PolicyEffect.ALLOW


# ── react_loop pre-dispatch integration ──────────────────────────────────────


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.invoked = False

    async def ainvoke(self, _args: object) -> str:
        self.invoked = True
        return "tool output"


class _ToolThenFinalLLM:
    def __init__(self, tool_name: str) -> None:
        self.calls = 0
        self.model = "fake/model"
        self.second_call_messages: list[object] = []
        self._tool_name = tool_name

    async def ainvoke(self, messages: object) -> AIMessage:
        self.calls += 1
        if self.calls == 1:
            msg = AIMessage(content="")
            msg.tool_calls = [{"name": self._tool_name, "args": {}, "id": "tc1"}]  # type: ignore[attr-defined]
            return msg
        self.second_call_messages = list(messages)  # type: ignore[arg-type]
        return AIMessage(content="done")


def _tool_message(messages: list[object]) -> ToolMessage:
    return next(m for m in messages if isinstance(m, ToolMessage))


async def test_react_loop_denies_tool_and_skips_dispatch() -> None:
    eng = _engine([ToolPolicy(agent="*", tool="http_request", effect=PolicyEffect.DENY)])
    run = RunToolPolicy(eng)
    tool = _Tool("http_request")
    llm = _ToolThenFinalLLM("http_request")

    await react_loop(
        llm, [tool], [HumanMessage(content="go")], agent_name="coder", tool_policy=run
    )

    assert tool.invoked is False  # dispatch was skipped
    content = str(_tool_message(llm.second_call_messages).content).lower()
    assert "denied" in content or "not permitted" in content


async def test_react_loop_require_hitl_skips_until_approved() -> None:
    eng = _engine([ToolPolicy(agent="*", tool="delete_file", effect=PolicyEffect.REQUIRE_HITL)])
    run = RunToolPolicy(eng)
    tool = _Tool("delete_file")
    llm = _ToolThenFinalLLM("delete_file")

    await react_loop(
        llm, [tool], [HumanMessage(content="rm")], agent_name="coder", tool_policy=run
    )

    assert tool.invoked is False
    content = str(_tool_message(llm.second_call_messages).content).lower()
    assert "approval" in content or "human" in content


async def test_react_loop_allows_tool_when_policy_permits() -> None:
    eng = _engine([], default="allow")
    run = RunToolPolicy(eng)
    tool = _Tool("read_file")
    llm = _ToolThenFinalLLM("read_file")

    await react_loop(
        llm, [tool], [HumanMessage(content="read")], agent_name="coder", tool_policy=run
    )

    assert tool.invoked is True
    assert str(_tool_message(llm.second_call_messages).content) == "tool output"


async def test_react_loop_no_policy_is_unchanged() -> None:
    tool = _Tool("read_file")
    llm = _ToolThenFinalLLM("read_file")
    await react_loop(llm, [tool], [HumanMessage(content="read")])
    assert tool.invoked is True
