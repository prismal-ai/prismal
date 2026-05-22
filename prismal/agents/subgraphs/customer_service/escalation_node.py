"""Escalation gate for customer_service subgraph.

A conditional-edge function (not a node). Routes to ``ticket_creator`` when
the query looks like a complaint or when retrieval confidence is below the
threshold; otherwise routes to ``response_generator``.

Defensive default: if ``metadata.customer_service`` is missing altogether we
escalate (the safer choice — better to open a ticket than hallucinate an
answer with no context).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("prismal.subgraphs.customer_service.escalation")


def make_escalation_gate(threshold: float = 0.6) -> Callable[[dict[str, Any]], str]:
    """Return a conditional-edge function: state → next node name."""

    def gate(state: dict[str, Any]) -> str:
        cs = (state.get("metadata") or {}).get("customer_service") or {}
        category = cs.get("category")
        confidence = cs.get("confidence")
        if category is None or confidence is None:
            logger.info(
                "customer_service_escalate",
                reason="missing_metadata",
            )
            return "ticket_creator"
        if category == "complaint":
            logger.info(
                "customer_service_escalate",
                reason="complaint",
                category=category,
            )
            return "ticket_creator"
        if float(confidence) < threshold:
            logger.info(
                "customer_service_escalate",
                reason="low_confidence",
                confidence=confidence,
                threshold=threshold,
            )
            return "ticket_creator"
        logger.info(
            "customer_service_respond",
            category=category,
            confidence=confidence,
        )
        return "response_generator"

    return gate


__all__ = ["make_escalation_gate"]
