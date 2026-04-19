"""Ticket-creator node for customer_service subgraph.

Assigns a fresh ticket id (``TK-<8 hex>``) and records it in the state's
customer_service metadata. Appends an ``AIMessage`` announcing the ticket
so the end user sees a confirmation in the conversation.

The node does not persist tickets to a real back-end — that integration is
expected to live in a separate service. This node is the in-graph receipt
and handoff signal.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage

from lightagent.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("lightagent.subgraphs.customer_service.ticket_creator")


def make_ticket_creator_node() -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node that creates a support ticket."""

    async def ticket_creator_node(state: dict[str, Any]) -> dict[str, Any]:
        cs = dict((state.get("metadata") or {}).get("customer_service") or {})
        ticket_id = f"TK-{uuid.uuid4().hex[:8].upper()}"

        category = cs.get("category") or "other"
        confidence = cs.get("confidence")
        reason_bits = [f"category={category}"]
        if confidence is not None:
            reason_bits.append(f"confidence={confidence:.2f}")
        reason = "; ".join(reason_bits)

        cs["ticket_id"] = ticket_id
        cs["reason"] = reason

        logger.info(
            "customer_service_ticket_created",
            ticket_id=ticket_id,
            reason=reason,
        )
        message = AIMessage(
            content=(
                f"A support ticket has been opened ({ticket_id}). "
                "A human agent will follow up shortly."
            )
        )
        return {
            "messages": [message],
            "metadata": {
                **(state.get("metadata") or {}),
                "customer_service": cs,
            },
        }

    return ticket_creator_node


__all__ = ["make_ticket_creator_node"]
