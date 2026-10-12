"""Error semantics of ``@prismal_node`` (NTS.1 prerequisite).

1. LangGraph control flow (``GraphBubbleUp``: ``interrupt()`` → ``GraphInterrupt``,
   ``ParentCommand``) is an ``Exception`` subclass. ``error_mapping`` used to
   swallow it into an error update (or wrap it), silently breaking HITL pauses
   in any decorated node. It must always bubble up unchanged.
2. ``propagate_errors=True`` re-raises a node's own exception unchanged, so a
   built-in node can adopt I/O contracts without changing how its failures
   reach the graph (no swallowing, no ``metadata`` clobbering, no wrapping).
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.errors import GraphInterrupt

from prismal.agents.extension import prismal_node


@pytest.mark.parametrize("raise_on_error", [False, True])
async def test_graph_interrupt_always_bubbles_up_unchanged(raise_on_error: bool) -> None:
    pause = GraphInterrupt(())

    @prismal_node(name="hitl_node", security="off", audit=False, raise_on_error=raise_on_error)
    async def node(_state: dict[str, Any]) -> dict[str, Any]:
        raise pause

    with pytest.raises(GraphInterrupt) as caught:
        await node({"messages": []})
    assert caught.value is pause


async def test_propagate_errors_reraises_the_original_exception() -> None:
    boom = ValueError("provider down")

    @prismal_node(name="core_node", security="off", audit=False, propagate_errors=True)
    async def node(_state: dict[str, Any]) -> dict[str, Any]:
        raise boom

    with pytest.raises(ValueError) as caught:
        await node({"messages": []})
    assert caught.value is boom


async def test_default_still_maps_errors_to_an_update() -> None:
    @prismal_node(name="plugin_node", security="off", audit=False)
    async def node(_state: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("x")

    update = await node({"messages": []})
    assert update["metadata"]["error"]["type"] == "ValueError"


def test_propagate_errors_is_recorded_on_the_metadata() -> None:
    @prismal_node(name="core_node2", security="off", audit=False, propagate_errors=True)
    async def node(_state: dict[str, Any]) -> dict[str, Any]:
        return {}

    assert node.__prismal_node__.propagate_errors is True


async def test_retry_never_retries_a_graph_interrupt() -> None:
    from prismal.agents.extension import RetryPolicy

    calls: list[int] = []

    @prismal_node(
        name="hitl_retry",
        security="off",
        audit=False,
        retry=RetryPolicy(max_attempts=3, backoff_s=(0.0,), retry_on=(Exception,)),
    )
    async def node(_state: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        await node({"messages": []})
    assert calls == [1]
