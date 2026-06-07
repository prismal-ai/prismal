"""worker node — execute one dispatched order (SPEC-SKY-SG-001).

One LangGraph ``Send`` invocation per order: the dispatcher injects the
serialized :class:`SwarmOrder` under ``state["_order"]`` (the ``task_key``).
The node runs :meth:`SwarmWorker.execute` and appends one tagged entry to the
``parallel_results`` ``operator.add`` channel, so concurrent workers merge
without clobbering (DD-SKY-004; same mechanism as the dev_pipeline parallel
unit tester).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from prismal.agents.skynet.types import SwarmOrder, WorkerResult
from prismal.agents.subgraphs.skynet._helpers import WORKER_TAG
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.skynet.worker import SwarmWorker

logger = get_logger("prismal.subgraphs.skynet.worker")


def make_worker_node(
    worker: SwarmWorker,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the worker node around an injected :class:`SwarmWorker`."""

    async def worker_node(state: dict[str, Any]) -> dict[str, Any]:
        """Execute ``state['_order']`` and append the tagged WorkerResult."""
        raw_order: Any = state.get("_order")
        if isinstance(raw_order, dict) and raw_order:
            order = SwarmOrder(**raw_order)
            result = await worker.execute(order)
        else:  # defensive — the dispatcher always injects the order
            logger.warning("skynet_worker_missing_order")
            result = WorkerResult(
                order_id="unknown",
                output="",
                success=False,
                error="missing order payload",
            )
        return {
            "parallel_results": [
                {
                    "agent": WORKER_TAG,
                    "order_id": result.order_id,
                    "result": asdict(result),
                }
            ]
        }

    return worker_node


__all__ = ["make_worker_node"]
