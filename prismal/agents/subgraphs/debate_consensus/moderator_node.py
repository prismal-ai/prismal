"""Moderator (neutral) node for debate_consensus subgraph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prismal.agents.subgraphs.debate_consensus._helpers import make_role_node

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_PROMPT = (
    "You are the MODERATOR in a structured debate. You have seen both the "
    "proponent's and the opponent's stances. Produce a concise neutral "
    "analysis (2-4 sentences) that names the strongest point from each side "
    "and surfaces any shared ground. Do not pick a winner — your role is "
    "mediation, not judgement."
)


def make_moderator_node(
    llm: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Return an async LangGraph node producing the moderator position."""
    return make_role_node(
        llm=llm,
        role="moderator",
        system_prompt=_PROMPT,
        agent_index=2,
    )


__all__ = ["make_moderator_node"]
