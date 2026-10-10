"""AG-UI 1.0 wire models (SPEC-PRT-AGU-001, pins: ADR-PRT-001 §2.3)."""

from __future__ import annotations

import json

import pytest

from prismal.events import agui
from prismal.events.agui import (
    AGUI_PROTOCOL_VERSION,
    AgUiEvent,
    AgUiEventType,
    CustomEvent,
    RunFinished,
    RunStarted,
    StateDelta,
    SubagentError,
    SubagentStarted,
    TextMessageContent,
    TokenUsage,
    ToolCallResult,
    ToolCallStart,
    to_sse,
)

pytestmark = pytest.mark.unit

_PINNED_31 = {
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TEXT_MESSAGE_CHUNK",
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_CHUNK",
    "TOOL_CALL_RESULT",
    "STATE_SNAPSHOT",
    "STATE_DELTA",
    "MESSAGES_SNAPSHOT",
    "ACTIVITY_SNAPSHOT",
    "ACTIVITY_DELTA",
    "RAW",
    "CUSTOM",
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "STEP_STARTED",
    "STEP_FINISHED",
    "REASONING_START",
    "REASONING_MESSAGE_START",
    "REASONING_MESSAGE_CONTENT",
    "REASONING_MESSAGE_END",
    "REASONING_MESSAGE_CHUNK",
    "REASONING_END",
    "REASONING_ENCRYPTED_VALUE",
    "SUBAGENT_STARTED",
    "SUBAGENT_FINISHED",
    "SUBAGENT_ERROR",
}


def test_protocol_version_is_two_component_1_0() -> None:
    assert AGUI_PROTOCOL_VERSION == "1.0"


def test_event_type_enum_has_exactly_the_31_pinned_names() -> None:
    names = {m.name for m in AgUiEventType}
    assert names == _PINNED_31
    assert len(list(AgUiEventType)) == 31
    assert all(m.value == m.name for m in AgUiEventType)


@pytest.mark.parametrize("forbidden", ["USAGE", "INTERRUPT"])
def test_no_usage_or_interrupt_event_types(forbidden: str) -> None:
    assert forbidden not in {m.name for m in AgUiEventType}


def test_dump_is_camel_case_and_excludes_none() -> None:
    ev = ToolCallStart(tool_call_id="c1", tool_call_name="search", timestamp=5)
    dumped = ev.to_wire()
    assert dumped == {
        "type": "TOOL_CALL_START",
        "timestamp": 5,
        "toolCallId": "c1",
        "toolCallName": "search",
    }


def test_populate_by_alias_and_by_name() -> None:
    a = TextMessageContent.model_validate({"messageId": "m", "delta": "hi"})
    b = TextMessageContent(message_id="m", delta="hi")
    assert a.message_id == b.message_id == "m"
    assert a.type == AgUiEventType.TEXT_MESSAGE_CONTENT


def test_extra_fields_are_allowed_and_round_trip() -> None:
    ev = CustomEvent.model_validate({"name": "x", "value": 1, "futureField": True})
    assert ev.to_wire()["futureField"] is True


def test_run_started_carries_protocol_version_by_default() -> None:
    ev = RunStarted(thread_id="t", run_id="r")
    assert ev.to_wire()["protocolVersion"] == "1.0"


def test_run_finished_native_usage_is_token_usage_list() -> None:
    usage = TokenUsage(
        provider="openrouter",
        model="anthropic/claude",
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        reasoning_tokens=1,
        cached_input_tokens=2,
        cache_write_input_tokens=0,
    )
    ev = RunFinished(thread_id="t", run_id="r", outcome={"type": "success"}, usage=[usage])
    wire = ev.to_wire()
    assert wire["usage"][0] == {
        "provider": "openrouter",
        "model": "anthropic/claude",
        "inputTokens": 10,
        "outputTokens": 5,
        "totalTokens": 15,
        "reasoningTokens": 1,
        "cachedInputTokens": 2,
        "cacheWriteInputTokens": 0,
    }


def test_state_delta_and_subagent_shapes() -> None:
    d = StateDelta(delta=[{"op": "add", "path": "/a", "value": 1}])
    assert d.to_wire()["delta"] == [{"op": "add", "path": "/a", "value": 1}]
    s = SubagentStarted(subagent_run_id="w1", name="worker", parent_tool_call_id="c")
    assert s.to_wire()["subagentRunId"] == "w1"
    assert s.to_wire()["parentToolCallId"] == "c"
    e = SubagentError(subagent_run_id="w1", message="boom")
    assert e.type == AgUiEventType.SUBAGENT_ERROR


def test_tool_call_result_role_defaults_to_tool() -> None:
    ev = ToolCallResult(message_id="m", tool_call_id="c", content="ok")
    assert ev.to_wire()["role"] == "tool"


def test_to_sse_frames_camel_json() -> None:
    frame = to_sse(TextMessageContent(message_id="m", delta="hé", timestamp=1))
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    body = json.loads(frame[len("data: ") : -2])
    assert body == {"type": "TEXT_MESSAGE_CONTENT", "timestamp": 1, "messageId": "m", "delta": "hé"}


def test_every_event_subclass_has_a_pinned_type() -> None:
    for cls in agui.EVENT_CLASSES.values():
        assert issubclass(cls, AgUiEvent)
    assert set(agui.EVENT_CLASSES) <= set(AgUiEventType)
