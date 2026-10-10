"""Translate a (fake) LangGraph ``astream`` into AG-UI 1.0 SSE frames — fully offline.

The translator in :mod:`prismal.events.agui` is pure: it turns each item of
``graph.astream(input, config, stream_mode=["messages", "updates", "custom"])`` into AG-UI
events; the host owns the transport. Here the chunks are hand-built so the example needs no
LLM, network or API key.

In a real host::

    ctx = AgUiContext(run_id=run_id, thread_id=thread_id)
    frames = [to_sse(e) for e in start_run(ctx, initial_state=state)]
    async for chunk in graph.astream(state, config, stream_mode=["messages", "updates", "custom"]):
        frames += [to_sse(e) for e in to_agui_events(chunk, ctx)]
    frames += [to_sse(e) for e in finish_run(ctx, summary=get_run_summary(thread_id))]

Run::

    uv run python examples/agui_translate.py
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessageChunk, ToolMessage

from prismal.budget.types import Usage
from prismal.events.agui import AgUiContext, finish_run, start_run, to_agui_events, to_sse
from prismal.monitoring.observability_types import RunSummary


def fake_astream() -> list[Any]:
    """What ``graph.astream(stream_mode=["messages", "updates", "custom"])`` would yield."""
    meta = {"langgraph_node": "agent"}
    return [
        (
            "updates",
            {"supervisor": {"next_agent": "agent", "metadata": {"routing": {"tier": "fast"}}}},
        ),
        (
            "messages",
            (
                AIMessageChunk(
                    content="",
                    id="msg-1",
                    tool_call_chunks=[
                        {"id": "call-1", "name": "web_search", "args": '{"q": "ag-ui"}', "index": 0}
                    ],
                ),
                meta,
            ),
        ),
        ("messages", (ToolMessage(content="3 results", tool_call_id="call-1", id="tool-1"), meta)),
        ("custom", {"type": "subagent", "phase": "start", "id": "w1", "name": "skynet_worker"}),
        ("custom", {"type": "subagent", "phase": "end", "id": "w1", "result": "ok"}),
        ("messages", (AIMessageChunk(content="AG-UI is ", id="msg-2"), meta)),
        ("messages", (AIMessageChunk(content="an event protocol.", id="msg-2"), meta)),
        ("updates", {"agent": {"current_agent": "agent"}}),
    ]


def main() -> None:
    ctx = AgUiContext(run_id="run-1", thread_id="thread-1")
    events = start_run(ctx, initial_state={"current_agent": "supervisor"})
    for chunk in fake_astream():
        events.extend(to_agui_events(chunk, ctx))
    summary = RunSummary(
        run_id="run-1",
        session_id="thread-1",
        agent_name="supervisor",
        visited_nodes=["supervisor", "agent"],
        spans=[],
        tool_calls=[],
        usage=Usage(prompt_tokens=120, completion_tokens=30, calls=2, cost_source="table"),
        latency_ms=850.0,
    )
    events.extend(finish_run(ctx, summary=summary))
    for event in events:
        print(to_sse(event), end="")


if __name__ == "__main__":
    main()
