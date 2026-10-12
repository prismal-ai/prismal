"""Sub-agent lifecycle payloads for AG-UI (PRT follow-up, ``docs/protocols-2026.md``).

``events/agui.py`` translates ``{"type": "subagent", ...}`` custom payloads into
``SUBAGENT_*`` events. Skynet workers and MoA proposers now emit them through
LangGraph's ``get_stream_writer()``; outside a run (or when the caller does not
stream ``custom``) emission is a silent no-op. Payloads never carry worker or
model output, only ids, names and an error *type*.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from unittest.mock import AsyncMock, MagicMock

from prismal.agents.patterns.mixture_of_agents import MixtureOfAgents
from prismal.agents.skynet.types import WorkerResult
from prismal.agents.subgraphs.skynet.worker_node import make_worker_node
from prismal.events.agui import AgUiContext, to_agui_events
from prismal.events.subagent import emit_subagent
from prismal.langgraph import END, START, StateGraph


class _S(TypedDict, total=False):
    x: int


async def _custom_payloads(fn: Callable[[], Awaitable[Any]]) -> list[Any]:
    """Run *fn* inside a one-node graph and return what it wrote to ``custom``."""

    async def node(_state: _S) -> dict[str, Any]:
        await fn()
        return {}

    builder = StateGraph(_S)
    builder.add_node("n", node)
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    return [chunk async for chunk in builder.compile().astream({"x": 0}, stream_mode="custom")]


async def test_emit_outside_a_run_is_a_no_op() -> None:
    emit_subagent("start", "sa-1", name="worker")  # must not raise


async def test_emit_writes_the_agui_subagent_shape() -> None:
    async def run() -> None:
        emit_subagent("start", "sa-1", name="worker", description="d")
        emit_subagent("end", "sa-1")

    payloads = await _custom_payloads(run)

    assert payloads == [
        {"type": "subagent", "phase": "start", "id": "sa-1", "name": "worker", "description": "d"},
        {"type": "subagent", "phase": "end", "id": "sa-1"},
    ]
    ctx = AgUiContext(thread_id="t", run_id="r")
    kinds = [type(e).__name__ for p in payloads for e in to_agui_events(("custom", p), ctx)]
    assert kinds == ["SubagentStarted", "SubagentFinished"]


def _worker(result: WorkerResult) -> MagicMock:
    worker = MagicMock()
    worker.execute = AsyncMock(return_value=result)
    return worker


def _order_state() -> dict[str, Any]:
    return {"_order": {"order_id": "o1", "instruction": "summarise", "attempt": 2}}


async def test_skynet_worker_emits_start_and_end() -> None:
    node = make_worker_node(_worker(WorkerResult(order_id="o1", output="secret", success=True)))

    payloads = await _custom_payloads(lambda: node(_order_state()))

    assert [(p["phase"], p["id"]) for p in payloads] == [
        ("start", "skynet:o1:2"),
        ("end", "skynet:o1:2"),
    ]
    assert payloads[0]["name"] == "skynet_worker"
    assert "secret" not in repr(payloads)


async def test_skynet_worker_failure_emits_error() -> None:
    failed = WorkerResult(order_id="o1", output="", success=False, error="boom: token=abc")
    node = make_worker_node(_worker(failed))

    payloads = await _custom_payloads(lambda: node(_order_state()))

    assert [p["phase"] for p in payloads] == ["start", "error"]
    assert payloads[1]["code"] == "worker_failed"
    assert "token=abc" not in repr(payloads)


async def test_moa_proposers_emit_lifecycle_per_model() -> None:
    ok_llm = MagicMock()
    ok_llm.ainvoke = AsyncMock(return_value=MagicMock(content="proposal"))
    bad_llm = MagicMock()
    bad_llm.ainvoke = AsyncMock(side_effect=RuntimeError("provider down: key=xyz"))
    registry = MagicMock()
    registry.get_llm.side_effect = lambda model: {"m-ok": ok_llm, "m-bad": bad_llm}[model]
    moa = MixtureOfAgents(proposer_models=["m-ok", "m-bad"])

    payloads = await _custom_payloads(lambda: moa._run_proposers(registry, "q"))

    by_id: dict[str, list[str]] = {}
    for p in payloads:
        by_id.setdefault(p["id"], []).append(p["phase"])
    assert by_id == {"moa:0:m-ok": ["start", "end"], "moa:1:m-bad": ["start", "error"]}
    errors = [p for p in payloads if p["phase"] == "error"]
    assert errors[0]["code"] == "RuntimeError"
    assert "key=xyz" not in repr(payloads)
