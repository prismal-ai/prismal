"""evaluate node — completion check + re-plan decision (SPEC-SKY-SG-001).

Runs :meth:`SkynetSupervisor.evaluate` over the round's deduplicated results.
When the goal is not met, there is work left (failed orders and/or deferred
overflow), and the round cap allows it, the node schedules a re-plan: the
unmet orders seed the next round and ``replan=True`` makes the conditional
edge route back to the plan node (RF-SKY-07/08, DD-SKY-005).  Otherwise the
loop terminates toward the output node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.skynet._helpers import dicts_to_results, get_skynet, merge_skynet
from prismal.core.exceptions import SkynetError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.skynet.supervisor import SkynetSupervisor
    from prismal.core.config import Settings

logger = get_logger("prismal.subgraphs.skynet.evaluate")


def make_evaluate_node(
    supervisor: SkynetSupervisor,
    settings: Settings,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the evaluate node around an injected :class:`SkynetSupervisor`."""

    async def evaluate_node(state: dict[str, Any]) -> dict[str, Any]:
        """Decide completion; schedule a bounded re-plan when work remains."""
        skynet = get_skynet(state)
        goal = str(skynet.get("goal") or "")
        round_ = int(skynet.get("round") or 1)
        result_dicts: list[dict[str, Any]] = list(skynet.get("results") or [])
        results = dicts_to_results(result_dicts)

        try:
            complete, answer = await supervisor.evaluate(goal, results)
        except SkynetError as exc:
            # Degraded mode: keep the reducer's answer and stop the loop —
            # a broken evaluator must not spin the swarm (RF-SKY-08).
            logger.warning("skynet_evaluate_node_error", error=str(exc), round=round_)
            update = merge_skynet(
                state,
                complete=False,
                replan=False,
                rounds_completed=round_,
                error=str(exc),
            )
            update["current_agent"] = "skynet_evaluate"
            return update

        orders_by_id = {
            str(d.get("order_id")): d for d in skynet.get("orders") or [] if isinstance(d, dict)
        }
        failed = [
            orders_by_id[r.order_id]
            for r in results
            if not r.success and r.order_id in orders_by_id
        ]
        deferred: list[dict[str, Any]] = list(skynet.get("deferred") or [])
        unmet_next = failed + deferred
        replan = (not complete) and round_ < settings.skynet_max_rounds and bool(unmet_next)

        updates: dict[str, Any] = {
            "answer": answer or skynet.get("answer", ""),
            "complete": complete,
            "replan": replan,
        }
        if replan:
            updates["round"] = round_ + 1
            updates["unmet"] = unmet_next
            updates["deferred"] = []
        else:
            updates["rounds_completed"] = round_

        logger.info(
            "skynet_evaluated",
            complete=complete,
            replan=replan,
            round=round_,
            unmet=len(unmet_next),
        )
        update = merge_skynet(state, **updates)
        update["current_agent"] = "skynet_evaluate"
        return update

    return evaluate_node


def route_after_evaluate(state: dict[str, Any]) -> str:
    """Conditional edge: back to plan while a re-plan is scheduled, else output."""
    return "skynet_plan" if get_skynet(state).get("replan") else "skynet_output"


__all__ = ["make_evaluate_node", "route_after_evaluate"]
