"""Opponent node for debate_consensus subgraph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.debate_consensus._helpers import make_role_node

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_PROMPT = (
    "You are the OPPONENT in a structured debate. You have seen the "
    "proponent's stance. Produce a concise counter-stance (2-4 sentences) "
    "that presses the strongest disagreement. Concede valid points if they "
    "strengthen your overall critique."
)


def make_opponent_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node producing the opponent position."""
    return make_role_node(
        llm=llm,
        role="opponent",
        system_prompt=_PROMPT,
        agent_index=1,
    )


__all__ = ["make_opponent_node"]
