"""Golden AG-UI transcripts (PRT5-01): deterministic ``graph.astream`` chunk streams.

Each transcript is built from real ``langchain_core`` message classes and LangGraph
``Interrupt`` objects exactly as ``graph.astream(stream_mode=["messages", "updates",
"custom"])`` yields them (``(mode, payload)`` tuples, or ``(namespace, mode, payload)`` with
``subgraphs=True``), then replayed through :mod:`prismal.events.agui`.

The committed fixtures live in ``tests/fixtures/protocols/agui/<name>.json`` and hold the
serialized input chunks plus the expected AG-UI events (camelCase wire dicts). Regenerate with::

    uv run python -m tests.unit.events._transcripts --write

Sub-agent custom payload consumed by the translator (``stream_mode="custom"``)::

    {"type": "subagent", "phase": "start" | "end" | "error", "id": <subagent run id>,
     "name": str, "description"?: str, "parent_id"?: str, "parent_tool_call_id"?: str,
     "parent_message_id"?: str, "result"?: Any, "message"?: str, "code"?: str}
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from langgraph.types import Interrupt

from prismal.budget.types import Usage
from prismal.events.agui import (
    AgUiContext,
    AgUiEvent,
    error_run,
    finish_run,
    start_run,
    to_agui_events,
)
from prismal.monitoring.observability_types import RunSummary

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "protocols" / "agui"


@dataclass
class Transcript:
    description: str
    chunks: list[Any]
    initial_state: dict[str, Any] | None = None
    ctx_kwargs: dict[str, Any] = field(default_factory=dict)
    summary: RunSummary | None = None
    result: dict[str, Any] | None = None
    error: tuple[str, str] | None = None  # (code, message) ⇒ error_run instead of finish_run


def _summary(**usage: Any) -> RunSummary:
    return RunSummary(
        run_id="run-1",
        session_id="thread-1",
        agent_name="supervisor",
        visited_nodes=[],
        spans=[],
        tool_calls=[],
        usage=Usage(**usage),
        latency_ms=12.5,
    )


_GW_SUMMARY = {
    "prompt_tokens": 1200,
    "completion_tokens": 300,
    "cache_read_tokens": 800,
    "reasoning_tokens": 40,
    "calls": 2,
    "cost_usd": 0.004321,
    "cost_source": "gateway",
    "gateway": "openrouter",
    "served_model": "anthropic/claude-sonnet-4.5",
}


def _msg(node: str, message: BaseMessage) -> tuple[str, Any]:
    return ("messages", (message, {"langgraph_node": node, "langgraph_step": 1}))


def _text(node: str, mid: str, text: str, *, last: bool = False) -> tuple[str, Any]:
    kw: dict[str, Any] = {"chunk_position": "last"} if last else {}
    return _msg(node, AIMessageChunk(content=text, id=mid, **kw))


def _tool(
    node: str, mid: str, chunks: list[tuple[str | None, str | None, str, int]]
) -> tuple[str, Any]:
    tcc = [{"id": cid, "name": name, "args": args, "index": idx} for cid, name, args, idx in chunks]
    return _msg(node, AIMessageChunk(content="", id=mid, tool_call_chunks=tcc))


def _sub(phase: str, sid: str, **kw: Any) -> tuple[str, Any]:
    return ("custom", {"type": "subagent", "phase": phase, "id": sid, **kw})


def react_text() -> Transcript:
    return Transcript(
        "Plain ReAct answer streamed as text chunks, then the node update.",
        [
            _text("agent", "msg-1", "Hello"),
            _text("agent", "msg-1", ", world"),
            _text("agent", "msg-1", "", last=True),
            ("updates", {"agent": {"current_agent": "agent", "messages": ["<omitted>"]}}),
        ],
        initial_state={"current_agent": "supervisor"},
        summary=_summary(**_GW_SUMMARY),
    )


def single_tool_call() -> Transcript:
    return Transcript(
        "One streamed tool call (args in fragments), its result, then the final answer.",
        [
            _tool("agent", "msg-1", [("call-1", "web_search", '{"query": ', 0)]),
            _tool("agent", "msg-1", [(None, None, '"ag-ui"}', 0)]),
            _msg("tools", ToolMessage(content="3 results", tool_call_id="call-1", id="tool-1")),
            _text("agent", "msg-2", "Found 3 results."),
            ("updates", {"agent": {"current_agent": "agent"}}),
        ],
        initial_state={"current_agent": "supervisor"},
        summary=_summary(**_GW_SUMMARY),
    )


def parallel_tool_calls() -> Transcript:
    return Transcript(
        "Two tool calls streamed in parallel (indices 0/1) and both results.",
        [
            _tool(
                "agent", "msg-1", [("call-a", "get_weather", "", 0), ("call-b", "get_time", "", 1)]
            ),
            _tool("agent", "msg-1", [(None, None, '{"city": "Lima"}', 0)]),
            _tool("agent", "msg-1", [(None, None, '{"tz": "UTC"}', 1)]),
            _msg("tools", ToolMessage(content="18C", tool_call_id="call-a", id="tool-a")),
            _msg("tools", ToolMessage(content="12:00", tool_call_id="call-b", id="tool-b")),
            ("updates", {"tools": {"tool_results": [{"n": 2}]}}),
        ],
        ctx_kwargs={"include_usage": False},
    )


def tool_error() -> Transcript:
    return Transcript(
        "A tool call whose ToolMessage has status=error; content is passed through as produced.",
        [
            _tool("agent", "msg-1", [("call-1", "read_file", '{"path": "/x"}', 0)]),
            _msg(
                "tools",
                ToolMessage(
                    content="Error: permission denied",
                    tool_call_id="call-1",
                    id="tool-1",
                    status="error",
                ),
            ),
            ("updates", {"tools": {"tool_errors": [{"tool": "read_file"}]}}),
            _text("agent", "msg-2", "I could not read the file.", last=True),
        ],
        initial_state={},
        summary=_summary(prompt_tokens=50, completion_tokens=10, calls=1, cost_source="table"),
    )


def multi_step_state_delta() -> Transcript:
    return Transcript(
        "Supervisor → planner → coder updates; RFC 6902 STATE_DELTA over the allowlisted view.",
        [
            (
                "updates",
                {
                    "supervisor": {
                        "next_agent": "planner",
                        "metadata": {"security": {"canary": "zzz"}, "intent": "code"},
                    }
                },
            ),
            (
                "updates",
                {"planner": {"task_plan": ["design", "build"], "pending_tasks": ["build"]}},
            ),
            (
                "updates",
                {
                    "coder": {
                        "completed_tasks": ["design", "build"],
                        "pending_tasks": [],
                        "next_agent": None,
                        "risk_score": 2.0,
                    }
                },
            ),
        ],
        initial_state={"current_agent": "supervisor", "next_agent": None, "metadata": {}},
        result={"status": "ok"},
    )


def skynet_subagents() -> Transcript:
    return Transcript(
        "Skynet swarm: two workers (subgraph namespace), one succeeds, one fails.",
        [
            ("updates", {"skynet_plan": {"skynet_orders": [{"id": "o1"}, {"id": "o2"}]}}),
            _sub("start", "worker-o1", name="skynet_worker", description="order o1"),
            _sub("start", "worker-o2", name="skynet_worker", description="order o2"),
            (
                ("skynet:worker-o1",),
                "messages",
                (AIMessageChunk(content="partial o1", id="w1-msg"), {"langgraph_node": "worker"}),
            ),
            (("skynet:worker-o1",), "updates", {"worker": {"parallel_results": [{"o": 1}]}}),
            _sub("end", "worker-o1", result={"order": "o1", "ok": True}),
            _sub("error", "worker-o2", message="worker timed out", code="timeout"),
            ("updates", {"reduce": {"current_agent": "skynet"}}),
        ],
        initial_state={"current_agent": "supervisor"},
        summary=_summary(**_GW_SUMMARY),
    )


def moa_proposers() -> Transcript:
    return Transcript(
        "Mixture-of-Agents: three proposers as sub-agents, then the aggregator's answer.",
        [
            _sub("start", "proposer-0", name="moa_proposer", parent_tool_call_id="call-moa"),
            _sub("start", "proposer-1", name="moa_proposer", parent_tool_call_id="call-moa"),
            _sub("start", "proposer-2", name="moa_proposer", parent_tool_call_id="call-moa"),
            _sub("end", "proposer-1", result="B"),
            _sub("end", "proposer-0", result="A"),
            _sub("end", "proposer-2", result="C"),
            _text("aggregator", "msg-agg", "Synthesis of A, B, C.", last=True),
            ("updates", {"aggregator": {"current_agent": "aggregator"}}),
        ],
        ctx_kwargs={"include_state_deltas": False},
        summary=_summary(prompt_tokens=10, completion_tokens=5, calls=4, served_model="openai/x"),
    )


def hitl_interrupt() -> Transcript:
    payload = {
        "artifact_field": "review_result",
        "artifact": {"diff": "+1 line"},
        "risk_level": "HIGH",
        "options": ["approve", "reject", "request_changes"],
        "message": "Approval required for: review_result",
    }
    return Transcript(
        "HITL gate: interrupt() ⇒ RUN_FINISHED.outcome {type: interrupt} (no INTERRUPT event).",
        [
            ("updates", {"reviewer": {"current_agent": "reviewer"}}),
            ("updates", {"__interrupt__": (Interrupt(value=payload, id="intr-1"),)}),
        ],
        initial_state={"current_agent": "supervisor"},
        summary=_summary(prompt_tokens=30, completion_tokens=3, calls=1, cost_source="litellm"),
    )


def routing_decision_metadata() -> Transcript:
    return Transcript(
        "metadata.routing / metadata.decision changes ⇒ CUSTOM prismal.routing / prismal.decision.",
        [
            (
                "updates",
                {
                    "supervisor": {
                        "next_agent": "coder",
                        "metadata": {
                            "routing": {"tier": "fast", "model": "gpt-mini"},
                            "decision": {"question": "route", "label": "coder", "tier": "rule"},
                        },
                    }
                },
            ),
            (
                "updates",
                {
                    "coder": {
                        "metadata": {
                            "routing": {"tier": "fast", "model": "gpt-mini"},
                            "decision": {"question": "route", "label": "END", "tier": "laya"},
                        }
                    }
                },
            ),
        ],
        initial_state={"next_agent": None},
    )


def run_error() -> Transcript:
    return Transcript(
        "Run aborted mid-stream: open message and tool call are closed before RUN_ERROR.",
        [
            _text("agent", "msg-1", "Working on it"),
            _tool("agent", "msg-1", [("call-1", "python", '{"code": "1+', 0)]),
        ],
        initial_state={"current_agent": "agent"},
        summary=_summary(prompt_tokens=500, completion_tokens=0, calls=1, cost_source="table"),
        error=("budget_exceeded", "Hard budget cap reached"),
    )


def empty_run() -> Transcript:
    return Transcript(
        "No chunks at all: RUN_STARTED, snapshot, RUN_FINISHED.", [], initial_state={}
    )


def unknown_chunk_raw() -> Transcript:
    return Transcript(
        "Unknown modes and custom payloads fall back to RAW carrying only {mode, payload_type}.",
        [
            ("debug", {"step": 1, "payload": {"type": "task"}}),
            ("custom", {"progress": 0.5}),
            ("custom", {"type": "subagent", "phase": "end", "id": "never-started"}),
        ],
        ctx_kwargs={"include_state_deltas": False, "include_usage": False},
    )


TRANSCRIPTS: dict[str, Callable[[], Transcript]] = {
    "react_text": react_text,
    "single_tool_call": single_tool_call,
    "parallel_tool_calls": parallel_tool_calls,
    "tool_error": tool_error,
    "multi_step_state_delta": multi_step_state_delta,
    "skynet_subagents": skynet_subagents,
    "moa_proposers": moa_proposers,
    "hitl_interrupt": hitl_interrupt,
    "routing_decision_metadata": routing_decision_metadata,
    "run_error": run_error,
    "empty_run": empty_run,
    "unknown_chunk_raw": unknown_chunk_raw,
}


def serialize(obj: Any) -> Any:
    """JSON form of an astream chunk (messages tagged with their class name)."""
    if isinstance(obj, BaseMessage):
        return {"__class__": type(obj).__name__, **obj.model_dump(mode="json", exclude_none=True)}
    if isinstance(obj, Interrupt):
        return {"__class__": "Interrupt", "value": serialize(obj.value), "id": obj.id}
    if isinstance(obj, dict):
        return {str(k): serialize(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [serialize(v) for v in obj]
    return obj


def run(t: Transcript) -> list[AgUiEvent]:
    counter = itertools.count(1_760_000_000_000)
    ctx = AgUiContext(
        run_id="run-1", thread_id="thread-1", clock=lambda: next(counter), **t.ctx_kwargs
    )
    events = start_run(ctx, initial_state=t.initial_state)
    for chunk in t.chunks:
        events.extend(to_agui_events(chunk, ctx))
    if t.error is not None:
        events.extend(error_run(ctx, code=t.error[0], message=t.error[1], summary=t.summary))
    else:
        events.extend(finish_run(ctx, summary=t.summary, result=t.result))
    return events


def render(name: str) -> dict[str, Any]:
    t = TRANSCRIPTS[name]()
    return {
        "name": name,
        "description": t.description,
        "context": {"run_id": "run-1", "thread_id": "thread-1", **t.ctx_kwargs},
        "initial_state": t.initial_state,
        "input": serialize(t.chunks),
        "end": (
            {"kind": "error", "code": t.error[0], "message": t.error[1]}
            if t.error
            else {"kind": "finish", "result": t.result}
        ),
        "summary_usage_wire": t.summary.to_usage_wire() if t.summary else None,
        "expected": [e.to_wire() for e in run(t)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="(re)write the JSON fixtures")
    args = parser.parse_args(argv)
    if not args.write:
        for name in TRANSCRIPTS:
            print(name, len(render(name)["expected"]), "events")
        return 0
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name in TRANSCRIPTS:
        text = json.dumps(render(name), indent=2, ensure_ascii=False, sort_keys=False)
        (FIXTURES / f"{name}.json").write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
