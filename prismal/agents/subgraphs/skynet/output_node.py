"""output node — render the final assistant message (SPEC-SKY-SG-001).

Assembles the :class:`SwarmResult` (answer + per-worker results + deferred
overflow) under ``metadata.skynet.result`` and appends one assistant message
with the answer and a per-worker summary.  On an upstream error with no
results it emits a plain error notice instead — the graph always terminates
with a message.  The working ``skynet_orders`` channel is drained.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from prismal.agents.skynet.types import SwarmResult
from prismal.agents.subgraphs.skynet._helpers import (
    dicts_to_orders,
    dicts_to_results,
    get_skynet,
    merge_skynet,
)
from prismal.core.logging import get_logger

logger = get_logger("prismal.subgraphs.skynet.output")


async def output_node(state: dict[str, Any]) -> dict[str, Any]:
    """Append the swarm outcome (or error notice) as an assistant message."""
    skynet = get_skynet(state)
    result_dicts: list[dict[str, Any]] = list(skynet.get("results") or [])

    error = skynet.get("error")
    if error and not result_dicts:
        logger.warning("skynet_output_error", error=str(error))
        return {
            "messages": [AIMessage(content=f"Skynet could not plan: {error}")],
            "skynet_orders": [],
        }

    goal = str(skynet.get("goal") or "")
    answer = str(skynet.get("answer") or "")
    complete = bool(skynet.get("complete"))
    results = dicts_to_results(result_dicts)
    deferred = dicts_to_orders(list(skynet.get("deferred") or []))
    rounds = int(skynet.get("rounds_completed") or skynet.get("round") or 1)

    swarm_result = SwarmResult(
        goal=goal,
        answer=answer,
        worker_results=results,
        rounds_completed=rounds,
        deferred_orders=deferred,
        complete=complete,
    )

    succeeded = sum(1 for r in results if r.success)
    parts = [answer] if answer else ["Skynet produced no answer."]
    parts.append(
        f"\nSwarm summary: {succeeded}/{len(results)} orders succeeded across {rounds} round(s)."
    )
    if deferred:
        parts.append(f"Deferred orders (not executed): {len(deferred)}.")
    if not complete:
        parts.append("The goal was not fully met within the round budget.")

    logger.info(
        "skynet_output_rendered",
        complete=complete,
        rounds=rounds,
        results=len(results),
        deferred=len(deferred),
    )
    update = merge_skynet(state, result=swarm_result)
    update["messages"] = [AIMessage(content="\n".join(parts))]
    update["skynet_orders"] = []
    return update


__all__ = ["output_node"]
