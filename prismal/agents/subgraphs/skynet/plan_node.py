"""plan node — decompose the goal into the round's swarm orders (SPEC-SKY-SG-001).

Calls :meth:`SkynetSupervisor.plan` and stages the capped orders both under
``metadata.skynet.orders`` (the durable view, S4-06) and in the top-level
``skynet_orders`` channel the dispatcher fans out from.  On a planning failure
(or an empty goal) it stages an empty order list so the dispatcher routes to
the output node, which reports the error — the graph always terminates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from prismal.agents.subgraphs.skynet._helpers import (
    dicts_to_orders,
    get_skynet,
    merge_skynet,
    orders_to_dicts,
)
from prismal.core.exceptions import SkynetError
from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prismal.agents.skynet.supervisor import SkynetSupervisor

logger = get_logger("prismal.subgraphs.skynet.plan")


def make_plan_node(
    supervisor: SkynetSupervisor,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build the plan node around an injected :class:`SkynetSupervisor`."""

    async def plan_node(state: dict[str, Any]) -> dict[str, Any]:
        """Decompose the goal (or re-plan unmet orders) into ``skynet_orders``."""
        skynet = get_skynet(state)
        goal = str(skynet.get("goal") or "").strip() or _extract_goal(state)
        if not goal:
            logger.warning("skynet_plan_empty_goal")
            update = merge_skynet(state, error="empty goal")
            update["skynet_orders"] = []
            return update

        round_ = int(skynet.get("round") or 1)
        unmet_dicts: list[dict[str, Any]] = list(skynet.get("unmet") or [])
        unmet = dicts_to_orders(unmet_dicts) or None

        try:
            plan = await supervisor.plan(goal, round=round_, unmet=unmet)
        except SkynetError as exc:
            logger.warning("skynet_plan_node_error", error=str(exc), round=round_)
            update = merge_skynet(state, error=str(exc), goal=goal, round=round_)
            update["skynet_orders"] = []
            return update

        order_dicts = orders_to_dicts(plan.orders)
        update = merge_skynet(
            state,
            goal=goal,
            round=round_,
            rationale=plan.rationale,
            orders=order_dicts,
            deferred=orders_to_dicts(plan.deferred),
            unmet=[],
        )
        update["skynet_orders"] = order_dicts
        update["current_agent"] = "skynet_plan"
        logger.info(
            "skynet_plan_staged",
            round=round_,
            orders=len(order_dicts),
            deferred=len(plan.deferred),
        )
        return update

    return plan_node


def _extract_goal(state: dict[str, Any]) -> str:
    """Return the latest human message content as the goal."""
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()
    return ""


__all__ = ["make_plan_node"]
