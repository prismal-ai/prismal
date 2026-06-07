"""Unit tests for ``prismal.agents.kokoro.judge`` (SPEC-KOK-AGT-003).

Covers RF-KOK-07/08 and the K5 "done when" criteria: the verdict cites all
three lenses + dissent; with execution off ``tool_executor`` is never called;
a denied action sets ``blocked_reason`` without raising.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from prismal.agents.kokoro.deliberation import DeliberationResult
from prismal.agents.kokoro.judge import KokoroAction, KokoroJudgeAgent, Verdict
from prismal.agents.patterns.debate import DebatePosition
from prismal.core.config import Settings
from prismal.core.exceptions import JudgeError, PermissionDeniedError
from prismal.security.prompt_builder import SecurePromptBuilder

# ── fixtures / fakes ─────────────────────────────────────────────────────────


def _deliberation(*, converged: bool = True, agreement: float = 0.8) -> DeliberationResult:
    finals = [
        DebatePosition(agent_id="spirit", role="values", content="Protect integrity.", round=2),
        DebatePosition(agent_id="mind", role="logic", content="Evidence supports it.", round=2),
        DebatePosition(agent_id="heart", role="empathy", content="People benefit.", round=2),
    ]
    return DeliberationResult(
        positions=finals,
        final_positions=finals,
        agreement_score=agreement,
        rounds_completed=2,
        converged=converged,
    )


_VERDICT_JSON = json.dumps(
    {
        "decision": "Ship it with safeguards",
        "rationale": "Spirit demands integrity; Mind shows feasibility; Heart confirms benefit.",
        "lens_summaries": {
            "spirit": "weighed integrity",
            "mind": "weighed evidence",
            "heart": "weighed impact",
        },
        "dissent_retained": [],
    }
)


class FakeJudge:
    """Deterministic judge_fn capturing the messages it receives."""

    def __init__(self, reply: str = _VERDICT_JSON) -> None:
        self.reply = reply
        self.received: list[list[dict[str, str]]] = []

    async def __call__(self, messages: list[dict[str, str]]) -> str:
        self.received.append(messages)
        return self.reply


class FakeExecutor:
    """tool_executor spy."""

    def __init__(self, result: str = "tool-ok") -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool_name: str, args: dict[str, Any]) -> str:
        self.calls.append((tool_name, args))
        return self.result


class AllowInterceptor:
    """ActionInterceptor stand-in that allows everything."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], str]] = []

    async def on_tool_start(self, serialized: dict[str, Any], input_str: str) -> None:
        self.calls.append((serialized, input_str))


class DenyInterceptor:
    """ActionInterceptor stand-in that denies everything."""

    async def on_tool_start(self, serialized: dict[str, Any], input_str: str) -> None:
        raise PermissionDeniedError(str(serialized.get("name")), "file_write")


class StubAudit:
    """AuditLogger stand-in recording log_event calls."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def log_event(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_type, payload))


def _judge_agent(
    *,
    reply: str = _VERDICT_JSON,
    execute_actions: bool = False,
    interceptor: object | None = None,
    executor: FakeExecutor | None = None,
    audit: StubAudit | None = None,
    prompt_builder: SecurePromptBuilder | None = None,
) -> tuple[KokoroJudgeAgent, FakeJudge]:
    fake_judge = FakeJudge(reply)
    agent = KokoroJudgeAgent(
        judge_fn=fake_judge,
        tool_executor=executor,
        interceptor=interceptor,  # type: ignore[arg-type]
        audit=audit if audit is not None else StubAudit(),  # type: ignore[arg-type]
        prompt_builder=prompt_builder,
        settings=Settings(kokoro_execute_actions=execute_actions),
    )
    return agent, fake_judge


# ── judge() — verdict parsing (RF-KOK-07) ────────────────────────────────────


async def test_judge_parses_json_verdict() -> None:
    agent, _ = _judge_agent()
    verdict = await agent.judge("Should we ship?", _deliberation())

    assert isinstance(verdict, Verdict)
    assert verdict.decision == "Ship it with safeguards"
    assert "Spirit" in verdict.rationale
    assert verdict.agreement_score == 0.8
    assert verdict.dissent_retained == []
    assert verdict.action is None


async def test_lens_summaries_one_entry_per_soul() -> None:
    agent, _ = _judge_agent()
    verdict = await agent.judge("q", _deliberation())
    assert set(verdict.lens_summaries) == {"spirit", "mind", "heart"}


async def test_missing_lens_filled_from_final_position() -> None:
    partial = json.dumps(
        {
            "decision": "d",
            "rationale": "r",
            "lens_summaries": {"spirit": "weighed integrity"},
            "dissent_retained": [],
        }
    )
    agent, _ = _judge_agent(reply=partial)
    verdict = await agent.judge("q", _deliberation())

    assert set(verdict.lens_summaries) == {"spirit", "mind", "heart"}
    assert verdict.lens_summaries["mind"] == "Evidence supports it."
    assert verdict.lens_summaries["heart"] == "People benefit."


async def test_non_json_reply_degrades_to_raw_decision() -> None:
    agent, _ = _judge_agent(reply="Just ship it already.")
    verdict = await agent.judge("q", _deliberation())

    assert verdict.decision == "Just ship it already."
    assert set(verdict.lens_summaries) == {"spirit", "mind", "heart"}


async def test_json_in_markdown_fences_is_parsed() -> None:
    agent, _ = _judge_agent(reply=f"```json\n{_VERDICT_JSON}\n```")
    verdict = await agent.judge("q", _deliberation())
    assert verdict.decision == "Ship it with safeguards"


async def test_dissent_defaults_to_diverging_positions_when_not_converged() -> None:
    agent, _ = _judge_agent(reply="Compromise decision.")
    verdict = await agent.judge("q", _deliberation(converged=False, agreement=0.1))

    assert len(verdict.dissent_retained) == 3
    assert "Protect integrity." in verdict.dissent_retained


async def test_dissent_empty_when_converged_and_judge_omits_it() -> None:
    agent, _ = _judge_agent(reply="Aligned decision.")
    verdict = await agent.judge("q", _deliberation(converged=True))
    assert verdict.dissent_retained == []


async def test_judge_backend_failure_raises_judge_error() -> None:
    async def boom(messages: list[dict[str, str]]) -> str:
        raise RuntimeError("llm down")

    agent = KokoroJudgeAgent(
        judge_fn=boom,
        audit=StubAudit(),  # type: ignore[arg-type]
        settings=Settings(),
    )
    with pytest.raises(JudgeError, match="llm down"):
        await agent.judge("q", _deliberation())


# ── judge() — secure prompt ──────────────────────────────────────────────────


class SpyPromptBuilder(SecurePromptBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, str]] = []

    def build(
        self,
        system: str,
        user: str,
        docs: list[str] | None = None,
    ) -> list[dict[str, str]]:
        self.calls.append({"system": system, "user": user})
        return super().build(system, user, docs)


async def test_positions_route_through_secure_prompt_builder() -> None:
    spy = SpyPromptBuilder()
    agent, fake = _judge_agent(prompt_builder=spy)
    await agent.judge("Should we ship?", _deliberation())

    assert len(spy.calls) == 1
    assert "Protect integrity." in spy.calls[0]["user"]
    assert "Should we ship?" in spy.calls[0]["user"]
    assert "Protect integrity." not in spy.calls[0]["system"]
    assert fake.received[0][1]["content"].startswith("<user_input>")


# ── action parsing / act() (RF-KOK-08) ───────────────────────────────────────


_VERDICT_WITH_ACTION = json.dumps(
    {
        "decision": "d",
        "rationale": "r",
        "lens_summaries": {},
        "dissent_retained": [],
        "action": {"tool_name": "file_write", "args": {"path": "/tmp/x", "content": "hi"}},
    }
)


async def test_action_ignored_when_execution_disabled() -> None:
    agent, _ = _judge_agent(reply=_VERDICT_WITH_ACTION, execute_actions=False)
    verdict = await agent.judge("q", _deliberation())
    assert verdict.action is None


async def test_action_parsed_in_action_mode() -> None:
    agent, _ = _judge_agent(reply=_VERDICT_WITH_ACTION, execute_actions=True)
    verdict = await agent.judge("q", _deliberation())
    assert verdict.action == KokoroAction(
        tool_name="file_write", args={"path": "/tmp/x", "content": "hi"}
    )
    assert verdict.action.executed is False


async def test_act_never_calls_executor_when_disabled() -> None:
    executor = FakeExecutor()
    agent, _ = _judge_agent(execute_actions=False, executor=executor)
    verdict = Verdict(
        decision="d",
        rationale="r",
        lens_summaries={},
        dissent_retained=[],
        agreement_score=1.0,
        action=KokoroAction(tool_name="file_write", args={}),
    )
    result = await agent.act(verdict)
    assert result is verdict
    assert executor.calls == []


async def test_act_passthrough_when_no_action() -> None:
    executor = FakeExecutor()
    agent, _ = _judge_agent(execute_actions=True, executor=executor)
    verdict = await agent.judge("q", _deliberation())  # _VERDICT_JSON has no action
    result = await agent.act(verdict)
    assert result is verdict
    assert executor.calls == []


async def test_act_executes_allowed_action() -> None:
    executor = FakeExecutor(result="written")
    interceptor = AllowInterceptor()
    agent, _ = _judge_agent(
        reply=_VERDICT_WITH_ACTION,
        execute_actions=True,
        interceptor=interceptor,
        executor=executor,
    )
    verdict = await agent.judge("q", _deliberation())
    result = await agent.act(verdict)

    assert executor.calls == [("file_write", {"path": "/tmp/x", "content": "hi"})]
    assert interceptor.calls[0][0] == {"name": "file_write"}
    assert result.action is not None
    assert result.action.executed is True
    assert result.action.result == "written"
    assert result.action.blocked_reason is None


async def test_act_denied_sets_blocked_reason_without_raising() -> None:
    executor = FakeExecutor()
    agent, _ = _judge_agent(
        reply=_VERDICT_WITH_ACTION,
        execute_actions=True,
        interceptor=DenyInterceptor(),
        executor=executor,
    )
    verdict = await agent.judge("q", _deliberation())
    result = await agent.act(verdict)

    assert executor.calls == []  # executor never reached
    assert result.action is not None
    assert result.action.executed is False
    assert result.action.blocked_reason is not None
    assert "file_write" in result.action.blocked_reason


async def test_act_without_executor_in_action_mode_raises() -> None:
    agent, _ = _judge_agent(
        reply=_VERDICT_WITH_ACTION,
        execute_actions=True,
        interceptor=AllowInterceptor(),
        executor=None,
    )
    verdict = await agent.judge("q", _deliberation())
    with pytest.raises(JudgeError, match="tool_executor"):
        await agent.act(verdict)


async def test_act_executor_failure_raises_judge_error() -> None:
    class BoomExecutor:
        async def __call__(self, tool_name: str, args: dict[str, Any]) -> str:
            raise RuntimeError("tool exploded")

    agent, _ = _judge_agent(
        reply=_VERDICT_WITH_ACTION,
        execute_actions=True,
        interceptor=AllowInterceptor(),
    )
    agent._tool_executor = BoomExecutor()  # noqa: SLF001
    verdict = await agent.judge("q", _deliberation())
    with pytest.raises(JudgeError, match="tool exploded"):
        await agent.act(verdict)


# ── audit (hash-first, K5-04) ────────────────────────────────────────────────


async def test_judge_audits_verdict_hash_first() -> None:
    audit = StubAudit()
    agent, _ = _judge_agent(audit=audit)
    verdict = await agent.judge("q", _deliberation())

    events = [e for e in audit.events if e[0] == "kokoro_verdict"]
    assert len(events) == 1
    payload = events[0][1]
    assert payload["decision_hash"] != verdict.decision
    assert len(str(payload["decision_hash"])) == 64  # sha256 hex
    assert "Ship it with safeguards" not in json.dumps(payload)
    assert payload["lens_count"] == 3


async def test_act_audits_action_without_raw_content() -> None:
    audit = StubAudit()
    agent, _ = _judge_agent(
        reply=_VERDICT_WITH_ACTION,
        execute_actions=True,
        interceptor=AllowInterceptor(),
        executor=FakeExecutor(result="secret-result"),
        audit=audit,
    )
    verdict = await agent.judge("q", _deliberation())
    await agent.act(verdict)

    events = [e for e in audit.events if e[0] == "kokoro_action"]
    assert len(events) == 1
    payload = events[0][1]
    assert payload["tool_name"] == "file_write"
    assert payload["executed"] is True
    assert payload["blocked"] is False
    serialized = json.dumps(payload)
    assert "secret-result" not in serialized
    assert "/tmp/x" not in serialized


async def test_act_audits_blocked_action() -> None:
    audit = StubAudit()
    agent, _ = _judge_agent(
        reply=_VERDICT_WITH_ACTION,
        execute_actions=True,
        interceptor=DenyInterceptor(),
        executor=FakeExecutor(),
        audit=audit,
    )
    verdict = await agent.judge("q", _deliberation())
    await agent.act(verdict)

    events = [e for e in audit.events if e[0] == "kokoro_action"]
    assert len(events) == 1
    assert events[0][1]["blocked"] is True
    assert events[0][1]["executed"] is False
