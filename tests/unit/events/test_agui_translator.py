"""Pure astream → AG-UI translator (SPEC-PRT-AGU-002, SPEC-PRT-USG-002, ADR-PRT-001 §2.3)."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.types import Interrupt

from prismal.budget.types import Usage
from prismal.events import agui
from prismal.events.agui import (
    AgUiContext,
    AgUiEvent,
    error_run,
    finish_run,
    json_patch_diff,
    start_run,
    state_view,
    to_agui_events,
)
from prismal.monitoring.observability_types import RunSummary
from tests.unit.events import _transcripts

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "protocols" / "agui"


def _ctx(**kw: Any) -> AgUiContext:
    counter = itertools.count(1)
    return AgUiContext(run_id="r1", thread_id="t1", clock=lambda: next(counter), **kw)


def _types(events: list[AgUiEvent]) -> list[str]:
    return [str(e.type) for e in events]


def _summary(**usage: Any) -> RunSummary:
    return RunSummary(
        run_id="r1",
        session_id="t1",
        agent_name="supervisor",
        visited_nodes=[],
        spans=[],
        tool_calls=[],
        usage=Usage(**usage),
        latency_ms=1.0,
    )


def _text(content: str, mid: str = "m1", **kw: Any) -> tuple[str, Any]:
    return (
        "messages",
        (AIMessageChunk(content=content, id=mid, **kw), {"langgraph_node": "agent"}),
    )


# --------------------------------------------------------------------------- start_run


def test_start_run_emits_run_started_then_filtered_snapshot() -> None:
    ctx = _ctx()
    state = {
        "messages": [HumanMessage(content="secret prompt")],
        "current_agent": "supervisor",
        "metadata": {"security": {"canary": "x"}, "hardening": {}, "_hitl": 1, "intent": "code"},
        "risk_score": 3.0,
    }
    events = start_run(ctx, initial_state=state)
    assert _types(events) == ["RUN_STARTED", "STATE_SNAPSHOT"]
    started = events[0].to_wire()
    assert started["protocolVersion"] == "1.0"
    assert started["threadId"] == "t1" and started["runId"] == "r1"
    assert started["timestamp"] == 1
    snap = events[1].to_wire()["snapshot"]
    assert snap == {"current_agent": "supervisor", "metadata": {"intent": "code"}}


def test_start_run_without_state_deltas_has_no_snapshot() -> None:
    events = start_run(_ctx(include_state_deltas=False), initial_state={"current_agent": "x"})
    assert _types(events) == ["RUN_STARTED"]


def test_state_view_drops_live_objects() -> None:
    view = state_view(
        {"task_plan": ["a", object()], "metadata": {"intent": object(), "budget": {"n": 1}}}
    )
    assert view == {"task_plan": ["a"]}


# --------------------------------------------------------------------------- text


def test_text_chunks_open_once_then_stream_content() -> None:
    ctx = _ctx()
    first = to_agui_events(_text("Hel"), ctx)
    second = to_agui_events(_text("lo"), ctx)
    assert _types(first) == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert _types(second) == ["TEXT_MESSAGE_CONTENT"]
    assert second[0].to_wire()["delta"] == "lo"
    closing = finish_run(ctx)
    assert _types(closing) == ["TEXT_MESSAGE_END", "RUN_FINISHED"]


def test_last_chunk_position_closes_message() -> None:
    ctx = _ctx()
    to_agui_events(_text("a"), ctx)
    events = to_agui_events(_text("", chunk_position="last"), ctx)
    assert _types(events) == ["TEXT_MESSAGE_END"]
    assert _types(finish_run(ctx)) == ["RUN_FINISHED"]


def test_content_blocks_extract_text_only() -> None:
    ctx = _ctx()
    chunk = AIMessageChunk(content=[{"type": "text", "text": "hi"}, {"type": "image"}], id="m")
    events = to_agui_events(("messages", (chunk, {})), ctx)
    assert events[1].to_wire()["delta"] == "hi"


def test_new_message_id_closes_previous_message() -> None:
    ctx = _ctx()
    to_agui_events(_text("a", "m1"), ctx)
    events = to_agui_events(_text("b", "m2"), ctx)
    assert _types(events) == ["TEXT_MESSAGE_END", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]


def test_complete_ai_message_is_self_balanced() -> None:
    ctx = _ctx()
    msg = AIMessage(
        content="done", id="m9", tool_calls=[{"name": "f", "args": {"x": 1}, "id": "c9"}]
    )
    events = to_agui_events(("messages", (msg, {})), ctx)
    assert _types(events) == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
    ]
    assert json.loads(events[4].to_wire()["delta"]) == {"x": 1}


def test_human_message_is_not_echoed() -> None:
    assert to_agui_events(("messages", (HumanMessage(content="hi"), {})), _ctx()) == []


def test_message_without_id_gets_deterministic_id() -> None:
    ctx = _ctx()
    events = to_agui_events(("messages", (AIMessageChunk(content="x"), {})), ctx)
    assert events[0].to_wire()["messageId"] == "r1-msg-1"


# --------------------------------------------------------------------------- tools


def _tool_chunk(cid: str | None, name: str | None, args: str, index: int = 0) -> tuple[str, Any]:
    chunk = AIMessageChunk(
        content="",
        id="m1",
        tool_call_chunks=[{"name": name, "args": args, "id": cid, "index": index}],
    )
    return ("messages", (chunk, {"langgraph_node": "agent"}))


def test_tool_call_chunks_start_args_end_result() -> None:
    ctx = _ctx()
    a = to_agui_events(_tool_chunk("c1", "search", '{"q"'), ctx)
    b = to_agui_events(_tool_chunk(None, None, ': "x"}'), ctx)
    result = ToolMessage(content="found", tool_call_id="c1", id="tm1")
    c = to_agui_events(("messages", (result, {})), ctx)
    assert _types(a) == ["TOOL_CALL_START", "TOOL_CALL_ARGS"]
    assert a[0].to_wire()["parentMessageId"] == "m1"
    assert a[0].to_wire()["toolCallName"] == "search"
    assert _types(b) == ["TOOL_CALL_ARGS"]
    assert b[0].to_wire()["toolCallId"] == "c1"
    assert _types(c) == ["TOOL_CALL_END", "TOOL_CALL_RESULT"]
    assert c[1].to_wire() == {
        "type": "TOOL_CALL_RESULT",
        "timestamp": 5,
        "messageId": "tm1",
        "toolCallId": "c1",
        "content": "found",
        "role": "tool",
    }


def test_tool_message_non_string_content_is_json_encoded() -> None:
    ctx = _ctx()
    msg = ToolMessage(content=[{"type": "text", "text": "a"}], tool_call_id="c")
    events = to_agui_events(("messages", (msg, {})), ctx)
    assert _types(events) == ["TOOL_CALL_RESULT"]
    assert json.loads(events[0].to_wire()["content"]) == [{"type": "text", "text": "a"}]


def test_finish_closes_open_tool_calls() -> None:
    ctx = _ctx()
    to_agui_events(_tool_chunk("c1", "search", "{}"), ctx)
    assert _types(finish_run(ctx)) == ["TOOL_CALL_END", "RUN_FINISHED"]


# --------------------------------------------------------------------------- updates


def test_update_emits_step_and_state_delta() -> None:
    ctx = _ctx()
    start_run(ctx, initial_state={"current_agent": "supervisor"})
    events = to_agui_events(("updates", {"coder": {"current_agent": "coder", "messages": []}}), ctx)
    assert _types(events) == ["STEP_STARTED", "STATE_DELTA", "STEP_FINISHED"]
    assert events[0].to_wire()["stepName"] == "coder"
    assert events[1].to_wire()["delta"] == [
        {"op": "replace", "path": "/current_agent", "value": "coder"}
    ]


def test_update_without_change_or_deltas_disabled_has_no_delta() -> None:
    ctx = _ctx()
    start_run(ctx, initial_state={"current_agent": "a"})
    same = to_agui_events(("updates", {"n": {"current_agent": "a"}}), ctx)
    assert _types(same) == ["STEP_STARTED", "STEP_FINISHED"]
    off = _ctx(include_state_deltas=False)
    none_update = to_agui_events(("updates", {"n": None}), off)
    assert _types(none_update) == ["STEP_STARTED", "STEP_FINISHED"]
    changed = to_agui_events(("updates", {"n": {"current_agent": "b"}}), off)
    assert _types(changed) == ["STEP_STARTED", "STEP_FINISHED"]


def test_update_closes_streamed_message_of_that_node() -> None:
    ctx = _ctx()
    to_agui_events(_text("hi"), ctx)
    events = to_agui_events(("updates", {"agent": {}}), ctx)
    assert _types(events) == ["TEXT_MESSAGE_END", "STEP_STARTED", "STEP_FINISHED"]


def test_routing_and_decision_metadata_emit_custom_once() -> None:
    ctx = _ctx()
    start_run(ctx, initial_state={})
    upd = {"metadata": {"routing": {"model": "m"}, "decision": {"label": "x"}}}
    events = to_agui_events(("updates", {"supervisor": upd}), ctx)
    customs = [e.to_wire() for e in events if e.type == "CUSTOM"]
    assert customs == [
        {"type": "CUSTOM", "timestamp": 4, "name": "prismal.routing", "value": {"model": "m"}},
        {"type": "CUSTOM", "timestamp": 5, "name": "prismal.decision", "value": {"label": "x"}},
    ]
    # routing / decision never leak through the state delta
    assert all(e.type != "STATE_DELTA" for e in events)
    again = to_agui_events(("updates", {"supervisor": upd}), ctx)
    assert all(e.type != "CUSTOM" for e in again)


def test_metadata_custom_suppressed_when_include_metadata_off() -> None:
    ctx = _ctx(include_metadata=False)
    upd = {"metadata": {"routing": {"model": "m"}}}
    events = to_agui_events(("updates", {"supervisor": upd}), ctx)
    assert all(e.type != "CUSTOM" for e in events)


def test_subgraph_three_tuple_updates_emit_steps_only() -> None:
    ctx = _ctx()
    chunk = (("skynet:abc",), "updates", {"worker": {"current_agent": "w"}})
    assert _types(to_agui_events(chunk, ctx)) == ["STEP_STARTED", "STEP_FINISHED"]
    msg_chunk = (("skynet:abc",), "messages", (AIMessageChunk(content="x", id="m"), {}))
    assert _types(to_agui_events(msg_chunk, ctx)) == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]


# --------------------------------------------------------------------------- RFC 6902


def test_json_patch_diff_add_remove_replace_and_escaping() -> None:
    old = {"a": 1, "b": {"c": 2, "x/y": 1}, "gone": True, "l": [1]}
    new = {"a": 1, "b": {"c": 3, "x/y": 1, "t~": 0}, "l": [1, 2], "new": None}
    assert json_patch_diff(old, new) == [
        {"op": "replace", "path": "/b/c", "value": 3},
        {"op": "add", "path": "/b/t~0", "value": 0},
        {"op": "remove", "path": "/gone"},
        {"op": "replace", "path": "/l", "value": [1, 2]},
        {"op": "add", "path": "/new", "value": None},
    ]
    assert json_patch_diff({"x/y": 1}, {}) == [{"op": "remove", "path": "/x~1y"}]
    assert json_patch_diff({"a": 1}, {"a": 1}) == []


# --------------------------------------------------------------------------- sub-agents


def _sub(phase: str, sid: str = "w1", **kw: Any) -> tuple[str, Any]:
    return ("custom", {"type": "subagent", "phase": phase, "id": sid, **kw})


def test_subagent_start_finish_and_error() -> None:
    ctx = _ctx()
    start = to_agui_events(_sub("start", name="worker", description="d", parent_id="p"), ctx)
    end = to_agui_events(_sub("end", result={"ok": True}), ctx)
    to_agui_events(_sub("start", "w2", name="worker"), ctx)
    err = to_agui_events(_sub("error", "w2", message="boom", code="E"), ctx)
    assert start[0].to_wire()["subagentRunId"] == "w1"
    assert start[0].to_wire()["parentSubagentRunId"] == "p"
    assert start[0].to_wire()["description"] == "d"
    assert end[0].to_wire()["outcome"] == {"type": "success"}
    assert end[0].to_wire()["result"] == {"ok": True}
    assert err[0].to_wire()["message"] == "boom" and err[0].to_wire()["code"] == "E"
    assert ctx.active_subagents == {}


def test_subagent_end_for_unknown_id_is_raw() -> None:
    assert _types(to_agui_events(_sub("end", "nope"), _ctx())) == ["RAW"]


def test_finish_closes_active_subagents_as_errors() -> None:
    ctx = _ctx()
    to_agui_events(_sub("start", name="w"), ctx)
    events = finish_run(ctx)
    assert _types(events) == ["SUBAGENT_ERROR", "RUN_FINISHED"]
    assert events[0].to_wire()["code"] == "subagent_incomplete"


def test_other_custom_payload_is_raw_with_source() -> None:
    events = to_agui_events(("custom", {"progress": 0.5}), _ctx())
    assert events[0].to_wire()["event"] == {"mode": "custom", "payload_type": "dict"}
    assert events[0].to_wire()["source"] == "langgraph.custom"


# --------------------------------------------------------------------------- interrupt


def test_interrupt_is_deferred_to_run_finished_outcome() -> None:
    ctx = _ctx()
    payload = {"message": "Approve?", "risk_level": "HIGH", "tool_call_id": "c1"}
    events = to_agui_events(
        ("updates", {"__interrupt__": (Interrupt(value=payload, id="i1"),)}), ctx
    )
    assert events == []
    done = finish_run(ctx)[-1].to_wire()
    assert done["outcome"] == {
        "type": "interrupt",
        "interrupts": [
            {
                "id": "i1",
                "reason": "human_approval",
                "message": "Approve?",
                "toolCallId": "c1",
                "metadata": {"value": payload},
            }
        ],
    }


def test_interrupt_with_string_value_and_reason() -> None:
    ctx = _ctx()
    to_agui_events(("updates", {"__interrupt__": [Interrupt(value="why?", id="i2")]}), ctx)
    to_agui_events(
        ("updates", {"__interrupt__": [Interrupt(value={"reason": "input"}, id="i3")]}), ctx
    )
    interrupts = finish_run(ctx)[-1].to_wire()["outcome"]["interrupts"]
    assert interrupts[0] == {
        "id": "i2",
        "reason": "human_approval",
        "message": "why?",
        "metadata": {"value": "why?"},
    }
    assert interrupts[1]["reason"] == "input"


# --------------------------------------------------------------------------- finish / usage


def test_finish_emits_usage_custom_before_run_finished_with_native_usage() -> None:
    ctx = _ctx()
    summary = _summary(
        prompt_tokens=100,
        completion_tokens=20,
        cache_read_tokens=40,
        reasoning_tokens=5,
        calls=1,
        cost_usd=0.01,
        cost_source="gateway",
        gateway="openrouter",
        served_model="anthropic/claude-sonnet-4.5",
    )
    events = finish_run(ctx, summary=summary, result={"answer": 42})
    assert _types(events) == ["CUSTOM", "RUN_FINISHED"]
    usage = events[0].to_wire()
    assert usage["name"] == "prismal.usage"
    assert list(usage["value"]) == list(summary.to_usage_wire())
    done = events[1].to_wire()
    assert done["outcome"] == {"type": "success"}
    assert done["result"] == {"answer": 42}
    assert done["usage"] == [
        {
            "provider": "openrouter",
            "model": "anthropic/claude-sonnet-4.5",
            "inputTokens": 100,
            "outputTokens": 20,
            "totalTokens": 120,
            "reasoningTokens": 5,
            "cachedInputTokens": 40,
            "cacheWriteInputTokens": 0,
        }
    ]


def test_native_usage_provider_derived_from_served_model_prefix() -> None:
    done = finish_run(_ctx(), summary=_summary(served_model="openai/gpt-x"))[-1].to_wire()
    assert done["usage"][0]["provider"] == "openai"
    unknown = finish_run(_ctx(), summary=_summary())[-1].to_wire()
    assert unknown["usage"][0]["provider"] == "unknown"
    assert unknown["usage"][0]["model"] == "unknown"


def test_include_usage_off_suppresses_both_usage_channels() -> None:
    events = finish_run(_ctx(include_usage=False), summary=_summary(calls=1))
    assert _types(events) == ["RUN_FINISHED"]
    assert "usage" not in events[0].to_wire()


def test_error_run_closes_open_items_then_run_error() -> None:
    ctx = _ctx()
    to_agui_events(_text("partial"), ctx)
    to_agui_events(_tool_chunk("c1", "f", ""), ctx)
    events = error_run(ctx, code="budget_exceeded", message="hard cap", summary=_summary(calls=1))
    assert _types(events) == ["TEXT_MESSAGE_END", "TOOL_CALL_END", "CUSTOM", "RUN_ERROR"]
    err = events[-1].to_wire()
    assert err["code"] == "budget_exceeded" and err["message"] == "hard cap"
    assert err["usage"][0]["provider"] == "unknown"


# --------------------------------------------------------------------------- robustness


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        42,
        "text",
        b"bytes",
        ("messages",),
        ("messages", "not-a-tuple"),
        ("messages", (object(), {})),
        ("updates", "x"),
        ("updates", {"n": "not-a-dict"}),
        ("weird-mode", {"a": 1}),
        (1, 2, 3, 4),
        {"set": {1, 2}},
    ],
)
def test_never_raises_on_garbage(garbage: Any) -> None:
    events = to_agui_events(garbage, _ctx())
    assert isinstance(events, list)
    for ev in events:
        json.loads(agui.to_sse(ev)[6:-2])


def test_unknown_chunk_is_raw_with_payload_type_only() -> None:
    events = to_agui_events(("weird-mode", {"a": object()}), _ctx())
    wire = events[0].to_wire()
    assert wire["type"] == "RAW"
    assert wire["event"] == {"mode": "weird-mode", "payload_type": "dict"}


def test_internal_error_returns_empty_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(agui, "_increment", lambda m, attrs: calls.append((m, attrs)))

    def boom(*_a: Any, **_k: Any) -> list[AgUiEvent]:
        raise RuntimeError("bug")

    monkeypatch.setattr(agui, "_translate", boom)
    assert to_agui_events(_text("x"), _ctx()) == []
    assert calls == [("agui_translation_errors", {"mode": "messages"})]


def test_finish_and_error_run_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agui, "_increment", lambda m, attrs: None)
    bad = _summary()
    monkeypatch.setattr(type(bad), "to_usage_wire", lambda self: 1 / 0)
    assert finish_run(_ctx(), summary=bad) == []
    assert error_run(_ctx(), code="x", message="y", summary=bad) == []


def test_events_are_counted_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(agui, "_increment", lambda m, attrs: calls.append((m, attrs)))
    ctx = _ctx()
    start_run(ctx)
    to_agui_events(_text("x"), ctx)
    assert calls == [
        ("agui_events", {"type": "RUN_STARTED"}),
        ("agui_events", {"type": "STATE_SNAPSHOT"}),
        ("agui_events", {"type": "TEXT_MESSAGE_START"}),
        ("agui_events", {"type": "TEXT_MESSAGE_CONTENT"}),
    ]


def test_increment_swallows_otel_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from prismal.monitoring import otel

    def explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("otel down")

    monkeypatch.setattr(otel.OTelManager, "increment_counter", explode)
    agui._increment("agui_events", {"type": "RAW"})  # must not raise


def test_default_clock_is_epoch_ms() -> None:
    ctx = AgUiContext(run_id="r", thread_id="t")
    ts = start_run(ctx)[0].timestamp
    assert ts is not None and ts > 1_600_000_000_000


# --------------------------------------------------------------------------- golden transcripts


def _fixture_files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def test_twelve_golden_transcripts_are_committed() -> None:
    names = {p.stem for p in _fixture_files()}
    assert names == set(_transcripts.TRANSCRIPTS)
    assert len(names) == 12


@pytest.mark.parametrize("name", sorted(_transcripts.TRANSCRIPTS))
def test_golden_transcript_matches_fixture(name: str) -> None:
    committed = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    produced = _transcripts.render(name)
    assert produced["input"] == committed["input"], "fixture input drifted; regenerate"
    assert produced["expected"] == committed["expected"]


def _balanced(events: list[dict[str, Any]]) -> None:
    open_msgs: set[str] = set()
    open_tools: set[str] = set()
    open_subs: set[str] = set()
    open_steps: list[str] = []
    for ev in events:
        t = ev["type"]
        if t == "TEXT_MESSAGE_START":
            assert ev["messageId"] not in open_msgs
            open_msgs.add(ev["messageId"])
        elif t in {"TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"}:
            assert ev["messageId"] in open_msgs
            if t == "TEXT_MESSAGE_END":
                open_msgs.remove(ev["messageId"])
        elif t == "TOOL_CALL_START":
            assert ev["toolCallId"] not in open_tools
            open_tools.add(ev["toolCallId"])
        elif t in {"TOOL_CALL_ARGS", "TOOL_CALL_END"}:
            assert ev["toolCallId"] in open_tools
            if t == "TOOL_CALL_END":
                open_tools.remove(ev["toolCallId"])
        elif t == "SUBAGENT_STARTED":
            open_subs.add(ev["subagentRunId"])
        elif t in {"SUBAGENT_FINISHED", "SUBAGENT_ERROR"}:
            open_subs.remove(ev["subagentRunId"])
        elif t == "STEP_STARTED":
            open_steps.append(ev["stepName"])
        elif t == "STEP_FINISHED":
            assert open_steps.pop() == ev["stepName"]
    assert not open_msgs and not open_tools and not open_subs and not open_steps
    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] in {"RUN_FINISHED", "RUN_ERROR"}


@pytest.mark.parametrize("name", sorted(_transcripts.TRANSCRIPTS))
def test_golden_transcript_is_balanced_and_usage_precedes_finish(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["expected"]
    _balanced(expected)
    names = [e.get("name") for e in expected]
    if "prismal.usage" in names:
        assert names.index("prismal.usage") == len(expected) - 2
    for ev in expected:
        assert ev["type"] in {m.value for m in agui.AgUiEventType}
