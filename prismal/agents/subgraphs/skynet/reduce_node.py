"""reduce node — merge the workers' results into one answer (SPEC-SKY-SG-001).

Filters the shared ``parallel_results`` channel for this subgraph's tagged
entries, deduplicates them by ``order_id`` (the latest attempt wins —
idempotent merge, DD-SKY-004), and runs :func:`reduce_results` with the
operator-configured strategy.  The deduplicated results and the answer are
written to ``metadata.skynet`` for the evaluator.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from prismal.agents.skynet.reduce import reduce_results
from prismal.agents.subgraphs.skynet._helpers import (
    WORKER_TAG,
    dicts_to_results,
    get_skynet,
    merge_skynet,
)
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.skynet.reduce import ReduceFn
    from prismal.core.config import Settings

logger = get_logger("prismal.subgraphs.skynet.reduce")


def make_reduce_node(
    settings: Settings,
    *,
    reduce_fn: ReduceFn | None = None,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the reduce node (strategy from ``settings.skynet_reduce_strategy``)."""

    async def reduce_node(state: dict[str, Any]) -> dict[str, Any]:
        """Dedupe the tagged worker results and synthesize the round answer."""
        skynet = get_skynet(state)
        goal = str(skynet.get("goal") or "")

        by_id: dict[str, dict[str, Any]] = {}
        for entry in state.get("parallel_results") or []:
            if not (isinstance(entry, dict) and entry.get("agent") == WORKER_TAG):
                continue
            result_dict = entry.get("result")
            if isinstance(result_dict, dict) and result_dict.get("order_id"):
                # entries append in round order — the latest attempt wins
                by_id[str(result_dict["order_id"])] = result_dict

        results = dicts_to_results(list(by_id.values()))
        answer = await reduce_results(
            goal,
            results,
            reduce_fn=reduce_fn,
            strategy=settings.skynet_reduce_strategy,
            settings=settings,
        )
        logger.info(
            "skynet_reduced",
            results=len(results),
            failures=sum(1 for r in results if not r.success),
        )
        update = merge_skynet(
            state,
            results=[asdict(r) for r in results],
            answer=answer,
        )
        update["current_agent"] = "skynet_reduce"
        return update

    return reduce_node


__all__ = ["make_reduce_node"]
